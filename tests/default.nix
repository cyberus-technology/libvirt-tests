{
  pkgs,
  libvirt,
  nixos-image,
  chv-ovmf,
  enablePortForwarding ? true,
}:
let
  numaConf = import ./numa-domain-xml.nix {
    inherit nixos-image;
    inherit (pkgs) writeText;
  };
  createTestSuite =
    {
      testScriptFile,
      enablePortForwarding,
      extraControllerConfig ? [ ],
      extraComputeConfig ? [ ],
    }:
    pkgs.callPackage ./libvirt-test.nix {
      inherit
        libvirt
        nixos-image
        chv-ovmf
        testScriptFile
        enablePortForwarding
        extraControllerConfig
        extraComputeConfig
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
      extraComputeConfig = [
        numaConf
        (
          { ... }:
          {
            virtualisation.qemu.options =

              [
                "-smp 4,sockets=2,cores=2,threads=1"
                "-object memory-backend-ram,size=2G,id=m0"
                "-object memory-backend-ram,size=2G,id=m1"
                "-numa node,nodeid=0,cpus=0-1,memdev=m0"
                "-numa node,nodeid=1,cpus=2-3,memdev=m1"
              ];
          }
        )
      ];
      extraControllerConfig = [
        numaConf
        (
          { ... }:
          {
            virtualisation.qemu.options =

              [
                "-smp 4,sockets=4,cores=1,threads=1"
                "-object memory-backend-ram,size=1G,id=m0"
                "-object memory-backend-ram,size=1G,id=m1"
                "-object memory-backend-ram,size=1G,id=m2"
                "-object memory-backend-ram,size=1G,id=m3"
                "-numa node,nodeid=0,cpus=0,memdev=m0"
                "-numa node,nodeid=1,cpus=1,memdev=m1"
                "-numa node,nodeid=2,cpus=2,memdev=m2"
                "-numa node,nodeid=3,cpus=3,memdev=m3"
              ];
          }
        )
      ];
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
