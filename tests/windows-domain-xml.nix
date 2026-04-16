# Returns a NixOS module with a libvirt XML definitions in `/etc` for
# our Windows tests.

{
  pkgs,
}:
{ lib, ... }:
let

  # The image size is currently 20 GiB
  windows_server_raw = pkgs.fetchurl {
    url = "https://nexus.vpn.cyberus-technology.de/repository/vm-test-images/server-2025-root-small-ssh-enabled.raw";
    hash = "sha256-Afc5ectMbmVxch8ivflQ4G27CcpKhCFsLPf5J9I+1KE=";
  };

  virsh_windows_server_xml =
    {
      image ? "/var/lib/libvirt/storage-pools/nfs-share/windows-server.img",
      cpuModel ? "",
    }:
    ''
      <domain type='kvm' id='21050'>
        <name>testvm</name>
        <uuid>4eb6319a-4302-4407-9a56-802fc7e6a422</uuid>
        <memory unit='MiB'>3072</memory>
        <currentMemory unit='MiB'>4096</currentMemory>
        ${lib.optionalString (cpuModel != "") ''
          <cpu mode='custom' match='exact' check='full'>
            <model fallback='forbid'>${cpuModel}</model>
          </cpu>
        ''}
        <vcpu placement='static'>4</vcpu>
        <os>
          <type arch='x86_64'>hvm</type>
          <kernel>/etc/CLOUDHV.fd</kernel>
          <boot dev='hd'/>
        </os>
        <features>
          <hyperv mode='custom'/>
        </features>
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
            <mac address='52:54:00:e5:b8:01'/>
            <target dev='tap1'/>
            <model type='virtio'/>
            <driver queues='1'/>
            <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x0'/>
          </interface>
          <serial type='pty'>
            <target port='0'/>
          </serial>
        </devices>
      </domain>
    '';
in
{
  systemd.tmpfiles.settings = {
    "10-chv" = {
      "/etc/windows-server.img" = {
        "L+" = {
          argument = "${windows_server_raw}";
        };
      };
      "/etc/domain-windows-server.xml" = {
        "C+" = {
          argument = "${pkgs.writeText "domain-windows-server.xml" (virsh_windows_server_xml { })}";
        };
      };
      "/etc/domain-windows-server-skylake-profile.xml" = {
        "C+" = {
          argument = "${pkgs.writeText "domain-windows-server-skylake-profile.xml" (virsh_windows_server_xml {
            cpuModel = "skylake";
          })}";
        };
      };
    };
  };
}
