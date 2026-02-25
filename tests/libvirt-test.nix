# Sets up a NixOS integration test with two VMs running NixOS.
#
# This will run our test suite.

{
  pkgs,
  libvirt, # debug-optimized libvirt package
  nixos-image,
  chv-ovmf,
  testScriptFile,
  enablePortForwarding,
  numaHosts ? false,
}:
let
  common = import ./common.nix { inherit libvirt nixos-image chv-ovmf; };
  numaConf = import ./numa-domain-xml.nix {
    inherit nixos-image;
    inherit (pkgs) writeText;
  };

  tls =
    let
      c = pkgs.callPackage ./certificates.nix { };
    in
    {
      ca = c.tlsCA;
      controller = c.mkHostCert "controllerVM" "192.168.100.1";
      compute = c.mkHostCert "computeVM" "192.168.100.2";
    };
in
pkgs.testers.nixosTest {
  name = "Libvirt test suite for Cloud Hypervisor";

  extraPythonPackages =
    p: with p; [
      pytest
      test-helper
    ];

  nodes.controllerVM =
    { lib, config, ... }:
    {
      imports = [
        common
        ../modules/nfs-host.nix
      ]
      ++ (lib.optional numaHosts numaConf);

      virtualisation = {
        forwardPorts =
          # Port forwarding prevents us from executing the nixos tests in
          # parallel in the CI, as they run in the same context and ports are
          # already occupied then.
          (
            lib.optionals enablePortForwarding [
              {
                from = "host";
                host.port = 2222;
                guest.port = 22;
              }
            ]
          );
      };

      networking.extraHosts = ''
        192.168.100.2 computeVM computeVM.local
      '';

      systemd.network = {
        enable = true;
        wait-online.enable = false;
        networks = {
          eth0 = {
            matchConfig.Name = [ "eth0" ];
            networkConfig.DHCP = "yes";
          };
          eth1 = {
            matchConfig.Name = [ "eth1" ];
            networkConfig = {
              Address = "192.168.100.1/24";
              Gateway = "192.168.100.1";
              DNS = "8.8.8.8";
            };
          };
        };
      };

      systemd.tmpfiles.settings."11-certs" = {
        "/var/lib/libvirt/ch/pki/ca-cert.pem"."C+".argument = "${tls.ca}/ca-cert.pem";
        "/var/lib/libvirt/ch/pki/server-cert.pem"."C+".argument = "${tls.controller}/server-cert.pem";
        "/var/lib/libvirt/ch/pki/server-key.pem"."C+".argument = "${tls.controller}/server-key.pem";
      };

      # We distribute 4G over 6 sockets (NUMA nodes). We use slightly more than
      # 4G so that the number is dividable by 6.
      virtualisation.memorySize = lib.mkForce 4098;

      virtualisation.qemu.options =
        let
          c = config.virtualisation.cores;
        in
        lib.optionals numaHosts [
          # Attention: Keep in sync with vCPU count in common-vm-host.nix!
          "-smp ${toString c},sockets=${toString c},cores=1,threads=1"
        ]
        ++ (lib.genList (
          x:
          "-object memory-backend-ram,size=${
            toString (config.virtualisation.memorySize / c)
          }M,id=m${toString x}"
        ) c)
        ++ (lib.genList (x: "-numa node,nodeid=${toString x},cpus=${toString x},memdev=m${toString x}") c);
    };

  nodes.computeVM =
    { lib, config, ... }:
    {
      imports = [
        common
        ../modules/nfs-client.nix
      ]
      ++ (lib.optional numaHosts numaConf);

      networking.extraHosts = ''
        192.168.100.1 controllerVM controllerVM.local
      '';

      virtualisation = {
        forwardPorts =
          # Port forwarding prevents us from executing the nixos tests in
          # parallel in the CI, as they run in the same context and ports are
          # already occupied then.
          (
            lib.optionals enablePortForwarding [
              {
                from = "host";
                host.port = 3333;
                guest.port = 22;
              }
            ]
          );
      };

      livemig.nfs.host = "192.168.100.1";

      systemd.network = {
        enable = true;
        wait-online.enable = false;
        networks = {
          eth0 = {
            matchConfig.Name = [ "eth0" ];
            networkConfig.DHCP = "yes";
          };
          eth1 = {
            matchConfig.Name = [ "eth1" ];
            networkConfig = {
              Address = "192.168.100.2/24";
              Gateway = "192.168.100.1";
              DNS = "8.8.8.8";
            };
          };
        };
      };

      systemd.tmpfiles.settings."11-certs" = {
        "/var/lib/libvirt/ch/pki/ca-cert.pem"."C+".argument = "${tls.ca}/ca-cert.pem";
        "/var/lib/libvirt/ch/pki/server-cert.pem"."C+".argument = "${tls.compute}/server-cert.pem";
        "/var/lib/libvirt/ch/pki/server-key.pem"."C+".argument = "${tls.compute}/server-key.pem";
      };

      virtualisation.qemu.options = lib.optionals numaHosts [
        # Attention: Keep in sync with vCPU count in common-vm-host.nix!
        "-smp 6,sockets=2,cores=3,threads=1"
        "-object memory-backend-ram,size=2G,id=m0"
        "-object memory-backend-ram,size=2G,id=m1"
        "-numa node,nodeid=0,cpus=0-2,memdev=m0"
        "-numa node,nodeid=1,cpus=3-5,memdev=m1"
      ];
    };

  testScript = { ... }: builtins.readFile testScriptFile;
}
