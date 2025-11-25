# Builds a small NixOS-based bootable image.

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

        services.openssh = {
          enable = true;
          settings = {
            PermitRootLogin = "yes";
            PasswordAuthentication = true;
          };
          openFirewall = true;
        };
        environment.systemPackages = with pkgs; [
          screen
          stress
        ];
        # pw: root
        users.users.root.initialHashedPassword = lib.mkForce "$y$j9T$HiT/m702z/73g4Dt5RzbW0$b3SaYI1FoyT/ORV/qFR/s9zonJBKDn4p2XKyYM2wp1.";
      }
    )
  ];
}
