# Sets up a NixOS integration test with two VMs running NixOS.
#
# This will run our test suite.

{
  pkgs,
  libvirt-src,
  nixos-image,
  chv-ovmf,
  testScriptFile,
}:
let
  common = import ./common.nix { inherit libvirt-src nixos-image chv-ovmf; };

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
      libvirt
    ];

  nodes.controllerVM =
    { ... }:
    {
      imports = [
        common
        ../modules/nfs-host.nix
      ];

      virtualisation = {
        cores = 4;
        memorySize = 4096;
        interfaces.eth1.vlan = 1;
        diskSize = 8192;
        forwardPorts = [
          {
            from = "host";
            host.port = 2222;
            guest.port = 22;
          }
          # The testscript runs in the Host context while we want to connect to
          # the libvirt in the controllerVM
          {
            from = "host";
            host.port = 2223;
            guest.port = 16509;
          }
        ];
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
    };

  nodes.computeVM =
    { ... }:
    {
      imports = [
        common
        ../modules/nfs-client.nix
      ];

      networking.extraHosts = ''
        192.168.100.1 controllerVM controllerVM.local
      '';

      virtualisation = {
        cores = 4;
        memorySize = 4096;
        interfaces.eth1.vlan = 1;
        diskSize = 2048;
        forwardPorts = [
          {
            from = "host";
            host.port = 3333;
            guest.port = 22;
          }
        ];
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
    };

  testScript = { ... }: builtins.readFile testScriptFile;
}
