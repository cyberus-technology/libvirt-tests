{ libvirt-src, nixos-image }:
{ pkgs, ... }:
let
  cirros_qcow = pkgs.fetchurl {
    url = "https://download.cirros-cloud.net/0.6.2/cirros-0.6.2-x86_64-disk.img";
    hash = "sha256-B+RKc+VMlNmIAoUVQDwe12IFXgG4OnZ+3zwrOH94zgA=";
  };

  cirros_raw = pkgs.runCommand "cirros_raw" { } ''
    ${pkgs.qemu-utils}/bin/qemu-img convert -O raw ${cirros_qcow} $out
  '';

  virsh_ch_xml = { image ? "/var/lib/libvirt/storage-pools/nfs-share/nixos.img", numa ? false, hugepages ? false, prefault ? false, serial ? "pty" }:
  ''
    <domain type='kvm' id='21050'>
      <name>testvm</name>
      <uuid>4eb6319a-4302-4407-9a56-802fc7e6a422</uuid>
      <memory unit='KiB'>2097152</memory>
      <currentMemory unit='KiB'>2097152</currentMemory>
      ${if numa then ''
      <vcpu placement='static'>4</vcpu>
      <cputune>
        <vcpupin vcpu='0' cpuset='0-1'/>
        <vcpupin vcpu='1' cpuset='0-1'/>
        <vcpupin vcpu='2' cpuset='2-3'/>
        <vcpupin vcpu='3' cpuset='2-3'/>
        <emulatorpin cpuset='0-1'/>
      </cputune>
      <cpu>
        <topology sockets='2' dies='1' cores='1' threads='2'/>
        <numa>
          <!-- Defines the guest NUMA topology -->
          <cell id='0' cpus='0-1' memory='1024' unit='MiB'/>
          <cell id='1' cpus='2-3,' memory='1024' unit='MiB'/>
        </numa>
      </cpu>
      <numatune>
        <memory mode='strict' nodeset='0'/>
          <!-- Maps memory from guest to host NUMA topology. nodeset refers to host NUMA node, cellid to guest NUMA -->
        <memnode cellid='0' mode='strict' nodeset='0'/>
        <memnode cellid='1' mode='strict' nodeset='0'/>
      </numatune>
      ${if hugepages then ''
      <memoryBacking>
        <hugepages>
          <page size="2" unit="M" nodeset="0"/>
          <page size="2" unit="M" nodeset="1"/>
        </hugepages>
        ${if prefault then ''
        <allocation mode="immediate"/>
        '' else '''' }
      </memoryBacking>
      '' else '''' }
      '' else ''
      <vcpu placement='static'>2</vcpu>
      ${if hugepages then ''
      <memoryBacking>
        <hugepages>
          <page size="2" unit="M"/>
        </hugepages>
        ${if prefault then ''
        <allocation mode="immediate"/>
        '' else '''' }
      </memoryBacking>
      '' else '''' }
      '' }
      <os>
        <type arch='x86_64'>hvm</type>
        <kernel>/etc/CLOUDHV.fd</kernel>
        <boot dev='hd'/>
      </os>
      <clock offset='utc'/>
      <on_poweroff>destroy</on_poweroff>
      <on_reboot>restart</on_reboot>
      <on_crash>destroy</on_crash>
      <devices>
        <emulator>cloud-hypervisor</emulator>
        <disk type='file' device='disk'>
          <source file='${image}'/>
          <target dev='vda' bus='virtio'/>
        </disk>
        <interface type='ethernet'>
          <mac address='52:54:00:e5:b8:ef'/>
          <target dev='vnet0'/>
          <model type='virtio'/>
          <driver queues='1'/>
        </interface>
        ${if serial == "pty" then ''
        <serial type='pty'>
          <source path='/dev/pts/2'/>
          <target port='0'/>
        </serial>
        '' else if serial == "file" then ''
        <serial type='file'>
          <source path='/tmp/vm_serial.log'/>
          <target port='0'/>
        </serial>
        '' else ""}
      </devices>
    </domain>
  '';

  new_interface = ''
    <interface type='ethernet'>
      <mac address='52:54:00:e5:b8:dd'/>
      <target dev='tap0'/>
      <model type='virtio'/>
      <driver queues='1'/>
    </interface>
  '';
in
{
  virtualisation.libvirtd = {
    enable = true;
    sshProxy = false;
    package = pkgs.libvirt.overrideAttrs (old: {
      src = libvirt-src;
      debug = true;
      doInstallCheck = false;
      doCheck = false;
      patches = [
        ../patches/libvirt/0001-meson-patch-in-an-install-prefix-for-building-on-nix.patch
        ../patches/libvirt/0002-substitute-zfs-and-zpool-commands.patch
      ];
      # Reduce files needed to compile. We cut the build-time in half.
      mesonFlags = old.mesonFlags ++ [
        # Disabling tests: 1500 -> 1200
        "-Dtests=disabled"
        "-Dexpensive_tests=disabled"
        # Disabling docs: 1200 -> 800
        "-Ddocs=disabled"
        # Disabling unneeded backends: 800 -> 685
        "-Ddriver_ch=enabled"
        "-Ddriver_qemu=enabled"
        "-Ddriver_bhyve=disabled"
        "-Ddriver_esx=disabled"
        "-Ddriver_hyperv=disabled"
        "-Ddriver_libxl=disabled"
        "-Ddriver_lxc=disabled"
        "-Ddriver_openvz=disabled"
        "-Ddriver_secrets=disabled"
        "-Ddriver_vbox=disabled"
        "-Ddriver_vmware=disabled"
        "-Ddriver_vz=disabled"
      ];
    });
  };

  systemd.services.virtstoraged.path = [ pkgs.mount ];

  systemd.services.virtchd.wantedBy = [ "multi-user.target" ];
  systemd.services.virtchd.path = [ pkgs.openssh ];
  systemd.sockets.virtproxyd-tcp.wantedBy = [ "sockets.target" ];
  systemd.sockets.virtstoraged.wantedBy = [ "sockets.target" ];

  systemd.network = {
    enable = true;
    wait-online.enable = false;

    netdevs = {
      "10-br0" = {
        netdevConfig = {
          Kind = "bridge";
          Name = "br0";
        };
      };
    };

    networks = {
      # Bridge interface configuration
      "10-br0" = {
        enable = true;
        matchConfig.Name = "br0";
        networkConfig = {
          Description = "Main Bridge";
          DHCPServer = "yes";
        };

        dhcpServerStaticLeases = [
          {
            Address = "192.168.1.2";
            MACAddress = "52:54:00:e5:b8:ef";
          }
          {
            Address = "192.168.1.3";
            MACAddress = "52:54:00:e5:b8:ee";
          }
        ];

        # DHCP server settings
        dhcpServerConfig = {
          PoolOffset = 2;
          PoolSize = 10;
          EmitDNS = false;
          EmitRouter = false;
        };

        # Static IP configuration for the bridge itself
        address = [
          "192.168.1.1/24"
        ];
      };
      "10-vnet0" = {
        matchConfig.Name = "vnet*";
        networkConfig.Bridge = "br0";
      };
    };
  };

  networking = {
    useDHCP = false;
    networkmanager.enable = false;
    useNetworkd = true;
    firewall.enable = false;
  };

  services.getty.autologinUser = "root";
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "yes";
      PermitEmptyPasswords = "yes";
    };
  };

  # The following works around the infamous
  # `Bad owner or permissions on /nix/store/ymmaa926pv3f3wlgpw9y1aygdvqi1m7j-systemd-257.6/lib/systemd/ssh_config.d/20-systemd-ssh-proxy.conf`
  # error. The current assumption is, that this is a nixos/nixpkgs bug handling
  # file permissions incorrectly. But the error is only appearing on certain
  # systems (AMD only?).
  environment.etc."ssh/ssh_config".enable = false;

  environment.variables = {
    LIBVIRT_DEFAULT_URI = "ch:///session";
  };

  security.pam.services.sshd.allowNullPassword = true;

  environment.systemPackages = [
    pkgs.cloud-hypervisor
    pkgs.qemu_kvm
    pkgs.bridge-utils
    pkgs.screen
    pkgs.jq
    pkgs.sshpass
    pkgs.mount
    pkgs.gdb
    pkgs.screen
    pkgs.tunctl
    pkgs.lsof
    pkgs.python3
    pkgs.numatop
    pkgs.numactl
    pkgs.htop
  ];

  systemd.tmpfiles.settings =
    let
      chv-firmware = pkgs.fetchurl {
        url = "https://github.com/cloud-hypervisor/rust-hypervisor-firmware/releases/download/0.5.0/hypervisor-fw";
        hash = "sha256-Sgoel3No9rFdIZiiFr3t+aNQv15a4H4p5pU3PsFq2Vg=";
      };
      chv-ovmf = pkgs.OVMF-cloud-hypervisor.fd;
    in
    {
      "10-chv" = {
        "/etc/hypervisor-fw" = {
          "L+" = {
            argument = "${chv-firmware}";
          };
        };
        "/etc/CLOUDHV.fd" = {
          "C+" = {
            argument = "${chv-ovmf}/FV/CLOUDHV.fd";
          };
        };
        "/etc/nixos.img" = {
          "L+" = {
            argument = "${nixos-image}";
          };
        };
        "/etc/cirros.img" = {
          "L+" = {
            argument = "${cirros_raw}";
          };
        };
        "/etc/domain-chv.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "domain.xml" (virsh_ch_xml {})}";
          };
        };
        "/etc/domain-chv-serial-file.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "domain.xml" (virsh_ch_xml { serial = "file"; })}";
          };
        };
        "/etc/domain-chv-cirros.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "domain-cirros.xml" (virsh_ch_xml { image = "/var/lib/libvirt/storage-pools/nfs-share/cirros.img"; })}";
          };
        };
        "/etc/domain-chv-hugepages.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "cirros.xml" (virsh_ch_xml { hugepages = true; })}";
          };
        };
        "/etc/domain-chv-hugepages-prefault.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "cirros.xml" (virsh_ch_xml { hugepages = true; prefault = true; })}";
          };
        };
        "/etc/domain-chv-numa.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "domain-numa.xml" (virsh_ch_xml { numa = true; })}";
          };
        };
        "/etc/domain-chv-numa-hugepages.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "cirros-numa.xml" (virsh_ch_xml { numa = true; hugepages = true; })}";
          };
        };
        "/etc/domain-chv-numa-hugepages-prefault.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "cirros-numa.xml" (virsh_ch_xml { numa = true; hugepages = true; prefault = true; })}";
          };
        };
        "/etc/new_interface.xml" = {
          "C+" = {
            argument = "${pkgs.writeText "new_interface.xml" new_interface}";
          };
        };
        "/var/log/libvirt/" = {
          D = {
            mode = "0755";
            user = "root";
          };
        };
        "/var/log/libvirt/ch" = {
          D = {
            mode = "0755";
            user = "root";
          };
        };
      };
    };
}
