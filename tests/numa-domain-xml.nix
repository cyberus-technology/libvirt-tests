# Returns a NixOS module with a libvirt XML definitions in `/etc` for our NUMA tests.
#
# This will run our test suite.

{
  writeText,
  nixos-image,
}:
let
  # XML used to migrate between host that have a different Numa configuration
  virsh-numahosts-xml =
    {
      image ? "/var/lib/libvirt/storage-pools/nfs-share/nixos.img",
      # Indicates if we create a config that is used to start the CHV VM
      # on the controllerVM
      is_controller_vm ? true,
    }:
    ''
      <domain type='kvm' id='21050'>
        <name>testvm</name>
        <uuid>4eb6319a-4302-4407-9a56-802fc7e6a422</uuid>
        <memory unit='MiB'>512</memory>
        <currentMemory unit='MiB'>512</currentMemory>
        <!-- Keep in sync with QEMU VM vCPUs. -->
        <vcpu placement='static'>2</vcpu>
        <cputune>
        ${
          # Note the difference in the cpuset
          if is_controller_vm then
            ''
              <vcpupin vcpu='0' cpuset='0'/>
              <vcpupin vcpu='1' cpuset='1'/>
              <emulatorpin cpuset='0-1'/>
            ''
          else
            ''
              <vcpupin vcpu='0' cpuset='2'/>
              <vcpupin vcpu='1' cpuset='3'/>
              <emulatorpin cpuset='0-1'/>
            ''
        }
        </cputune>
        <cpu>
          <topology sockets='2' dies='1' cores='1' threads='1'/>
          <numa>
            <!-- Defines the guest NUMA topology -->
            <cell id='0' cpus='0-1' memory='512' unit='MiB'/>
          </numa>
        </cpu>
        <numatune>
        ${
          if is_controller_vm then
            ''
              <memory mode='strict' nodeset='3'/>
                <!-- Maps memory from guest to host NUMA topology. nodeset refers to host NUMA node, cellid to guest NUMA -->
              <memnode cellid='0' mode='strict' nodeset='3'/>
            ''
          else
            ''
              <memory mode='strict' nodeset='1'/>
                <!-- Maps memory from guest to host NUMA topology. nodeset refers to host NUMA node, cellid to guest NUMA -->
              <memnode cellid='0' mode='strict' nodeset='1'/>
            ''
        }
        </numatune>
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
            <mac address='52:54:00:e5:b8:01'/>
            <target dev='tap1'/>
            <model type='virtio'/>
            <driver queues='1'/>
            <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x0'/>
          </interface>
          <serial type='pty'>
            <source path='/dev/pts/2'/>
            <target port='0'/>
          </serial>
        </devices>
      </domain>
    '';
in
{
  systemd.tmpfiles.settings = {
    "10-chv" = {
      "/etc/domain-numa-init.xml" = {
        "C+" = {
          argument = "${writeText "domain-numa-init.xml" (virsh-numahosts-xml {
            is_controller_vm = true;
          })}";
        };
      };

      "/etc/domain-numa-update.xml" = {
        "C+" = {
          argument = "${writeText "domain-numa-init.xml" (virsh-numahosts-xml {
            is_controller_vm = false;
          })}";
        };
      };
    };
  };
}
