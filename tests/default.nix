{
  pkgs,
  libvirt,
  nixos-image,
  chv-ovmf,
  enablePortForwarding ? true,
}:
let
  createTestSuite =
    {
      testScriptFile,
      enablePortForwarding,
      numaHosts ? false,
    }:
    pkgs.callPackage ./libvirt-test.nix {
      inherit
        libvirt
        nixos-image
        chv-ovmf
        testScriptFile
        enablePortForwarding
        numaHosts
        ;
    };
  # Function to add a passthru attribute to a nixos test derivation that
  # disables port forwarding. The non port forwarding version will mainly be
  # used in the CI.
  addNoPortForwardingAttr =
    _: drv:
    drv
    // {
      passthru.no_port_forwarding = drv.override { enablePortForwarding = false; };
    };

  tests = {
    default = createTestSuite {
      inherit enablePortForwarding;
      testScriptFile = ./testsuite_default.py;
    };

    live_migration = createTestSuite {
      inherit enablePortForwarding;
      testScriptFile = ././testsuite_migration.py;
    };

    hugepage = createTestSuite {
      inherit enablePortForwarding;
      testScriptFile = ./testsuite_hugepages.py;
    };

    long_migration_with_load = createTestSuite {
      inherit enablePortForwarding;
      testScriptFile = ./testsuite_long_migration_with_load.py;
    };

    numa_hosts = createTestSuite {
      inherit enablePortForwarding;
      testScriptFile = ./testsuite_numa.py;
      numaHosts = true;
    };
  };

  # Convenience attribute containing all nixos test driver attributes mainly
  # used for evaluation checks
  all = pkgs.symlinkJoin {
    name = "all-test-driver";
    paths = pkgs.lib.pipe tests [
      builtins.attrValues
      (map (t: t.driver))
    ];
  };
in
(builtins.mapAttrs addNoPortForwardingAttr tests) // { inherit all; }
