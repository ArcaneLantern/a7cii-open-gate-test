# Sony A7C II open gate: the Nix/NixOS shell for the linux launchers.
#
# python3 and libusb as nixpkgs provides them, with the library path exported: NixOS has no
# ldconfig cache, so Python's ctypes (and pyusb through it) cannot find libusb without the
# path, and nothing of the kit can talk USB. Enter it by hand with:
#
#   nix-shell linux/shell.nix
#
# linux/setup.sh, run.sh and the four launchers put themselves inside this shell when they
# find /nix/store and nix-shell (IN_NIX_SHELL stops a second entry), so nothing needs
# entering by hand on NixOS; on other distributions nothing changes. The pinned Python
# packages stay in the venv that setup.sh builds, as everywhere else.
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  name = "a7cii-open-gate";
  packages = [ pkgs.python3 pkgs.libusb1 ];
  shellHook = ''
    export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.libusb1 ]}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  '';
}