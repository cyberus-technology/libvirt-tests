import time
import unittest

# Following is required to allow proper linting of the python code in IDEs.
# Because certain functions like start_all() and certain objects like computeVM
# or other machines are added by Nix, we need to provide certain stub objects
# in order to allow the IDE to lint the python code successfully.
if "start_all" not in globals():
    from nixos_test_stubs import start_all, computeVM, controllerVM  # type: ignore


class LibvirtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_all()
        controllerVM.wait_for_unit("multi-user.target")
        controllerVM.succeed("cp /etc/nixos.img /nfs-root/")
        controllerVM.succeed("chmod 0666 /nfs-root/nixos.img")
        controllerVM.succeed("cp /etc/cirros.img /nfs-root/")
        controllerVM.succeed("chmod 0666 /nfs-root/cirros.img")

        controllerVM.succeed(
            'virt-admin -c virtchd:///system daemon-log-outputs "2:journald 1:file:/var/log/libvirt/libvirtd.log"'
        )
        controllerVM.succeed(
            "virt-admin -c virtchd:///system daemon-timeout --timeout 0"
        )

        computeVM.succeed(
            'virt-admin -c virtchd:///system daemon-log-outputs "2:journald 1:file:/var/log/libvirt/libvirtd.log"'
        )
        computeVM.succeed("virt-admin -c virtchd:///system daemon-timeout --timeout 0")

        controllerVM.succeed("mkdir -p /var/lib/libvirt/storage-pools/nfs-share")
        computeVM.succeed("mkdir -p /var/lib/libvirt/storage-pools/nfs-share")

        controllerVM.succeed("ssh -o StrictHostKeyChecking=no computeVM echo")
        computeVM.succeed("ssh -o StrictHostKeyChecking=no controllerVM echo")

        controllerVM.succeed(
            'virsh -c ch:///session pool-define-as --name "nfs-share" --type netfs --source-host "localhost" --source-path "nfs-root" --source-format "nfs" --target "/var/lib/libvirt/storage-pools/nfs-share"'
        )
        controllerVM.succeed("virsh -c ch:///session pool-start nfs-share")

        computeVM.succeed(
            'virsh -c ch:///session pool-define-as --name "nfs-share" --type netfs --source-host "controllerVM" --source-path "nfs-root" --source-format "nfs" --target "/var/lib/libvirt/storage-pools/nfs-share"'
        )
        computeVM.succeed("virsh -c ch:///session pool-start nfs-share")

    def setUp(self):
        print(f"\n\nRunning test: {self._testMethodName}\n\n")

    def tearDown(self):
        # Destroy and undefine all running and persistent domains
        controllerVM.execute(
            'virsh -c ch:///session list --name | while read domain; do [[ -n "$domain" ]] && virsh -c ch:///session destroy "$domain"; done'
        )
        controllerVM.execute(
            'virsh -c ch:///session list --all --name | while read domain; do [[ -n "$domain" ]] && virsh -c ch:///session undefine "$domain"; done'
        )
        computeVM.execute(
            'virsh -c ch:///session list --name | while read domain; do [[ -n "$domain" ]] && virsh -c ch:///session destroy "$domain"; done'
        )
        computeVM.execute(
            'virsh -c ch:///session list --all --name | while read domain; do [[ -n "$domain" ]] && virsh -c ch:///session undefine "$domain"; done'
        )

        # After undefining and destroying all domains, there should not be any .xml files left
        # Any files left here, indicate that we do not clean up properly
        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")
        controllerVM.fail("find /var/lib/libvirt/ch -name *.xml | grep .")
        computeVM.fail("find /run/libvirt/ch -name *.xml | grep .")
        computeVM.fail("find /var/lib/libvirt/ch -name *.xml | grep .")

        # Destroy any remaining huge page allocations.
        controllerVM.succeed("echo 0 > /proc/sys/vm/nr_hugepages")
        computeVM.succeed("echo 0 > /proc/sys/vm/nr_hugepages")

        # Remove any remaining vm logs.
        controllerVM.succeed("rm -f /tmp/*.log")
        computeVM.succeed("rm -f /tmp/*.log")

    def test_network_hotplug_transient_vm_restart(self):
        """
        Test whether we can attach a network device without the --persistent
        parameter, which means the device should disappear if the vm is destroyed
        and later restarted.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a transient network device, i.e. the device should disappear
        # when the VM is destroyed and restarted.
        controllerVM.succeed(
            "virsh -c ch:///session attach-device testvm /etc/new_interface.xml"
        )

        num_net_devices_new = number_of_network_devices(controllerVM)

        assert num_net_devices_new == num_net_devices_old + 1

        controllerVM.succeed(
            "virsh -c ch:///session destroy testvm"
        )

        controllerVM.succeed("virsh -c ch:///session start testvm")
        assert wait_for_ssh(controllerVM)

        assert number_of_network_devices(controllerVM) == num_net_devices_old

    def test_network_hotplug_persistent_vm_restart(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, which means the device should reappear if the vm is destroyed
        and later restarted.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a persistent network device, i.e. the device should re-appear
        # when the VM is destroyed and restarted.
        controllerVM.succeed(
            "virsh -c ch:///session attach-device testvm /etc/new_interface.xml --persistent"
        )

        num_net_devices_new = number_of_network_devices(controllerVM)

        assert num_net_devices_new == num_net_devices_old + 1

        controllerVM.succeed(
            "virsh -c ch:///session destroy testvm"
        )

        controllerVM.succeed("virsh -c ch:///session start testvm")
        assert wait_for_ssh(controllerVM)

        assert number_of_network_devices(controllerVM) == num_net_devices_new

    def test_network_hotplug_persistent_transient_detach_vm_restart(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, and detach it without the parameter. When we then destroy and
        restart the VM, the device should re-appear.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a persistent network device, i.e. the device should re-appear
        # when the VM is destroyed and restarted.
        controllerVM.succeed(
            "virsh -c ch:///session attach-device testvm /etc/new_interface.xml --persistent"
        )

        num_net_devices_new = number_of_network_devices(controllerVM)

        assert num_net_devices_new == num_net_devices_old + 1

        # Transiently detach the device. It should re-appear when the VM is restarted.
        controllerVM.succeed(
            "virsh -c ch:///session detach-device testvm /etc/new_interface.xml"
        )

        assert number_of_network_devices(controllerVM) == num_net_devices_old

        controllerVM.succeed(
            "virsh -c ch:///session destroy testvm"
        )

        controllerVM.succeed("virsh -c ch:///session start testvm")
        assert wait_for_ssh(controllerVM)

        assert number_of_network_devices(controllerVM) == num_net_devices_new


    def test_network_hotplug_attach_detach_transient(self):
        """
        Test whether we can attach a network device without the --persistent
        parameter, and detach it. After detach, the device should disappear from
        the VM.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        num_devices_old = number_of_network_devices(controllerVM)

        controllerVM.succeed(
            "virsh -c ch:///session attach-device testvm /etc/new_interface.xml"
        )

        num_devices_new = number_of_network_devices(controllerVM)

        assert num_devices_new == num_devices_old + 1

        controllerVM.succeed(
            "virsh -c ch:///session detach-device testvm /etc/new_interface.xml"
        )

        assert number_of_network_devices(controllerVM) == num_devices_old

    def test_network_hotplug_attach_detach_persistent(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, and then detach it. After detach, the device should disappear from
        the VM.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        num_devices_old = number_of_network_devices(controllerVM)

        controllerVM.succeed(
            "virsh -c ch:///session attach-device --persistent testvm /etc/new_interface.xml"
        )

        num_devices_new = number_of_network_devices(controllerVM)

        assert num_devices_new == num_devices_old + 1

        controllerVM.succeed(
            "virsh -c ch:///session detach-device --persistent testvm /etc/new_interface.xml"
        )

        assert number_of_network_devices(controllerVM) == num_devices_old

    def test_hotplug(self):
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        num_devices_old = number_of_devices(controllerVM)

        controllerVM.succeed("qemu-img create -f raw /tmp/disk.img 100M")
        controllerVM.succeed(
            "virsh -c ch:///session attach-disk --domain testvm --target vdb --persistent --source /tmp/disk.img"
        )

        controllerVM.succeed(
            "virsh -c ch:///session attach-device --persistent testvm /etc/new_interface.xml"
        )

        num_devices_new = number_of_devices(controllerVM)

        assert num_devices_new == num_devices_old + 2

        controllerVM.succeed(
            "virsh -c ch:///session detach-disk --domain testvm --target vdb"
        )
        controllerVM.succeed(
            "virsh -c ch:///session detach-device testvm /etc/new_interface.xml"
        )

        assert number_of_devices(controllerVM) == num_devices_old

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
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh -c ch:///session shutdown testvm")
        controllerVM.succeed("systemctl restart virtchd")

        controllerVM.succeed("virsh -c ch:///session list --all | grep 'shut off'")

        controllerVM.succeed("virsh -c ch:///session start testvm")
        controllerVM.succeed("systemctl restart virtchd")
        controllerVM.succeed("virsh -c ch:///session list | grep 'running'")

    def test_live_migration_with_hotplug_and_virtchd_restart(self):
        """
        Test that we can restart the libvirt daemon (virtchd) in between live-migrations
        and hotplugging.
        """

        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed(
            "virsh -c ch:///session attach-device testvm /etc/new_interface.xml"
        )

        num_devices_controller = number_of_network_devices(controllerVM)
        assert num_devices_controller == 2

        num_disk_controller = number_of_storage_devices(controllerVM)
        assert num_disk_controller == 1

        controllerVM.succeed(
            "virsh -c ch:///session migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)
        assert num_devices_compute == 2

        controllerVM.succeed("systemctl restart virtchd")
        computeVM.succeed("systemctl restart virtchd")

        computeVM.succeed("virsh -c ch:///session list | grep testvm")
        controllerVM.fail("virsh -c ch:///session list | grep testvm")

        computeVM.succeed(
            "virsh -c ch:///session detach-device testvm /etc/new_interface.xml"
        )

        computeVM.succeed(
            "virsh -c ch:///session attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img"
        )

        num_devices_compute = number_of_network_devices(computeVM)
        assert num_devices_compute == 1

        num_disk_compute = number_of_storage_devices(computeVM)
        assert num_disk_compute == 2

        computeVM.succeed(
            "virsh -c ch:///session migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("systemctl restart virtchd")
        computeVM.succeed("systemctl restart virtchd")

        computeVM.fail("virsh -c ch:///session list | grep testvm")
        controllerVM.succeed("virsh -c ch:///session list | grep testvm")

        controllerVM.succeed(
            "virsh -c ch:///session detach-disk --domain testvm --target vdb"
        )

        num_disk_compute = number_of_storage_devices(controllerVM)
        assert num_disk_compute == 1

    def test_live_migration(self):
        """
        Test the live migration via virsh between 2 hosts. We want to use the
        "--p2p" flag as this is the one used by OpenStack Nova. Using "--p2p"
        results in another control flow of the migration, which is the one we
        want to test.
        We also hot-attach some devices before migrating, in order to cover
        proper migration of those devices.
        """

        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed(
            "virsh -c ch:///session attach-device testvm /etc/new_interface.xml"
        )
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")
        controllerVM.succeed(
            "virsh -c ch:///session attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img"
        )

        for i in range(2):
            # Explicitly use IP in desturi as this was already a problem in the past
            controllerVM.succeed(
                "virsh -c ch:///session migrate --domain testvm --desturi ch+tcp://192.168.100.2/session --persistent --live --p2p"
            )
            time.sleep(5)
            assert wait_for_ssh(computeVM)
            computeVM.succeed(
                "virsh -c ch:///session migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
            )
            time.sleep(5)
            assert wait_for_ssh(controllerVM)

    def test_live_migration_with_hotplug(self):
        """
        Test that transient and persistent devices are correctly handled during live migrations.
        The tests first starts a VM, then attaches a persistent network device. After that, the VM
        is migrated and the new device is detached transiently. Then the VM is destroyed and restarted
        again. The assumption is that the persistent device is still present after the VM has rebooted.
        """

        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed(
            "virsh -c ch:///session attach-device testvm /etc/new_interface.xml --persistent"
        )

        num_devices_controller = number_of_network_devices(controllerVM)

        assert num_devices_controller == 2

        controllerVM.succeed(
            "virsh -c ch:///session migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)

        assert num_devices_controller == num_devices_compute

        computeVM.succeed(
            "virsh -c ch:///session detach-device testvm /etc/new_interface.xml"
        )

        assert number_of_network_devices(computeVM) == 1

        computeVM.succeed(
            "virsh -c ch:///session migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(controllerVM)
        assert number_of_network_devices(controllerVM) == 1

        controllerVM.succeed(
            "virsh -c ch:///session destroy testvm"
        )

        controllerVM.succeed("virsh -c ch:///session start testvm")
        assert wait_for_ssh(controllerVM)

        assert number_of_network_devices(controllerVM) == 2

    def test_live_migration_with_hugepages(self):
        """
        Test that a VM that utilizes hugepages is still using hugepages after live migration.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages));
        computeVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages));

        status, out = controllerVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        status, out = computeVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        status, out = controllerVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) == 0, "not enough huge pages are in-use"

        controllerVM.succeed(
            "virsh -c ch:///session migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(computeVM)

        status, out = computeVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) == 0, "not enough huge pages are in-use"

        status, out = controllerVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) == nr_hugepages, "not all huge pages have been freed"

    def test_live_migration_with_hugepages_failure_case(self):
        """
        Test that migrating a VM with hugepages to a destination without huge pages will fail gracefully.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages));

        status, out = controllerVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.fail(
            "virsh -c ch:///session migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )
        assert wait_for_ssh(controllerVM)

        computeVM.fail("virsh -c ch:///session list | grep testvm")

    def test_numa_topology(self):
        """
        We test that a NUMA topology and NUMA tunings are correctly passed to
        Cloud Hypervisor and the VM.
        """
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-numa.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node0")
        assert status == 0

        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node1")
        assert status == 0

        # Check that there are 2 CPU sockets and 2 threads per core
        status, out = ssh(controllerVM, "lscpu | grep Socket | awk '{print $2}'")
        assert status == 0, "cmd failed"
        assert int(out) == 2, "Expect to find 2 sockets"

        status, out = ssh(controllerVM, "lscpu | grep Thread\( | awk '{print $4}'")
        assert status == 0, "cmd failed"
        assert int(out) == 2, "Expect to find 2 threads per core"

    def test_cirros_image(self):
        """
        The cirros image is often used as the most basic initial image to test
        via openstack or libvirt. We want to make sure it boots flawlessly.
        """
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-cirros.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM, user="cirros", password="gocubsgo")

    def test_hugepages(self):
        """
        Test hugepage on-demand usage for a non-NUMA VM.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages));
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-hugepages.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that we really use hugepages from the hugepage pool
        status, out = controllerVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) < nr_hugepages, "No huge pages have been used"

    def test_hugepages_prefault(self):
        """
        Test hugepage usage with pre-faulting for a non-NUMA VM.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages));
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that all huge pages are in use
        status, out = controllerVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) == 0, "Invalid hugepage usage"

    def test_numa_hugepages(self):
        """
        Test hugepage on-demand usage for a NUMA VM.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages));
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-numa-hugepages.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node0")
        assert status == 0

        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node1")
        assert status == 0

        # Check that we really use hugepages from the hugepage pool
        status, out = controllerVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) < nr_hugepages, "No huge pages have been used"

    def test_numa_hugepages_prefault(self):
        """
        Test hugepage usage with pre-faulting for a NUMA VM.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages));
        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-numa-hugepages-prefault.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node0")
        assert status == 0

        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node1")
        assert status == 0

        # Check that all huge pages are in use
        status, out = controllerVM.execute("cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'")
        assert int(out) == 0, "Invalid huge page usage"

    def test_serial_file_output(self):
        """
        Test that the serial to file configuration works.
        """

        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv-serial-file.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        status, out = controllerVM.execute("cat /tmp/vm_serial.log | wc -l")
        assert int(out) > 50

        status, out = controllerVM.execute("cat /tmp/vm_serial.log | grep 'Welcome to NixOS'")

    def test_managedsave(self):
        """
        Test that the managedsave call results in a state file. Further, we
        ensure the transient xml definition of the domain is deleted correctly
        after the managedsave call, because this was an issue before.
        It is also tested if the restore call is able to restore the domain successfully.
        """

        controllerVM.succeed("virsh -c ch:///session define /etc/domain-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start testvm")

        assert wait_for_ssh(controllerVM)

        # Place some temporary file that would not survive a reboot in order to
        # check that we are indeed restored from the saved state.
        ssh(controllerVM, "touch /tmp/foo")

        controllerVM.succeed("virsh -c ch:///session managedsave testvm")

        controllerVM.succeed("ls /var/lib/libvirt/ch/save/testvm.save/state.json")
        controllerVM.succeed("ls /var/lib/libvirt/ch/save/testvm.save/config.json")
        controllerVM.succeed("ls /var/lib/libvirt/ch/save/testvm.save/memory-ranges")
        controllerVM.succeed("ls /var/lib/libvirt/ch/save/testvm.save/libvirt-save.xml")

        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

        controllerVM.succeed("virsh -c ch:///session restore /var/lib/libvirt/ch/save/testvm.save/")
        controllerVM.succeed("virsh -c ch:///session managedsave-remove testvm")

        assert wait_for_ssh(controllerVM)

        status, _ = ssh(controllerVM, "ls /tmp/foo")
        assert status == 0


def suite():
    suite = unittest.TestSuite()
    suite.addTest(LibvirtTests("test_hotplug"))
    suite.addTest(LibvirtTests("test_libvirt_restart"))
    suite.addTest(LibvirtTests("test_live_migration"))
    suite.addTest(LibvirtTests("test_live_migration_with_hotplug"))
    suite.addTest(LibvirtTests("test_live_migration_with_hugepages"))
    suite.addTest(LibvirtTests("test_live_migration_with_hugepages_failure_case"))
    suite.addTest(LibvirtTests("test_live_migration_with_hotplug_and_virtchd_restart"))
    suite.addTest(LibvirtTests("test_numa_topology"))
    suite.addTest(LibvirtTests("test_hugepages"))
    suite.addTest(LibvirtTests("test_hugepages_prefault"))
    suite.addTest(LibvirtTests("test_numa_hugepages"))
    suite.addTest(LibvirtTests("test_numa_hugepages_prefault"))
    suite.addTest(LibvirtTests("test_network_hotplug_attach_detach_transient"))
    suite.addTest(LibvirtTests("test_network_hotplug_attach_detach_persistent"))
    suite.addTest(LibvirtTests("test_network_hotplug_transient_vm_restart"))
    suite.addTest(LibvirtTests("test_network_hotplug_persistent_vm_restart"))
    suite.addTest(LibvirtTests("test_network_hotplug_persistent_transient_detach_vm_restart"))
    suite.addTest(LibvirtTests("test_serial_file_output"))
    suite.addTest(LibvirtTests("test_managedsave"))
    return suite


def wait_for_ssh(machine, user="root", password="root", ip="192.168.1.2"):
    retries = 100
    for i in range(retries):
        print(f"Wait for ssh {i}/{retries}")
        status, _ = ssh(machine, "echo hello", user, password, ip="192.168.1.2")
        if status == 0:
            return True
        time.sleep(1)
    return False


def ssh(machine, cmd, user="root", password="root", ip="192.168.1.2"):
    status, out = machine.execute(
        f"sshpass -p {password} ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no {user}@{ip} {cmd}"
    )
    return status, out


def number_of_devices(machine):
    status, out = ssh(machine, "lspci | wc -l")
    assert status == 0
    return int(out)

def number_of_network_devices(machine):
    status, out = ssh(machine, "lspci -n | grep 0200 | wc -l")
    assert status == 0
    return int(out)

def number_of_storage_devices(machine):
    status, out = ssh(machine, "lspci -n | grep 0180 | wc -l")
    assert status == 0
    return int(out)


runner = unittest.TextTestRunner()
runner.run(suite())
