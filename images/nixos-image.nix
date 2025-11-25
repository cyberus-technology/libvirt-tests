# Builds a small NixOS-based bootable image.

{
  nixpkgs,
}:

let
  mac = "52:54:00:e5:b8:ef";
in
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
        system.stateVersion = "25.05";

        nix.enable = false;

        boot.kernelParams = [
          "console=ttyS0"
          "earlyprintk=ttyS0"
        ];

        # 6.17 has a broken virtio-net driver. As this image runs in a CHV VM
        # with a virtio-net device for communication with the outer world, we
        # stick to a LTS kernel for now.
        # https://github.com/cloud-hypervisor/cloud-hypervisor/issues/7447
        boot.kernelPackages = lib.mkForce pkgs.linuxPackages_6_12;

        hardware.enableAllHardware = lib.mkForce false;
        hardware.enableRedistributableFirmware = false;

        isoImage.makeUsbBootable = true;
        isoImage.makeEfiBootable = true;
        isoImage.makeBiosBootable = false;

        boot.initrd.availableKernelModules = [
          "virtio_pci"
          "virtio_blk"
        ];
        boot.initrd.kernelModules = [ "virtio_net" ];

        boot.loader.timeout = lib.mkForce 0;
        networking.hostName = "nixos";
        networking.firewall.enable = false;
        networking.useDHCP = false;
        networking.useNetworkd = true;
        networking.interfaces.eth1337.useDHCP = false;
        networking.interfaces.eth1337.ipv4.addresses = [
          {
            address = "192.168.1.2";
            prefixLength = 24;
          }
        ];

        systemd.network.wait-online.ignoredInterfaces = [
          "eth1337"
        ];

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

        # We use a dummy key for the test VM to shortcut the boot time.
        systemd.services.sshd-keygen.enable = false;
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

        environment.systemPackages = with pkgs; [
          screen
          stress
        ];

        services.udev.extraRules = ''
          # Stable NIC name for known test VM MAC
          ACTION=="add", SUBSYSTEM=="net", \
            ATTR{address}=="${mac}", \
            NAME="eth1337"
        '';

        # pw: root
        users.users.root.initialHashedPassword = lib.mkForce "$y$j9T$HiT/m702z/73g4Dt5RzbW0$b3SaYI1FoyT/ORV/qFR/s9zonJBKDn4p2XKyYM2wp1.";
      }
    )
  ];
}
