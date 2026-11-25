"""Offline checks of the Linux takeover: finding the camera and its card in /sys/bus/usb, and
unmounting the card before Linux's usb-storage driver is detached. No device is touched: /sys,
/proc/self/mounts and umount are replaced by a fake tree.

    python -I -B test_linux_takeover.py        (with pyusb installed; linux/.venv has it)
"""
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ct1_common as c
import ct1_stage2_service as service
import usb.core


def build_tree(root, mounted=True):
    """The shape of a Sony camera in Mass Storage mode as Linux shows it in /sys: the device, its
    interface with usb-storage bound, the card as a whole disk (sda) with a partition (sda1), and
    the card's one mount, by label as the desktop mounts it."""
    sys = root / "sys"
    usb = sys / "bus/usb/devices"
    camera = usb / "1-2"
    camera.mkdir(parents=True)
    (camera / "idVendor").write_text("054c\n")
    (camera / "idProduct").write_text("0336\n")
    (camera / "product").write_text("ILCE-7CM2\n")
    interface = usb / "1-2:1.0"
    interface.mkdir()
    (interface / "bInterfaceClass").write_text("08\n")
    (root / "drivers/usb-storage").mkdir(parents=True)
    (interface / "driver").symlink_to(root / "drivers/usb-storage")      # as sysfs links a bound driver
    card = camera / "1-2:1.0/host4/target4:0:0/4:0:0:0/block/sda"         # the card, as the kernel links it
    (card / "sda1").mkdir(parents=True)
    (card / "sda1/partition").write_text("1\n")                           # sda1 is a partition, sda is not
    blocks = sys / "class/block"
    blocks.mkdir(parents=True)
    (blocks / "sda").symlink_to(card)
    (blocks / "sda1").symlink_to(card / "sda1")
    dev = root / "dev"
    (dev / "sda1").parent.mkdir(parents=True)
    (dev / "sda1").write_text("")                                    # resolved by the label symlink below
    (dev / "disk/by-label").mkdir(parents=True)
    (dev / "disk/by-label/CARD").symlink_to("../../sda1")
    card_line = "%s/dev/disk/by-label/CARD /run/media/user/My\\040Card vfat rw,nosuid,nodev 0 0\n" % root
    tmp_line = "tmpfs /run tmpfs rw 0 0\n"
    (root / "mounts").write_text((card_line + tmp_line) if mounted else tmp_line)
    return root


class FakeSession:
    def __init__(self):
        self.events = []

    def log(self, kind, **fields):
        self.events.append(kind)

    def say(self, text):
        pass


class FakeDriver:
    def __init__(self, session, refuse=False):
        self.session = session
        self.refuse = refuse

    def reset(self):
        if self.refuse:
            raise usb.core.USBError("Operation not permitted")
        self.session.events.append("detach")


def fake_run(umount_code=0):
    def run(cmd, **kwargs):
        if cmd[0] == "umount":
            return subprocess.CompletedProcess(cmd, umount_code, stdout="umount: %s" % cmd[1], stderr="")
        raise AssertionError("unexpected command %r" % cmd)
    return run


def fake_sysfs(root):
    return (mock.patch.object(c, "SYSFS_USB", root / "sys/bus/usb/devices"),
            mock.patch.object(c, "SYSFS_BLOCK", root / "sys/class/block"),
            mock.patch.object(c, "MOUNTS", root / "mounts"))


class SysfsListing(unittest.TestCase):
    def rows(self, root):
        with ExitStack() as stack:
            for patch in fake_sysfs(root):
                stack.enter_context(patch)
            return c.sysfs_sony_devices()

    def mounts(self, root):
        with ExitStack() as stack:
            for patch in fake_sysfs(root):
                stack.enter_context(patch)
            return c.sony_mounts()

    def test_lists_the_camera_with_its_card_and_driver(self):
        with TemporaryDirectory() as tmp:
            rows = self.rows(build_tree(Path(tmp)))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["instance_id"], row["class"], row["name"], row["service"], row["status"]),
                         ("USB 054C:0336 at 1-2", "8", "ILCE-7CM2", "usb-storage", None))
        self.assertEqual(row["disks"], ["sda"])

    def test_lists_nothing_when_there_is_no_sony_device(self):
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            devices = tmp_root / "sys/bus/usb/devices"
            (devices / "1-1").mkdir(parents=True)                  # a hub, as a real one is: Genesys Logic
            (devices / "1-1/idVendor").write_text("05e3\n")
            (devices / "1-1/idProduct").write_text("0610\n")
            self.assertEqual(self.rows(tmp_root), [])
        with TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "sys/bus/usb/devices").mkdir(parents=True)    # present and empty
            self.assertEqual(self.rows(tmp_root), [])

    def test_listing_becomes_an_error_when_sysfs_cannot_be_read(self):
        with TemporaryDirectory() as tmp:
            rows = self.rows(Path(tmp))                          # no /sys/bus/usb/devices under this root
        self.assertEqual(len(rows), 1)
        self.assertIn("error", rows[0])

    def test_mount_is_found_by_label_and_unescaped(self):
        with TemporaryDirectory() as tmp:
            root = build_tree(Path(tmp))
            self.assertEqual(self.mounts(root), [("%s/dev/disk/by-label/CARD" % root, "/run/media/user/My Card")])

    def test_no_mount_when_the_card_is_not_mounted(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(self.mounts(build_tree(Path(tmp), mounted=False)), [])


class Takeover(unittest.TestCase):
    def take_over(self, root, euid=0, umount_code=0, refuse=False):
        self.session = FakeSession()
        patches = fake_sysfs(root)
        with ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(c.subprocess, "run", fake_run(umount_code)))
            with mock.patch("os.geteuid", return_value=euid, create=True):     # Windows has no os.geteuid
                service.take_over_from_linux(self.session, FakeDriver(self.session, refuse))
        return self.session.events

    def test_unmounts_before_detaching(self):
        with TemporaryDirectory() as tmp:
            events = self.take_over(build_tree(Path(tmp)))
        self.assertLess(events.index("unmount"), events.index("detach"))

    def test_stops_without_root(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(c.StopTest, "admin rights"):
                self.take_over(build_tree(Path(tmp)), euid=1000)
        self.assertNotIn("detach", self.session.events)

    def test_stops_before_detaching_when_the_camera_is_not_listed(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(c.StopTest, "not found in /sys/bus/usb"):
                self.take_over(Path(tmp))
        self.assertNotIn("detach", self.session.events)

    def test_detaches_without_unmounting_when_no_mount_of_the_card_is_found(self):
        with TemporaryDirectory() as tmp:
            events = self.take_over(build_tree(Path(tmp), mounted=False))
        self.assertNotIn("unmount", events)
        self.assertIn("detach", events)

    def test_stops_before_detaching_when_the_unmount_fails(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(c.StopTest, "could not unmount"):
                self.take_over(build_tree(Path(tmp)), umount_code=1)
        self.assertNotIn("detach", self.session.events)

    def test_stops_when_linux_keeps_its_driver(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(c.StopTest, "did not let go"):
                self.take_over(build_tree(Path(tmp)), refuse=True)


if __name__ == "__main__":
    unittest.main()