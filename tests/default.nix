{
  pkgs,
  libvirt-src,
  nixos-image,
  chv-ovmf,
}:

{
  default = import ./libvirt-test.nix {
    inherit
      pkgs
      libvirt-src
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript.py;
  };

  long_migration_with_load = import ./libvirt-test.nix {
    inherit
      pkgs
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
