{
  pkgs,
  libvirt-src,
  nixos-image,
  testScriptFile,
}:
let
  common = import ./common.nix { inherit libvirt-src nixos-image; };
in
pkgs.nixosTest {
  name = "Libvirt test";

  extraPythonPackages = p: with p; [ pytest libvirt ];

  nodes.controllerVM = { ... }: {
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
        { from = "host"; host.port = 2222; guest.port = 22; }
        { from = "host"; host.port = 2223; guest.port = 16509; }
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
  };

  nodes.computeVM = { ... }: {
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
        { from = "host"; host.port = 3333; guest.port = 22; }
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
  };

  testScript = { ... }: builtins.readFile testScriptFile;
}
