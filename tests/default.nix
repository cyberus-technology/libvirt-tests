{
  pkgs,
  libvirt-chv,
  nixos-image,
  chv-ovmf,
}:

{
  default = pkgs.callPackage ./libvirt-test.nix {
    inherit
      libvirt-chv
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript.py;
  };

  long_migration_with_load = pkgs.callPackage ./libvirt-test.nix {
    inherit
      libvirt-chv
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript_long_migration_with_load.py;
  };
}
