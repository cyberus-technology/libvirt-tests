# Builds a small NixOS-based bootable image (iso).

{
  nixpkgs,
}:

nixpkgs.lib.nixosSystem {
  system = "x86_64-linux";
  modules = [
    (
      {
        config,
        pkgs,
        modulesPath,
        lib,
        ...
      }:
      {
        imports = [
          # The minimal ch installer module has given us the smallest size for
          # a bootable image so far. We would prefer a real disk image instead
          # of an iso, but works nonetheless.
          "${modulesPath}/installer/cd-dvd/installation-cd-minimal-new-kernel-no-zfs.nix"
        ];

        boot.initrd.availableKernelModules = [
          "virtio_blk"
          "virtio_pci"
        ];
        boot.initrd.kernelModules = [ "virtio_net" ];
        boot.initrd.systemd.enable = false;
        boot.kernelParams = [
          "console=ttyS0"
          "earlyprintk=ttyS0"
        ];
        boot.loader.timeout = lib.mkForce 0;

        documentation = {
          enable = false;
          doc.enable = false;
          info.enable = false;
          man.enable = false;
          nixos.enable = false;
        };

        environment.defaultPackages = [ ];
        environment.etc = {
          "ssh/ssh_host_ed25519_key" = {
            mode = "0600";
            source = pkgs.writers.writeText "ssh_host_ed25519_key" ''
              -----BEGIN OPENSSH PRIVATE KEY-----
              b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
              QyNTUxOQAAACCl2D0beTfBGUE+IyEvjfs8bOqoTpwm1PzYWwvUCbFP+AAAAKChrvISoa7y
              EgAAAAtzc2gtZWQyNTUxOQAAACCl2D0beTfBGUE+IyEvjfs8bOqoTpwm1PzYWwvUCbFP+A
              AAAEAcuVo5dChbKfChFIx0bb6WCxZ7l0vSC2F9kgQl0NoCJqXYPRt5N8EZQT4jIS+N+zxs
              6qhOnCbU/NhbC9QJsU/4AAAAG3BzY2h1c3RlckBwaGlwcy1mcmFtZXdvcmsxMwEC
              -----END OPENSSH PRIVATE KEY-----
            '';
          };
          "ssh/ssh_host_ed25519_key.pub" = {
            mode = "0644";
            source = pkgs.writers.writeText "ssh_host_ed25519_key.pub" "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKXYPRt5N8EZQT4jIS+N+zxs6qhOnCbU/NhbC9QJsU/4 test@testvm";
          };
        };
        environment.stub-ld.enable = false;
        environment.systemPackages = with pkgs; [
          screen
          stress
        ];

        isoImage.makeUsbBootable = true;
        isoImage.makeEfiBootable = true;
        isoImage.makeBiosBootable = false;

        hardware.enableAllHardware = lib.mkForce false;
        hardware.enableRedistributableFirmware = false;

        # Please keep in sync with documentation in networks.md!
        networking.firewall.enable = false;
        networking.hostName = "nixos";
        networking.interfaces.eth1337.ipv4.addresses = [
          {
            address = "192.168.1.2";
            prefixLength = 24;
          }
        ];
        networking.interfaces.eth1337.useDHCP = false;
        networking.interfaces.eth1338.ipv4.addresses = [
          {
            address = "192.168.2.2";
            prefixLength = 24;
          }
        ];
        networking.interfaces.eth1338.useDHCP = false;
        networking.interfaces.eth1339.ipv4.addresses = [
          {
            address = "192.168.3.2";
            prefixLength = 24;
          }
        ];
        networking.interfaces.eth1339.useDHCP = false;
        networking.interfaces.eth1340.ipv4.addresses = [
          {
            address = "192.168.4.2";
            prefixLength = 24;
          }
        ];
        networking.interfaces.eth1340.useDHCP = false;

        networking.useDHCP = false;
        networking.useNetworkd = true;

        nix.enable = false;

        programs = {
          command-not-found.enable = false;
          fish.generateCompletions = false;
        };

        services.openssh = {
          enable = true;
          settings = {
            PermitRootLogin = "yes";
            PasswordAuthentication = true;
          };
          openFirewall = true;
          hostKeys = [
            {
              path = "/etc/ssh/ssh_host_ed25519_key";
              type = "ed25519";
            }
          ];
        };

        services.logrotate.enable = false;
        services.resolved.enable = false;
        services.timesyncd.enable = false;
        services.udev.extraRules = ''
          # Stable NIC name for the default VM network.
          ACTION=="add", SUBSYSTEM=="net", \
            ATTR{address}=="52:54:00:e5:b8:01", \
            NAME="eth1337"

          # Stable NIC name for hotplugged network (type 'ethernet').
          ACTION=="add", SUBSYSTEM=="net", \
            ATTR{address}=="52:54:00:e5:b8:02", \
            NAME="eth1338"

          # Stable NIC name for hotplugged network #2 (type 'network').
          ACTION=="add", SUBSYSTEM=="net", \
            ATTR{address}=="52:54:00:e5:b8:03", \
            NAME="eth1339"

          # Stable NIC name for hotplugged network #2 (type 'network').
          ACTION=="add", SUBSYSTEM=="net", \
            ATTR{address}=="52:54:00:e5:b8:04", \
            NAME="eth1340"
        '';
        services.udisks2.enable = false;

        system.stateVersion = "25.05";
        system.switch.enable = false;

        systemd.network.wait-online.ignoredInterfaces = [
          "eth1337"
        ];
        systemd.services.mount-pstore.enable = false;
        systemd.services.resolvconf.enable = false;
        # We use a dummy key for the test VM to shortcut the boot time.
        systemd.services.sshd-keygen.enable = false;

        # pw: root
        users.mutableUsers = false;
        users.users.root.initialHashedPassword = lib.mkForce "$y$j9T$HiT/m702z/73g4Dt5RzbW0$b3SaYI1FoyT/ORV/qFR/s9zonJBKDn4p2XKyYM2wp1.";
      }
    )
  ];
}
