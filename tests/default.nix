{
  pkgs,
  libvirt-src,
  nixos-image,
}:

{
  default = import ./libvirt-test.nix {
    inherit pkgs libvirt-src nixos-image;
    testScriptFile = ./testscript.py;
  };

  long_migration_with_load = import ./libvirt-test.nix {
    inherit pkgs libvirt-src nixos-image;
    testScriptFile = ./testscript_long_migration_with_load.py;
  };
}
