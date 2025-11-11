{
  pkgs,
  libvirt-src,
  nixos-image,
  chv-ovmf,
}:

{
  default = pkgs.callPackage ./libvirt-test.nix {
    inherit
      libvirt-src
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript.py;
  };

  long_migration_with_load = pkgs.callPackage ./libvirt-test.nix {
    inherit
      libvirt-src
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript_long_migration_with_load.py;
  };

  cpu_profiles = import ./libvirt-test.nix {
    inherit
      pkgs
      libvirt-src
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript_cpu_profiles.py;
  };
}
