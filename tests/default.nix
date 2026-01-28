{
  pkgs,
  libvirt-src,
  nixos-image,
  chv-ovmf,
}:

rec {
  default = pkgs.callPackage ./libvirt-test.nix {
    inherit
      libvirt-src
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript.py;
  };

  live_migration = pkgs.callPackage ./libvirt-test.nix {
    inherit
      libvirt-src
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript_migration_tests.py;
  };

  hugepage = pkgs.callPackage ./libvirt-test.nix {
    inherit
      libvirt-src
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript_hugepage_tests.py;
  };

  long_migration_with_load = pkgs.callPackage ./libvirt-test.nix {
    inherit
      libvirt-src
      nixos-image
      chv-ovmf
      ;
    testScriptFile = ./testscript_long_migration_with_load.py;
  };

  # Convenience attribute containing all nixos test driver attributes mainly
  # used for evaluation checks
  all = pkgs.symlinkJoin {
    name = "all-test-driver";
    paths = [
      default.driver
      live_migration.driver
      hugepage.driver
      long_migration_with_load.driver
    ];
  };
}
