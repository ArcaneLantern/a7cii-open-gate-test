# Sony A7C II open gate: the Nix flake of the shell the linux launchers put themselves inside.
#
#   nix develop linux/          the same shell as nix-shell linux/shell.nix brings: python3 and
#                               libusb1 from nixpkgs, with the library path exported (NixOS has
#                               no ldconfig cache, so Python cannot find libusb without it)
#
# setup.sh, run.sh and the four launchers keep using nix-shell linux/shell.nix: it asks for no
# flake support and no input download, and brings the same packages as this flake pins (the
# devShell below is the import of that very shell.nix). The lock pins the nixpkgs the flake
# uses; the nix-shell path uses what <nixpkgs> resolves to on this machine.
{
  description = "Sony A7C II open gate: the Nix shell for the linux launchers (python3, libusb)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (system: {
        default = import ./shell.nix { pkgs = nixpkgs.legacyPackages.${system}; };
      });
    };
}