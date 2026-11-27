/*
 * apsc60_p2a.c — Phase 2A v2 coprocessor RAM writer/restorer for Sony A7C II (ILCE-7CM2) fw 2.01.
 *
 * Path A sensor-only squeeze fix (`apsc60_p2a_v2`, dual-mode `m24 0x08c1` + `m27 0x0bc1`):
 *   Widens BOTH regular Movie APS-C 4K 59.94p (`0x08c1`, m24) and S&Q/alt APS-C 4K 59.94p
 *   (`0x0bc1`, m27) sensor readout to 4608x3072 -> stock 16:9 Lrc output with `27/32` vertical
 *   sampling ratio in both stored records.
 *   Compile with -DP2A_APPLY=1 for apply, -DP2A_APPLY=0 for putback.
 *   Compile with -DHOST_TEST for off-camera host verification harness.
 *
 * SAFETY INVARIANTS (modeled strictly on helper/readcheck.c levels 5/6):
 *   - Writes ONLY /tmp_wbshd/readcheck_result.txt opened O_WRONLY|O_CREAT|O_EXCL (0600).
 *     If that file already exists, returns immediately with ZERO DMA or DMM calls.
 *   - Opens /dev/mem ONLY with O_RDWR|O_SYNC (0x101002) and maps ONLY the single 4096-byte
 *     DMM bounce buffer after verifying 64-byte alignment and [0x400000000, 0x500000000).
 *   - Imports ONLY three functions from libsencore.so:
 *       _Z9dmm_allocjPj, _Z8dmm_freej, sen_dma_transfer
 *   - Contains EXACTLY ONE sen_dma_transfer(1, ...) call inside non-inlined site_write(),
 *     which performs 64-byte-aligned line RMW (reads full line(s), replaces ONLY the listed
 *     site bytes, writes back).
 *   - Pass 1 (strictly read-only): verifies EVERY static guard (18), EVERY dynamic stored_record
 *     guard (5 on 0x08c1 + 5 on 0x0bc1 = 10), the dynamic IMAGER->ctrl->drvparam->IDS/RECS pointer
 *     chain, and EVERY site (25 static + 2 dynamic = 27, must hold FROM or TO) before ANY write.
 *     Any mismatch aborts with ZERO writes.
 *   - Pass 2: writes each FROM site, immediately reads back, verifies target bytes AND every
 *     untouched neighbor byte in the 64-byte line(s). On ANY failure, auto-undo restores
 *     every touched site (in reverse order) back to FROM and verifies read-back.
 *   - Pass 3: re-reads every site (all 27) to confirm all hold TO.
 *   - Always executes munmap, close(/dev/mem), and dmm_free(handle) on every exit path.
 *   - Strictly volatile coprocessor RAM only; zero flash/setting/ioctl/exec/unlink calls.
 */

#ifndef P2A_APPLY
#error "Compile with -DP2A_APPLY=1 (apply) or -DP2A_APPLY=0 (putback)"
#endif

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long u64;
typedef long i64;

#include "expected_sites_apsc60_p2a.h"

#if P2A_APPLY
#define FROM(s)   ((s)->stock)
#define TO(s)     ((s)->changed)
#define MODE_STR  "apply"
#define VERB      "apply"
#else
#define FROM(s)   ((s)->changed)
#define TO(s)     ((s)->stock)
#define MODE_STR  "putback"
#define VERB      "put back"
#endif

#define CP_PAGE       4096U
#define RAM_LO        0x400000000UL
#define RAM_HI        0x500000000UL
#define CP_LO         0x00108000UL
#define CP_HI         0x10000000UL
#define TOTAL_CAP     (256UL * 1024UL)

#define N_TABLE       (sizeof cp_sites / sizeof cp_sites[0])
#define N_SITES       (N_TABLE + N_RECS)
#define N_GUARDS      (sizeof cp_guards / sizeof cp_guards[0])

/* ------------------------------------------------------------------ syscalls & imports */

#ifdef HOST_TEST

extern i64 host_open_result(void);
extern i64 host_open_mem(int flags);
extern i64 host_write(int fd, const void *b, u64 n);
extern i64 host_close(int fd);
extern i64 host_geteuid(void);
extern i64 host_mmap_rw(int fd, u64 len, u64 off);
extern i64 host_munmap(void *p, u64 len);
extern u64 host_dmm_alloc(u32 size, u32 *handle);
extern u64 host_dmm_free(u32 handle);
extern u32 host_dma_transfer(u32 dir, u64 src, u64 dst, u32 size);

#define F_RDWR_SYNC   (2 | 0x101000)
static i64 sys_open_result(void)                  { return host_open_result(); }
static i64 sys_open_mem(int flags)                { return host_open_mem(flags); }
static i64 sys_write(int fd, const void *b, u64 n){ return host_write(fd, b, n); }
static i64 sys_close(int fd)                      { return host_close(fd); }
static i64 sys_geteuid(void)                      { return host_geteuid(); }
static i64 sys_mmap_rw(int fd, u64 len, u64 off)  { return host_mmap_rw(fd, len, off); }
static i64 sys_munmap(void *p, u64 len)           { return host_munmap(p, len); }
static u64 cam_dmm_alloc(u32 size, u32 *handle)   { return host_dmm_alloc(size, handle); }
static u64 cam_dmm_free(u32 handle)               { return host_dmm_free(handle); }
static u32 sen_dma_transfer(u32 dir, u64 src, u64 dst, u32 size) {
    return host_dma_transfer(dir, src, dst, size);
}

#else /* AArch64 Linux camera build */

#define NR_openat     56
#define NR_close      57
#define NR_write      64
#define NR_geteuid    175
#define NR_munmap     215
#define NR_mmap       222

#define AT_FDCWD      (-100)
#define F_NEW         (1 | 0x40 | 0x80)    /* O_WRONLY | O_CREAT | O_EXCL */
#define F_RDWR_SYNC   (2 | 0x101000)       /* O_RDWR | O_SYNC (arm64) */

#define RESULT_PATH   "/tmp_wbshd/readcheck_result.txt"

static i64 sc(i64 n, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f)
{
    register i64 x8 __asm__("x8") = n;
    register i64 x0 __asm__("x0") = a;
    register i64 x1 __asm__("x1") = b;
    register i64 x2 __asm__("x2") = c;
    register i64 x3 __asm__("x3") = d;
    register i64 x4 __asm__("x4") = e;
    register i64 x5 __asm__("x5") = f;
    __asm__ volatile("svc #0"
                     : "+r"(x0)
                     : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5)
                     : "memory", "cc");
    return x0;
}

static i64 sys_open_result(void)
{
    return sc(NR_openat, AT_FDCWD, (i64)RESULT_PATH, F_NEW, 0600, 0, 0);
}
static i64 sys_open_mem(int flags)
{
    return sc(NR_openat, AT_FDCWD, (i64)"/dev/mem", flags, 0, 0, 0);
}
static i64 sys_write(int fd, const void *b, u64 n)
{
    return sc(NR_write, fd, (i64)b, (i64)n, 0, 0, 0);
}
static i64 sys_close(int fd)
{
    return sc(NR_close, fd, 0, 0, 0, 0, 0);
}
static i64 sys_geteuid(void)
{
    return sc(NR_geteuid, 0, 0, 0, 0, 0, 0);
}
static i64 sys_mmap_rw(int fd, u64 len, u64 off)
{
    i64 r = sc(NR_mmap, 0, (i64)len, 3 /* PROT_READ|PROT_WRITE */, 1 /* MAP_SHARED */, fd, (i64)off);
    return (r < 0 && r > -4096) ? -1 : r;
}
static i64 sys_munmap(void *p, u64 len)
{
    return sc(NR_munmap, (i64)p, (i64)len, 0, 0, 0, 0);
}

/* The ONLY three external imports, all from libsencore.so */
extern u64 cam_dmm_alloc(u32 size, u32 *handle) __asm__("_Z9dmm_allocjPj");
extern u64 cam_dmm_free(u32 handle) __asm__("_Z8dmm_freej");
extern u32 sen_dma_transfer(u32 dir, u64 src, u64 dst, u32 size);

#endif /* HOST_TEST */

/* ------------------------------------------------------------------ report output */

static int out_fd = -1;
static u64 out_total = 0;

static void write_raw(const u8 *p, u64 n)
{
    if (out_fd < 0 || out_total >= TOTAL_CAP) return;
    if (n > TOTAL_CAP - out_total) n = TOTAL_CAP - out_total;
    while (n > 0) {
        i64 w = sys_write(out_fd, p, n);
        if (w <= 0) {
            sys_close(out_fd);
            out_fd = -1;
            return;
        }
        p += (u64)w;
        n -= (u64)w;
        out_total += (u64)w;
    }
}

static void puts_(const char *s)
{
    u64 n = 0;
    while (s[n]) n++;
    write_raw((const u8 *)s, n);
}

static void puthex(const u8 *b, u64 n)
{
    static const char hx[] = "0123456789abcdef";
    u8 buf[128];
    u64 i = 0;
    while (i < n) {
        u64 chunk = n - i;
        if (chunk > 64) chunk = 64;
        for (u64 k = 0; k < chunk; k++) {
            buf[2 * k] = (u8)hx[b[i + k] >> 4];
            buf[2 * k + 1] = (u8)hx[b[i + k] & 15];
        }
        write_raw(buf, 2 * chunk);
        i += chunk;
    }
}

static void putnum(i64 v)
{
    u8 buf[24];
    int n = 0;
    u64 u;
    if (v < 0) {
        puts_("-");
        u = (u64)(-(v + 1)) + 1ULL;
    } else {
        u = (u64)v;
    }
    do {
        buf[n++] = (u8)('0' + (u % 10));
        u /= 10;
    } while (u);
    for (int i = 0; i < n / 2; i++) {
        u8 t = buf[i];
        buf[i] = buf[n - 1 - i];
        buf[n - 1 - i] = t;
    }
    write_raw(buf, (u64)n);
}

static void putu32_hex(u32 v)
{
    u8 a[4] = { (u8)(v >> 24), (u8)(v >> 16), (u8)(v >> 8), (u8)v };
    puts_("0x");
    puthex(a, 4);
}

static void putu64_hex(u64 v)
{
    u8 a[8];
    for (int k = 0; k < 8; k++) a[k] = (u8)(v >> (8 * (7 - k)));
    puts_("0x");
    puthex(a, 8);
}

/* ------------------------------------------------------------------ coprocessor site engine */

static u64 p2a_phys;
static u8 *p2a_buf;
static struct cp_site p2a_recs[N_RECS];
static u8 line_snap[CP_PAGE];

static const struct cp_site *site_at(u64 i)
{
    return i < N_TABLE ? &cp_sites[i] : &p2a_recs[i - N_TABLE];
}

static u64 line_base(const struct cp_site *s)
{
    return s->address & ~63UL;
}

static u32 line_len(const struct cp_site *s)
{
    return (u32)(((s->address + s->length + 63UL) & ~63UL) - line_base(s));
}

static __attribute__((noinline)) u32 cp_dma_read(u64 cp_addr, u32 len)
{
    for (u32 i = 0; i < len; i++) p2a_buf[i] = 0xee;
    return sen_dma_transfer(0, cp_addr, p2a_phys, len);
}

static int site_read(const struct cp_site *s)
{
    u64 base = line_base(s);
    u32 n = line_len(s);
    if (s->address < CP_LO || s->address + s->length > CP_HI || n == 0 || n > CP_PAGE) return 1;
    return cp_dma_read(base, n) != 0;
}

/*
 * THE ONLY TRANSFER TOWARDS THE COPROCESSOR (dir == 1) IN THIS ENTIRE FILE.
 * Reads the full 64-byte-aligned line(s) first, replaces ONLY the site's listed bytes,
 * and writes back the exact same 64-byte-aligned line(s).
 */
static __attribute__((noinline)) int site_write(const struct cp_site *s, const u8 *bytes)
{
    u64 base = line_base(s);
    u32 n = line_len(s);
    if (s->address < CP_LO || s->address + s->length > CP_HI || n == 0 || n > CP_PAGE) return 1;
    if (cp_dma_read(base, n) != 0) return 1;
    u8 *at = p2a_buf + (s->address - base);
    if (s->offs) {
        for (u32 i = 0; i < s->noffs; i++) at[s->offs[i]] = bytes[i];
    } else {
        for (u32 i = 0; i < s->length; i++) at[i] = bytes[i];
    }
    return sen_dma_transfer(1, p2a_phys, base, n) != 0;
}

static int site_holds(const struct cp_site *s, const u8 *bytes)
{
    const u8 *at = p2a_buf + (s->address - line_base(s));
    if (s->offs) {
        for (u32 i = 0; i < s->noffs; i++) {
            if (at[s->offs[i]] != bytes[i]) return 0;
        }
    } else {
        for (u32 i = 0; i < s->length; i++) {
            if (at[i] != bytes[i]) return 0;
        }
    }
    return 1;
}

/* Verify that every byte in the 64-byte line(s) NOT belonging to `s`'s listed bytes matches `snap`. */
static int neighbors_unchanged(const struct cp_site *s, const u8 *snap)
{
    u64 base = line_base(s);
    u32 n = line_len(s);
    u32 start = (u32)(s->address - base);
    u32 end = start + s->length;
    for (u32 pos = 0; pos < n; pos++) {
        int is_site_byte = 0;
        if (pos >= start && pos < end) {
            if (!s->offs) {
                is_site_byte = 1;
            } else {
                u32 rel = pos - start;
                for (u32 k = 0; k < s->noffs; k++) {
                    if (s->offs[k] == rel) {
                        is_site_byte = 1;
                        break;
                    }
                }
            }
        }
        if (!is_site_byte && p2a_buf[pos] != snap[pos]) return 0;
    }
    return 1;
}

static const u8 *chain_read(u64 addr, u32 len)
{
    u64 base = addr & ~63UL;
    u64 end = (addr + len + 63UL) & ~63UL;
    if (addr < CP_LO || addr + len > CP_HI || end - base > CP_PAGE) return 0;
    if (cp_dma_read(base, (u32)(end - base)) != 0) return 0;
    return p2a_buf + (addr - base);
}

static u32 le32(const u8 *p)
{
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

static int bad_pointer(u32 v)
{
    return (v & 3U) || v < CP_LO || v >= CP_HI;
}

static int find_records(void)
{
    const u8 *p = chain_read(IMAGER_ADDR, 4);
    if (!p) return 1;
    u32 ctrl = le32(p);
    if (bad_pointer(ctrl) || !(p = chain_read((u64)ctrl + CTRL_DRVPARAM, 4))) return 1;
    u32 obj = le32(p);
    if (bad_pointer(obj) || !(p = chain_read(obj, 0x18))) return 1;
    u32 count = le32(p + 4);
    u32 ids = le32(p + 8);
    u32 base = le32(p + 0x10);
    if (count != EXPECTED_MODES || bad_pointer(ids) || bad_pointer(base)) return 1;
    p = chain_read(ids, count * 4U);
    if (!p) return 1;

    u32 found_idx[N_RECS];
    for (u32 r = 0; r < N_RECS; r++) {
        u32 idx = 0xffU;
        for (u32 i = 0; i < count; i++) {
            if (le32(p + 4U * i) == rec_targets[r].mode_id && idx == 0xffU) idx = i;
        }
        if (idx == 0xffU) return 1;
        for (u32 prev = 0; prev < r; prev++) {
            if (found_idx[prev] == idx) return 1;
        }
        found_idx[r] = idx;
        p2a_recs[r].address = (u64)base + (u64)idx * REC_SIZE;
        p2a_recs[r].length = REC_SIZE;
        p2a_recs[r].stock = rec_targets[r].stock;
        p2a_recs[r].changed = rec_targets[r].changed;
        p2a_recs[r].offs = rec_offs;
        p2a_recs[r].noffs = REC_BYTES;
        if (p2a_recs[r].address + REC_SIZE > CP_HI) return 1;
    }
    return 0;
}

static void say_site(const char *what, const struct cp_site *s)
{
    puts_(what);
    puts_(" ");
    putu32_hex((u32)s->address);
    puts_(" length ");
    putnum((i64)s->length);
    puts_(": ");
}

static void p2a_run(void)
{
    u8 done[N_SITES];
    for (u64 i = 0; i < N_SITES; i++) done[i] = 0;

    u32 handle = 0;
    puts_("== apsc60_p2a: " VERB " the " SITES_TEXT " sites of " DESIGN_LABEL " and the " N_RECS_TEXT " stored records\n");

    p2a_phys = cam_dmm_alloc(CP_PAGE, &handle);
    if (!p2a_phys) {
        puts_("!! buffer allocation failed; nothing is written\n");
        return;
    }
    puts_("buffer physical ");
    putu64_hex(p2a_phys);
    puts_(" handle ");
    putnum((i64)handle);
    puts_("\n");

    if ((p2a_phys & 0x3fUL) || p2a_phys < RAM_LO || p2a_phys + CP_PAGE > RAM_HI) {
        puts_("!! buffer address is not 64-byte aligned inside system RAM; nothing is written\n");
        cam_dmm_free(handle);
        puts_("buffer released\n");
        return;
    }

    i64 fd = sys_open_mem(F_RDWR_SYNC);
    if (fd < 0) {
        puts_("!! /dev/mem open failed ");
        putnum(fd);
        puts_("; nothing is written\n");
        cam_dmm_free(handle);
        puts_("buffer released\n");
        return;
    }

    u64 lead = p2a_phys & 0xfffUL;
    u64 maplen = (lead + CP_PAGE + 0xfffUL) & ~0xfffUL;
    i64 m = sys_mmap_rw((int)fd, maplen, p2a_phys - lead);
    if (m == -1) {
        puts_("!! mmap of the buffer failed; nothing is written\n");
        sys_close((int)fd);
        cam_dmm_free(handle);
        puts_("buffer released\n");
        return;
    }
    p2a_buf = (u8 *)m + lead;

    if (find_records()) {
        puts_("!! the stored records were not found where the pointers lead; nothing is written\n");
        goto out;
    }
    for (u32 r = 0; r < N_RECS; r++) {
        puts_("stored record of mode ");
        puts_(rec_targets[r].mode_text);
        say_site(" at", &p2a_recs[r]);
        puts_(REC_BYTES_TEXT " of its bytes are a site\n");
    }

    /* Pass 1: strictly read-only verification of ALL guards and ALL sites */
    puts_("pass 1: read every guard and site; nothing is written unless all are as expected\n");

    for (u64 i = 0; i < N_GUARDS; i++) {
        const struct cp_guard *g = &cp_guards[i];
        const u8 *p = chain_read(g->address, g->length);
        if (!p) {
            puts_("!! guard read failed at ");
            putu32_hex((u32)g->address);
            puts_("; nothing is written\n");
            goto out;
        }
        for (u32 k = 0; k < g->length; k++) {
            if (p[k] != g->bytes[k]) {
                puts_("!! guard mismatch at ");
                putu32_hex((u32)g->address);
                puts_("; nothing is written\n");
                goto out;
            }
        }
    }
    puts_("guards: all static table guards verified\n");

    for (u32 r = 0; r < N_RECS; r++) {
        if (site_read(&p2a_recs[r])) {
            puts_("!! stored record guard read failed for ");
            puts_(rec_targets[r].mode_text);
            puts_("; nothing is written\n");
            goto out;
        }
        const u8 *rec_at = p2a_buf + (p2a_recs[r].address - line_base(&p2a_recs[r]));
        for (u32 i = 0; i < N_REC_GUARDS_EACH; i++) {
            const struct rec_guard *rg = &rec_targets[r].guards[i];
            for (u32 k = 0; k < rg->length; k++) {
                if (rec_at[rg->off + k] != rg->bytes[k]) {
                    puts_("!! stored record guard mismatch for ");
                    puts_(rec_targets[r].mode_text);
                    puts_(" at +");
                    putu32_hex((u32)rg->off);
                    puts_("; nothing is written\n");
                    goto out;
                }
            }
        }
    }
    puts_("guards: all stored record invariants verified\n");

    u32 todo = 0;
    for (u64 i = 0; i < N_SITES; i++) {
        const struct cp_site *s = site_at(i);
        say_site("site", s);
        if (site_read(s)) {
            puts_("!! read failed; nothing is written\n");
            goto out;
        }
        if (site_holds(s, FROM(s))) {
            puts_("as expected\n");
            todo++;
        } else if (site_holds(s, TO(s))) {
            puts_("already holds the target bytes\n");
        } else {
            puts_("!! holds neither the stock nor the new bytes; nothing is written\n");
            goto out;
        }
    }

    if (!todo) {
        puts_("NOTHING TO DO: every site already holds the target bytes\n");
        goto out;
    }

    /* Pass 2: write each FROM site, read back, verify target AND untouched neighbor bytes */
    puts_("pass 2: write, and read each site back\n");
    int failed = 0;
    for (u64 i = 0; i < N_SITES && !failed; i++) {
        const struct cp_site *s = site_at(i);
        if (site_read(s)) {
            say_site("site", s);
            puts_("!! read failed\n");
            failed = 1;
            break;
        }
        if (site_holds(s, TO(s))) continue;

        u32 n = line_len(s);
        for (u32 k = 0; k < n; k++) line_snap[k] = p2a_buf[k];

        say_site("site", s);
        done[i] = 1;
        if (site_write(s, TO(s))) {
            puts_("!! write failed\n");
            failed = 1;
            break;
        }
        if (site_read(s)) {
            puts_("!! read-back failed\n");
            failed = 1;
            break;
        }
        if (!site_holds(s, TO(s))) {
            puts_("!! not as written on read-back\n");
            failed = 1;
            break;
        }
        if (!neighbors_unchanged(s, line_snap)) {
            puts_("!! neighbor byte changed on read-back\n");
            failed = 1;
            break;
        }
        puts_("written and read back\n");
    }

    /* Pass 3: full verification of every site */
    if (!failed) {
        puts_("pass 3: read every site once more\n");
        for (u64 i = 0; i < N_SITES; i++) {
            const struct cp_site *s = site_at(i);
            if (site_read(s) || !site_holds(s, TO(s))) {
                say_site("site", s);
                puts_("!! does not hold the target bytes\n");
                failed = 1;
            }
        }
    }

    if (!failed) {
        puts_("DONE: all " ALL_TEXT " sites hold the target bytes (" VERB ")\n");
        goto out;
    }

    /* Auto-undo: restore every site touched by this run in reverse order */
    puts_("undo: every site this run touched gets its earlier bytes back\n");
    int left = 0;
    for (i64 i = (i64)N_SITES - 1; i >= 0; i--) {
        const struct cp_site *s = site_at((u64)i);
        if (!done[i]) continue;
        say_site("undo", s);
        if (site_write(s, FROM(s)) || site_read(s) || !site_holds(s, FROM(s))) {
            puts_("!! NOT PUT BACK\n");
            left++;
        } else {
            puts_("put back and read back\n");
        }
    }
    puts_(left
          ? "!! UNDO INCOMPLETE. Cold boot the camera (battery out) before anything else.\n"
          : "NOT DONE: a step failed; every site this run touched was put back\n");

out:
    sys_munmap((void *)m, maplen);
    sys_close((int)fd);
    cam_dmm_free(handle);
    puts_("buffer released\n");
}

static void run_entry(void)
{
    out_fd = -1;
    out_total = 0;
    i64 fd = sys_open_result();
    if (fd < 0) return;
    out_fd = (int)fd;
    puts_("apsc60_p2a 1 mode " MODE_STR "\neuid ");
    putnum(sys_geteuid());
    puts_("\n");
    p2a_run();
    puts_("== done, bytes written before this line: ");
    putnum((i64)out_total);
    puts_("\n");
    if (out_fd >= 0) sys_close(out_fd);
    out_fd = -1;
}

#ifdef HOST_TEST
void p2a_host_run(void) { run_entry(); }
#else
__attribute__((constructor)) static void apsc60_p2a_ctor(void) { run_entry(); }
#endif
