import textwrap
import time
import unittest

# Following import statement allows for proper python IDE support and proper
# nix build support. The duplicate listing of imported functions is a bit
# unfortunate, but it seems to be the best compromise. This way the python IDE
# support works out of the box in VSCode and IntelliJ without requiring
# additional IDE configuration.
try:
    from ..test_helper.test_helper import (  # type: ignore
        LibvirtTestsBase,
        hotplug,
        hotplug_fail,
        number_of_devices,
        number_of_network_devices,
        pci_devices_by_bdf,
        initialControllerVMSetup,
        initialComputeVMSetup,
        ssh,
        wait_for_guest_pci_device_enumeration,
        wait_for_ssh,
        wait_until_succeed,
        parse_devices_from_dom_def,
    )
except Exception:
    from test_helper import (
        LibvirtTestsBase,
        hotplug,
        hotplug_fail,
        number_of_devices,
        number_of_network_devices,
        pci_devices_by_bdf,
        initialControllerVMSetup,
        initialComputeVMSetup,
        ssh,
        wait_for_guest_pci_device_enumeration,
        wait_for_ssh,
        wait_until_succeed,
        parse_devices_from_dom_def,
    )

# pyright: reportPossiblyUnboundVariable=false

# Following is required to allow proper linting of the python code in IDEs.
# Because certain functions like start_all() and certain objects like computeVM
# or other machines are added by Nix, we need to provide certain stub objects
# in order to allow the IDE to lint the python code successfully.
if "start_all" not in globals():
    from ..test_helper.test_helper.nixos_test_stubs import (  # type: ignore
        computeVM,
        controllerVM,
        start_all,
    )

# Paths where we can find the libvirt domain configuration XML files
DOMAIN_DEF_PERSISTENT_PATH = "/var/lib/libvirt/ch/testvm.xml"
DOMAIN_DEF_TRANSIENT_PATH = "/var/run/libvirt/ch/testvm.xml"


class LibvirtTests(LibvirtTestsBase):  # type: ignore
    def __init__(self, methodName):
        super().__init__(methodName, controllerVM, computeVM)

    @classmethod
    def setUpClass(cls):
        start_all()
        initialControllerVMSetup(controllerVM)
        initialComputeVMSetup(computeVM)

    def test_network_hotplug_transient_vm_restart(self):
        """
        Test whether we can attach a network device without the --persistent
        parameter, which means the device should disappear if the vm is destroyed
        and later restarted.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a transient network device, i.e. the device should disappear
        # when the VM is destroyed and restarted.
        hotplug(controllerVM, "virsh attach-device testvm /etc/new_interface.xml")

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)
        self.assertEqual(number_of_network_devices(controllerVM), num_net_devices_old)

    def test_network_hotplug_persistent_vm_restart(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, which means the device should reappear if the vm is destroyed
        and later restarted.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a persistent network device, i.e. the device should re-appear
        # when the VM is destroyed and restarted.
        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface.xml --persistent",
        )

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)
        self.assertEqual(
            number_of_network_devices(controllerVM), num_net_devices_old + 1
        )

    def test_network_hotplug_persistent_transient_detach_vm_restart(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, and detach it without the parameter. When we then destroy and
        restart the VM, the device should re-appear.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a persistent network device, i.e. the device should re-appear
        # when the VM is destroyed and restarted.
        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface.xml --persistent",
        )

        num_net_devices_new = number_of_network_devices(controllerVM)
        self.assertEqual(num_net_devices_new, num_net_devices_old + 1)

        # Transiently detach the device. It should re-appear when the VM is restarted.
        hotplug(controllerVM, "virsh detach-device testvm /etc/new_interface.xml")
        self.assertEqual(number_of_network_devices(controllerVM), num_net_devices_old)

        controllerVM.succeed("virsh destroy testvm")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)
        self.assertEqual(number_of_network_devices(controllerVM), num_net_devices_new)

    def test_network_hotplug_attach_detach_transient(self):
        """
        Test whether we can attach a network device without the --persistent
        parameter, and detach it. After detach, the device should disappear from
        the VM.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        num_devices_old = number_of_network_devices(controllerVM)

        hotplug(controllerVM, "virsh attach-device testvm /etc/new_interface.xml")
        hotplug(controllerVM, "virsh detach-device testvm /etc/new_interface.xml")
        self.assertEqual(number_of_network_devices(controllerVM), num_devices_old)

    def test_network_hotplug_attach_detach_persistent(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, and then detach it. After detach, the device should disappear from
        the VM.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        num_devices_old = number_of_network_devices(controllerVM)

        hotplug(
            controllerVM,
            "virsh attach-device --persistent testvm /etc/new_interface.xml",
        )
        hotplug(
            controllerVM,
            "virsh detach-device --persistent testvm /etc/new_interface.xml",
        )
        self.assertEqual(number_of_network_devices(controllerVM), num_devices_old)

    def test_hotplug(self):
        """
        Tests device hot plugging with multiple devices of different types:
        - attaching a disk (persistent)
        - attaching a network with type 'ethernet' (persistent)
        - attaching a network with type 'network' (transient)
        - attaching a network with type 'bridge' (transient)

        Also connects into the VM via each attached network interface.
        :return:
        """

        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.succeed("qemu-img create -f raw /tmp/disk.img 100M")

        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --persistent --source /tmp/disk.img",
        )
        hotplug(
            controllerVM,
            "virsh attach-device --persistent testvm /etc/new_interface.xml",
        )
        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface_type_network.xml",
        )
        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface_type_bridge.xml",
        )

        # Test attached network interface (type ethernet)
        wait_for_ssh(controllerVM, ip="192.168.2.2")
        # Test attached network interface (type network - managed by libvirt)
        wait_for_ssh(controllerVM, ip="192.168.3.2")
        # Test attached network interface (type bridge)
        wait_for_ssh(controllerVM, ip="192.168.4.2")

        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")
        hotplug(controllerVM, "virsh detach-device testvm /etc/new_interface.xml")
        hotplug(
            controllerVM,
            "virsh detach-device testvm /etc/new_interface_type_network.xml",
        )
        hotplug(
            controllerVM,
            "virsh detach-device testvm /etc/new_interface_type_bridge.xml",
        )

    def test_libvirt_restart(self):
        """
        We test the restart of the libvirt daemon. A restart requires that
        we correctly re-attach to persistent domain, which can currently be
        running or shutdown.
        Previously, shutdown domains were detected as running which led to
        problems when trying to interact with them. Thus, we check the restart
        with both running and shutdown domains.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh shutdown testvm")
        controllerVM.succeed("systemctl restart virtchd")

        controllerVM.succeed("virsh list --all | grep 'shut off'")

        controllerVM.succeed("virsh start testvm")
        controllerVM.succeed("systemctl restart virtchd")
        controllerVM.succeed("virsh list | grep 'running'")

    def test_numa_topology(self):
        """
        We test that a NUMA topology and NUMA tunings are correctly passed to
        Cloud Hypervisor and the VM.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-numa.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        ssh(controllerVM, "ls /sys/devices/system/node/node0")

        ssh(controllerVM, "ls /sys/devices/system/node/node1")

        # Check that there are 2 CPU sockets and 2 threads per core
        out = ssh(controllerVM, "lscpu | grep Socket | awk '{print $2}'")
        self.assertEqual(int(out), 2, "could not find two sockets")

        out = ssh(controllerVM, "lscpu | grep Thread\\( | awk '{print $4}'")
        self.assertEqual(int(out), 2, "could not find two threads per core")

    def test_cirros_image(self):
        """
        The cirros image is often used as the most basic initial image to test
        via openstack or libvirt. We want to make sure it boots flawlessly.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-cirros.xml")
        controllerVM.succeed("virsh start testvm")

        # Attach a network where libvirt performs DHCP as the cirros image has
        # no static IP in it.
        # We can't use our hotplug() helper here, as it's network check would
        # fail at this point.
        controllerVM.succeed(
            "virsh attach-device testvm /etc/new_interface_type_network.xml"
        )
        # The VM boot takes very long (due to DHCP on the default interface
        # which doesn't uses DHCP.
        wait_for_ssh(
            controllerVM,
            user="cirros",
            password="gocubsgo",
            ip="192.168.3.42",
            # The VM boot is very slow as it tries to perform DHCP on all
            # interfaces.
            retries=350,
        )

    def test_serial_file_output(self):
        """
        Test that the serial to file configuration works.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-serial-file.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        status, out = controllerVM.execute("cat /tmp/vm_serial.log | wc -l")
        self.assertGreater(int(out), 50, "no serial log output")

        status, out = controllerVM.execute("grep 'Welcome to NixOS' /tmp/vm_serial.log")

    def test_managedsave(self):
        """
        Test that the managedsave call results in a state file. Further, we
        ensure the transient xml definition of the domain is deleted correctly
        after the managedsave call, because this was an issue before.
        It is also tested if the restore call is able to restore the domain successfully.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Place some temporary file that would not survive a reboot in order to
        # check that we are indeed restored from the saved state.
        ssh(controllerVM, "touch /tmp/foo")

        controllerVM.succeed("virsh managedsave testvm")

        controllerVM.succeed("ls /var/lib/libvirt/ch/save/testvm.save/state.json")
        controllerVM.succeed("ls /var/lib/libvirt/ch/save/testvm.save/config.json")
        controllerVM.succeed("ls /var/lib/libvirt/ch/save/testvm.save/memory-ranges")
        controllerVM.succeed("ls /var/lib/libvirt/ch/save/testvm.save/libvirt-save.xml")

        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

        controllerVM.succeed("virsh restore /var/lib/libvirt/ch/save/testvm.save/")
        controllerVM.succeed("virsh managedsave-remove testvm")

        wait_for_ssh(controllerVM)

        ssh(controllerVM, "ls /tmp/foo")

    def test_shutdown(self):
        """
        Test that transient XMLs are cleaned up correctly when using different
        methods to shutdown the VM:
            * VM shuts down from the inside via "shutdown" command
            * virsh shutdown
            * virsh destroy
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Do some extra magic to not end in a hanging SSH session if the
        # shutdown happens too fast.
        ssh(controllerVM, "\"nohup sh -c 'sleep 5 && shutdown now' >/dev/null 2>&1 &\"")

        def is_shutoff():
            return (
                controllerVM.execute('virsh domstate testvm | grep "shut off"')[0] == 0
            )

        wait_until_succeed(is_shutoff)

        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh shutdown testvm")
        wait_until_succeed(is_shutoff)
        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh destroy testvm")
        wait_until_succeed(is_shutoff)
        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

    def test_libvirt_event_stop_failed(self):
        """
        Test that a Stopped Failed event is emitted in case the Cloud
        Hypervisor process crashes.
        """
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.succeed(
            'screen -dmS events bash -c "virsh event --all --loop testvm 2>&1 | tee /tmp/events.log"'
        )

        # Allow 'virsh event' some time to listen for events
        time.sleep(1)

        # Simulate crash of the VMM process
        controllerVM.succeed("kill -9 $(pidof cloud-hypervisor)")

        def stop_fail_detected():
            status, _ = controllerVM.execute("grep -q 'Stopped Failed' /tmp/events.log")
            return status == 0

        wait_until_succeed(stop_fail_detected)

        # In case we would not detect the crash, Libvirt would still show the
        # domain as running.
        controllerVM.succeed('virsh list --all | grep "shut off"')

        # Check that this case of shutting down a domain also leads to the
        # cleanup of the transient XML correctly.
        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

        # Make sure screen is closed again
        controllerVM.succeed("pkill screen")

    def test_serial_tcp(self):
        """
        Test that the TCP serial mode of Cloud Hypervisor works when defined
        via Libvirt. Further, the test checks that simultaneous logging to file
        works.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-serial-tcp.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that port 2222 is used by cloud hypervisor
        controllerVM.succeed(
            "ss --numeric --processes --listening --tcp src :2222 | grep cloud-hyperviso"
        )

        # Check that we log to file in addition to the TCP socket
        def prompt():
            status, _ = controllerVM.execute(
                "grep -q 'Welcome to NixOS' /var/log/libvirt/ch/testvm.log"
            )
            return status == 0

        wait_until_succeed(prompt)

        controllerVM.succeed(
            textwrap.dedent("""
            cat > /tmp/socat.expect << EOF
            spawn socat - TCP:localhost:2222
            send "\\n\\n"
            expect "$"
            send "pwd\\n"
            expect {
            -exact "/home/nixos" { }
            timeout { puts "timeout hitted!"; exit 1}
            }
            send \\x03
            expect eof
            EOF
        """).strip()
        )

        # The expect script tests interactivity of the serial connection by
        # executing 'pwd' and checking a proper response output
        controllerVM.succeed("expect /tmp/socat.expect")

    def test_virsh_console_works_with_pty(self):
        """
        The test checks that a 'virsh console' command results in an
        interactive console session were we are able to interact with the VM.
        This is done with a PTY configured as a serial backend.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        controllerVM.succeed(
            textwrap.dedent("""
            cat > /tmp/console.expect << EOF
            spawn virsh console testvm
            send "\\n\\n"
            sleep 1
            expect "$"
            send "pwd\\n"
            expect {
                -exact "/home/nixos" { }
                timeout { puts "timeout hitted!"; exit 1}
            }
            send \\x1d
            expect eof
            EOF
        """).strip()
        )

        wait_for_ssh(controllerVM)

        controllerVM.succeed("expect /tmp/console.expect")

    def test_disk_resize_raw(self):
        """
        Test disk resizing for RAW images during VM runtime.

        Here we test that we can grow and shrink a RAW image. Further, we test
        that both size modes (KiB and Byte) are working correctly.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        disk_size_bytes_10M = 1024 * 1024 * 10
        disk_size_bytes_100M = 1024 * 1024 * 100
        disk_size_bytes_200M = 1024 * 1024 * 200

        wait_for_ssh(controllerVM)

        controllerVM.succeed("qemu-img create -f raw /tmp/disk.img 100M")
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --persistent --source /tmp/disk.img",
        )
        disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        self.assertEqual(int(disk_size_guest), disk_size_bytes_100M)
        self.assertEqual(int(disk_size_host), disk_size_bytes_100M)

        # Use full file path instead of virtual device name here because both should work with --path
        controllerVM.succeed(
            f"virsh blockresize --domain testvm --path /tmp/disk.img --size {disk_size_bytes_10M // 1024}"
        )

        disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        self.assertEqual(int(disk_size_guest), disk_size_bytes_10M)
        self.assertEqual(int(disk_size_host), disk_size_bytes_10M)

        # Use virtual device name as --path
        controllerVM.succeed(
            f"virsh blockresize --domain testvm --path vdb --size {disk_size_bytes_200M // 1024}"
        )

        disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        self.assertEqual(int(disk_size_guest), disk_size_bytes_200M)
        self.assertEqual(int(disk_size_host), disk_size_bytes_200M)

        # Use bytes instead of KiB
        controllerVM.succeed(
            f"virsh blockresize --domain testvm --path vdb --size {disk_size_bytes_100M}b"
        )

        disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        self.assertEqual(int(disk_size_guest), disk_size_bytes_100M)
        self.assertEqual(int(disk_size_host), disk_size_bytes_100M)

        # Changing to capacity must fail and not change the disk size because it
        # is not supported for file-based disk images.
        controllerVM.fail("virsh blockresize --domain testvm --path vdb --capacity")

        disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        self.assertEqual(int(disk_size_guest), disk_size_bytes_100M)
        self.assertEqual(int(disk_size_host), disk_size_bytes_100M)

    def test_disk_is_locked(self):
        """
        Test that Cloud Hypervisor indeed locks images using advisory OFD locks.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.succeed("qemu-img create -f raw /tmp/disk.img 100M")

        controllerVM.succeed("fcntl-tool test-lock /tmp/disk.img | grep Unlocked")

        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /tmp/disk.img --mode readonly",
        )

        # Check for shared read lock
        controllerVM.succeed("fcntl-tool test-lock /tmp/disk.img | grep SharedRead")
        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")

        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /tmp/disk.img",
        )
        # Check for exclusive write lock
        controllerVM.succeed("fcntl-tool test-lock /tmp/disk.img | grep ExclusiveWrite")

        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")

    def test_disk_resize_qcow2(self):
        """
        Test disk resizing for qcow2 images during VM runtime.

        We expect that resizing the image fails because CHV does
        not have support for qcow2 resizing yet.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        disk_size_bytes_10M = 1024 * 1024 * 10
        disk_size_bytes_100M = 1024 * 1024 * 100

        wait_for_ssh(controllerVM)

        controllerVM.succeed("qemu-img create -f qcow2 /tmp/disk.img 100M")
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --persistent --source /tmp/disk.img",
        )
        disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )

        self.assertEqual(int(disk_size_guest), disk_size_bytes_100M)

        controllerVM.fail(
            f"virsh blockresize --domain testvm --path vdb --size {disk_size_bytes_10M // 1024}"
        )

    def test_bdfs_implicitly_assigned_same_after_recreate(self):
        """
        Test that BDFs stay consistent after a recreate when hotplugging
        a transient and then a persistent device.

        The persistent config needs to adopt the assigned BDF correctly
        to recreate the same device at the same address after recreate.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Add a persistent network device, i.e. the device should re-appear
        # when the VM is destroyed and recreated.
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdb.img 5M"
        )
        # Attach to implicit BDF 0:04.0, transient
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img",
        )
        # Attach to implicit BDF 0:05.0, persistent
        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface.xml --persistent",
        )
        # The net device was attached persistently, so we expect the device to be there after a recreate, but not the
        # disk. We indeed expect it to be not there anymore and leave a hole in the assigned BDFs
        devices_before = pci_devices_by_bdf(controllerVM)
        del devices_before["00:04.0"]

        # Transiently detach the devices. Net should re-appear when the VM is recreated.

        hotplug(controllerVM, "virsh detach-device testvm /etc/new_interface.xml")
        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        devices_after = pci_devices_by_bdf(controllerVM)
        self.assertEqual(devices_after, devices_before)

    def test_bdf_domain_defs_in_sync_after_transient_unplug(self):
        """
        Test that BDFs that are handed out persistently are not freed by
        transient unplugs.

        The persistent domain definition (XML) needs to adopt the assigned BDF
        correctly and when unplugging a device, the transient domain definition
        has to respect BDFs that are already reserved in the persistent domain
        definition. In other words, we test that BDFs are correctly synced
        between persistent and transient domain definition whenever both are
        affected and that weird hot/-unplugging doesn't make both domain
        definitions go out of sync.

        Developer note: This test assumes that BDFs are handed out with the
        first free numerical smallest BDF first and that freed BDFs can be
        reused. Currently this is enforced by test
        `test_bdf_implicit_assignment`. Without these constraints, this test
        will not be able to detect a conflict. E.g. without BDFs being reused
        and handing out BDFs in a round robin approach, could lead to some kind
        of wrapping, that hands out the correct BDF by accident and doesn't
        provoke the conflict we are checking for.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        # Add a persistent disk. Note: If we would add a network device with <target dev = "vnet*">, then this test
        # would fail. This is because libvirt uses "vnet*" as one of its prefixes for auto-generated names and
        # clears any occurrence of such names from the config.
        devices_persistent = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_PERSISTENT_PATH
        )
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdb.img 5M"
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img --persistent",
        )
        # Check that vdb is added to the same PCI slot in both definitions
        devices_persistent_vdb_added = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_PERSISTENT_PATH
        )
        devices_transient = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_TRANSIENT_PATH
        )
        new_bdf_vdb = list(
            (set(devices_persistent_vdb_added.keys())).difference(
                set(devices_persistent.keys())
            )
        )[0]
        self.assertEqual(
            devices_persistent_vdb_added.get(new_bdf_vdb),
            devices_transient.get(new_bdf_vdb),
        )
        # Remove transient. The device is removed from the transient domain definition but not from the persistent
        # one. The transient domain definition has to mark the BDF as still in use nevertheless.
        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")
        devices_transient = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_TRANSIENT_PATH
        )
        self.assertIsNone(devices_transient.get(new_bdf_vdb))
        # Attach another device persistently. If we did not respect in the transient domain definition that the disk
        # we detached before is still present in persistent domain definition, then we now try to assign
        # `new_bdf_vdb` twice in the persistent domain definition. In other words: Persistent and transient domain
        # definition's BDF management are out of sync if this command fails.
        # Developer note: This assumption only holds as long as we hand out the first free BDF that is numerical the
        # smallest and as long as the algorithm clear BDF for reuse. See the developer node in the documentation
        # string of this test.
        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface.xml --persistent",
        )
        # Find the new devices and their BDFs by comparing to older state
        devices_persistent = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_PERSISTENT_PATH
        )
        bdf_new_devices = list(
            (set(devices_persistent.keys())).difference(
                set(devices_persistent_vdb_added.keys())
            )
        )
        # Ensure the same device can be found with the same BDF in the transient definition
        devices_transient = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_TRANSIENT_PATH
        )
        for bdf in bdf_new_devices:
            self.assertEqual(devices_transient.get(bdf), devices_persistent.get(bdf))

    def test_bdf_domain_defs_in_sync_after_transient_hotplug(self):
        """
        Test that BDFs that are handed out persistently are not freed by
        transient unplugs.

        The persistent config needs to adopt the assigned BDF correctly
        and when unplugging a device, the transient config has to
        respect BDFs that are already reserved in the persistent config.
        In other words, we test that BDFs are correctly synced between
        persistent and transient config whenever both are affected and
        that weird hot/-unplugging doesn't make both configs go out of
        sync.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        # Add a transient disk.
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdb.img 5M"
        )
        devices_transient_before = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_TRANSIENT_PATH
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img",
        )
        # Following the expected semantics, we now have a disk device in the transient definition that is missing in
        # the persistent one. We need to find its BDF in the transient definition and check that there is no match
        # in the persistent one.
        devices_persistent = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_PERSISTENT_PATH
        )
        devices_transient_now = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_TRANSIENT_PATH
        )
        new_bdf_transient = list(
            (set(devices_transient_now.keys())).difference(
                set(devices_transient_before.keys())
            )
        )[0]
        self.assertIsNone(devices_persistent.get(new_bdf_transient))

        # Attach another device persistently. If we did not respect in the persistent definition that the disk we
        # attached before is still present in transient definition, then we now try to assign the BDF of the disk
        # attached transiently before to the new network interface. In other words: Persistent and transient
        # config's BDF management are out of sync. We can see the result by looking into the domain definition...
        hotplug(
            controllerVM,
            "virsh attach-interface --target l33t_n37 --persistent --type network --source libvirt-testnetwork --mac DE:AD:BE:EF:13:37 --model virtio testvm",
        )
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdc.img 5M"
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --persistent --target vdc --source /var/lib/libvirt/storage-pools/nfs-share/vdc.img",
        )

        # So now look into the config
        devices_persistent_end = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_PERSISTENT_PATH
        )
        devices_transient_end = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_TRANSIENT_PATH
        )
        # Find the BDFs of devices we just added
        bdf_new_devices = list(
            (set(devices_transient_end.keys())).difference(
                set(devices_transient_now.keys())
            )
        )
        # And make sure that the exact same devices share the same BDF in transient and persistent definitions
        for bdf in bdf_new_devices:
            self.assertEqual(
                devices_transient_end.get(bdf), devices_persistent_end.get(bdf)
            )

    def test_libvirt_default_net_prefix_triggers_desynchronizing(self):
        """
        Test that using a libvirt reserved name for a net device leads to asynchronism between domain definitions.

        We sync BDFs by finding the net device under its `ifname` property. `ifname` is cleared if the device definition
        uses a prefix that is reserved by libvirt. This test ensure that clearing works and warns us about semantic
        changes in libvirt's parsing infrastructure if it fails.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)
        # We need to know all devices after starting the VM to conclude which one is new later
        devices_before_attach = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_TRANSIENT_PATH
        )
        # Add network interface that uses a libvirt reserved prefix as argument for `target`. We expect it to be
        # cleared which leads to address synchronization failing.
        hotplug(
            controllerVM,
            "virsh attach-interface --target vnet2 --persistent --type network --source libvirt-testnetwork --mac DE:AD:BE:EF:13:37 --model virtio testvm",
        )
        devices_persistent = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_PERSISTENT_PATH
        )
        devices_transient = parse_devices_from_dom_def(
            controllerVM, DOMAIN_DEF_TRANSIENT_PATH
        )
        bdf_in_transient = list(
            (set(devices_transient.keys())).difference(
                set(devices_before_attach.keys())
            )
        )[0]
        # By chance the net device receives the same BDF in both domain definitions, so look for an exact match. If
        # we find one, this means definitions are in sync (because even the `target` attribute is right)
        if devices_persistent[bdf_in_transient] is not None:
            self.assertNotEqual(
                devices_transient.get(bdf_in_transient),
                devices_persistent.get(bdf_in_transient),
            )

    def test_bdf_invalid_device_id(self):
        """
        Test that a BDF with invalid device ID generates an error in libvirt.

        We test the case that a device id higher than 31 is used by a device.
        """
        # Create a VM
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        # We need to check that no devices are added, so let's save how
        # many devices are present in the VM after creating it.
        num_before_expected_failure = number_of_devices(controllerVM)
        # Add a persistent disk.
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdb.img 5M"
        )
        # Now we create a disk that we hotplug to a BDF with a device
        # ID 32. This should fail.
        hotplug_fail(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img --persistent --address pci:0.0.20.0",
        )
        wait_for_guest_pci_device_enumeration(controllerVM, num_before_expected_failure)

    def test_bdf_valid_device_id_with_function_id(self):
        """
        Test that a BDFs containing a function ID leads to errors.

        CHV currently doesn't support multi function devices. So we need
        to checks that libvirt does not allow to attach such devices. We
        check that instantiating a domain with function ID doesn't work.
        Then we test that we cannot hotplug a device with a function ID
        in its BDF definition.
        """
        # We don't support multi function devices currently. The config
        # below defines a device with function ID, so instantiating it
        # should fail.
        controllerVM.fail("virsh define /etc/domain-chv-static-bdf-with-function.xml")

        # Now create a VM from a definition that does not contain any
        # function IDs in it device definition.
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        # We need to check that no devices are added, so let's save how
        # many devices are present in the VM after creating it.
        num_before_expected_failure = number_of_devices(controllerVM)
        # Now we create a disk that we hotplug to a BDF with a function
        # ID. This should fail.
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdb.img 5M"
        )
        hotplug_fail(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img --persistent --address pci:0.0.1f.5",
        )
        # Even though we only land here if the command above failed, we
        # should still ensure that no new devices magically appeared.
        wait_for_guest_pci_device_enumeration(controllerVM, num_before_expected_failure)

    def test_list_cpu_models(self):
        """
        This tests checks that the cpu-models API call is implemented and
        returns at least a skylake and a sapphire-rapids model.
        Further, we check that the domain capabilities API call returns the
        expected CPU profile as usable.
        Both is required to be able to use the specific CPU profile.
        While the 'virsh cpu-models' call only lists the CPU profiles the VMM
        supports, the 'virsh domcapabilities' call takes into account the hosts
        architecture. Thus, the latter reports what CPU profile actually can be
        used in the current environment.
        """
        expected_cpu_models = [
            "skylake",
            "sapphire-rapids",
        ]

        cpu_models_out = controllerVM.succeed("virsh cpu-models x86_64")
        domcapabilities_out = controllerVM.succeed("virsh domcapabilities")

        for model in expected_cpu_models:
            self.assertIn(model, cpu_models_out)
            self.assertIn(
                f"<model usable='yes' vendor='Intel' canonical='{model}'>{model}</model>",
                domcapabilities_out,
            )


def suite():
    # Test cases sorted in alphabetical order.
    testcases = [
        LibvirtTests.test_bdf_domain_defs_in_sync_after_transient_hotplug,
        LibvirtTests.test_bdf_domain_defs_in_sync_after_transient_unplug,
        LibvirtTests.test_bdf_invalid_device_id,
        LibvirtTests.test_bdf_valid_device_id_with_function_id,
        LibvirtTests.test_bdfs_implicitly_assigned_same_after_recreate,
        LibvirtTests.test_cirros_image,
        LibvirtTests.test_disk_is_locked,
        LibvirtTests.test_disk_resize_qcow2,
        LibvirtTests.test_disk_resize_raw,
        LibvirtTests.test_hotplug,
        LibvirtTests.test_libvirt_event_stop_failed,
        LibvirtTests.test_libvirt_restart,
        LibvirtTests.test_libvirt_default_net_prefix_triggers_desynchronizing,
        LibvirtTests.test_list_cpu_models,
        LibvirtTests.test_managedsave,
        LibvirtTests.test_network_hotplug_attach_detach_persistent,
        LibvirtTests.test_network_hotplug_attach_detach_transient,
        LibvirtTests.test_network_hotplug_persistent_transient_detach_vm_restart,
        LibvirtTests.test_network_hotplug_persistent_vm_restart,
        LibvirtTests.test_network_hotplug_transient_vm_restart,
        LibvirtTests.test_numa_topology,
        LibvirtTests.test_serial_file_output,
        LibvirtTests.test_serial_tcp,
        LibvirtTests.test_shutdown,
        LibvirtTests.test_virsh_console_works_with_pty,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
