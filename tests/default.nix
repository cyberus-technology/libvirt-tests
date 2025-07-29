{
  pkgs,
  libvirt-src,
}:
pkgs.nixosTest {
  name = "Libvirt test";

  extraPythonPackages = p: [ p.pytest ];

  nodes.controllerVM =
    { ... }:
    {
      imports = [
        (import ./common.nix { inherit libvirt-src; })
        ../modules/nfs-host.nix
      ];

      virtualisation = {
        cores = 4;
        memorySize = 3072;
        interfaces = {
          eth1 = {
            vlan = 1;
          };
        };
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
            networkConfig = {
              DHCP = "yes";
            };
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

  nodes.computeVM =
    { ... }:
    {
      imports = [
        (import ./common.nix { inherit libvirt-src; })
        ../modules/nfs-client.nix
      ];

      networking.extraHosts = ''
        192.168.100.1 controllerVM controllerVM.local
      '';

      virtualisation = {
        cores = 4;
        memorySize = 3072;
        interfaces = {
          eth1 = {
            vlan = 1;
          };
        };
      };

      livemig.nfs.host = "192.168.100.1";

      systemd.network = {
        enable = true;
        wait-online.enable = false;

        networks = {
          eth0 = {
            matchConfig.Name = [ "eth0" ];
            networkConfig = {
              DHCP = "yes";
            };
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

  testScript = { ... }: builtins.readFile ./testscript.py;
}
