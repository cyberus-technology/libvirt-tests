import libvirt  # type: ignore
import textwrap
import time
import unittest

# Following is required to allow proper linting of the python code in IDEs.
# Because certain functions like start_all() and certain objects like computeVM
# or other machines are added by Nix, we need to provide certain stub objects
# in order to allow the IDE to lint the python code successfully.
if "start_all" not in globals():
    from nixos_test_stubs import start_all, computeVM, controllerVM  # type: ignore


class PrintLogsOnErrorTestCase(unittest.TestCase):
    """
    Custom TestCase class that prints interesting logs in error case.
    """

    def run(self, result=None):
        if result is None:
            result = self.defaultTestResult()

        original_addError = result.addError
        original_addFailure = result.addFailure

        def custom_addError(test, err):
            self.print_logs(f"Error in {test._testMethodName}")
            original_addError(test, err)

        def custom_addFailure(test, err):
            self.print_logs(f"Failure in {test._testMethodName}")
            original_addFailure(test, err)

        result.addError = custom_addError
        result.addFailure = custom_addFailure

        return super().run(result)

    def print_machine_log(self, machine, path):
        status, out = machine.execute(f"cat {path}")
        if status != 0:
            print(f"Could not retrieve logs: {machine.name}:{path}")
            return
        print(f"\nLog {machine.name}:{path}:\n{out}\n")

    def print_logs(self, message):
        print(f"{message}")

        for machine in [controllerVM, computeVM]:
            self.print_machine_log(machine, "/var/log/libvirt/ch/testvm.log")
            self.print_machine_log(machine, "/var/log/libvirt/libvirtd.log")


class LibvirtTests(PrintLogsOnErrorTestCase):
    @classmethod
    def setUpClass(cls):
        start_all()
        controllerVM.wait_for_unit("multi-user.target")
        computeVM.wait_for_unit("multi-user.target")
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
            'virsh pool-define-as --name "nfs-share" --type netfs --source-host "localhost" --source-path "nfs-root" --source-format "nfs" --target "/var/lib/libvirt/storage-pools/nfs-share"'
        )
        controllerVM.succeed("virsh pool-start nfs-share")

        computeVM.succeed(
            'virsh pool-define-as --name "nfs-share" --type netfs --source-host "controllerVM" --source-path "nfs-root" --source-format "nfs" --target "/var/lib/libvirt/storage-pools/nfs-share"'
        )
        computeVM.succeed("virsh pool-start nfs-share")

    def setUp(self):
        print(f"\n\nRunning test: {self._testMethodName}\n\n")

    def tearDown(self):
        # Destroy and undefine all running and persistent domains
        controllerVM.execute(
            'virsh list --name | while read domain; do [[ -n "$domain" ]] && virsh destroy "$domain"; done'
        )
        controllerVM.execute(
            'virsh list --all --name | while read domain; do [[ -n "$domain" ]] && virsh undefine "$domain"; done'
        )
        computeVM.execute(
            'virsh list --name | while read domain; do [[ -n "$domain" ]] && virsh destroy "$domain"; done'
        )
        computeVM.execute(
            'virsh list --all --name | while read domain; do [[ -n "$domain" ]] && virsh undefine "$domain"; done'
        )

        # After undefining and destroying all domains, there should not be any .xml files left
        # Any files left here, indicate that we do not clean up properly
        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")
        controllerVM.fail("find /var/lib/libvirt/ch -name *.xml | grep .")
        computeVM.fail("find /run/libvirt/ch -name *.xml | grep .")
        computeVM.fail("find /var/lib/libvirt/ch -name *.xml | grep .")

        # Ensure we can access specific test case logs afterward.
        commands = [
            f"mv /var/log/libvirt/ch/testvm.log /var/log/libvirt/ch/{self._testMethodName}_vmm.log || true",
            # libvirt bug: can't cope with new or truncated log files
            # f"mv /var/log/libvirt/libvirtd.log /var/log/libvirt/{timestamp}_{self._testMethodName}_libvirtd.log",
            f"mv /var/log/vm_serial.log /var/log/{self._testMethodName}_vm-serial.log || true",
        ]

        # Various cleanup commands to be executed on all machines
        commands = commands + [
            # Destroy any remaining huge page allocations.
            "echo 0 > /proc/sys/vm/nr_hugepages",
            "rm -f /tmp/*.expect",
        ]

        for cmd in commands:
            print(f"cmd: {cmd}")
            controllerVM.succeed(cmd)
            computeVM.succeed(cmd)

    def test_network_hotplug_transient_vm_restart(self):
        """
        Test whether we can attach a network device without the --persistent
        parameter, which means the device should disappear if the vm is destroyed
        and later restarted.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a transient network device, i.e. the device should disappear
        # when the VM is destroyed and restarted.
        controllerVM.succeed("virsh attach-device testvm /etc/new_interface.xml")

        num_net_devices_new = number_of_network_devices(controllerVM)

        assert num_net_devices_new == num_net_devices_old + 1

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        assert wait_for_ssh(controllerVM)

        assert number_of_network_devices(controllerVM) == num_net_devices_old

    def test_network_hotplug_persistent_vm_restart(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, which means the device should reappear if the vm is destroyed
        and later restarted.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a persistent network device, i.e. the device should re-appear
        # when the VM is destroyed and restarted.
        controllerVM.succeed(
            "virsh attach-device testvm /etc/new_interface.xml --persistent"
        )

        num_net_devices_new = number_of_network_devices(controllerVM)

        assert num_net_devices_new == num_net_devices_old + 1

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        assert wait_for_ssh(controllerVM)

        assert number_of_network_devices(controllerVM) == num_net_devices_new

    def test_network_hotplug_persistent_transient_detach_vm_restart(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, and detach it without the parameter. When we then destroy and
        restart the VM, the device should re-appear.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        num_net_devices_old = number_of_network_devices(controllerVM)

        # Add a persistent network device, i.e. the device should re-appear
        # when the VM is destroyed and restarted.
        controllerVM.succeed(
            "virsh attach-device testvm /etc/new_interface.xml --persistent"
        )

        num_net_devices_new = number_of_network_devices(controllerVM)

        assert num_net_devices_new == num_net_devices_old + 1

        # Transiently detach the device. It should re-appear when the VM is restarted.
        controllerVM.succeed("virsh detach-device testvm /etc/new_interface.xml")

        assert number_of_network_devices(controllerVM) == num_net_devices_old

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        assert wait_for_ssh(controllerVM)

        assert number_of_network_devices(controllerVM) == num_net_devices_new

    def test_network_hotplug_attach_detach_transient(self):
        """
        Test whether we can attach a network device without the --persistent
        parameter, and detach it. After detach, the device should disappear from
        the VM.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        num_devices_old = number_of_network_devices(controllerVM)

        controllerVM.succeed("virsh attach-device testvm /etc/new_interface.xml")

        num_devices_new = number_of_network_devices(controllerVM)

        assert num_devices_new == num_devices_old + 1

        controllerVM.succeed("virsh detach-device testvm /etc/new_interface.xml")

        assert number_of_network_devices(controllerVM) == num_devices_old

    def test_network_hotplug_attach_detach_persistent(self):
        """
        Test whether we can attach a network device with the --persistent
        parameter, and then detach it. After detach, the device should disappear from
        the VM.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        num_devices_old = number_of_network_devices(controllerVM)

        controllerVM.succeed(
            "virsh attach-device --persistent testvm /etc/new_interface.xml"
        )

        num_devices_new = number_of_network_devices(controllerVM)

        assert num_devices_new == num_devices_old + 1

        controllerVM.succeed(
            "virsh detach-device --persistent testvm /etc/new_interface.xml"
        )

        assert number_of_network_devices(controllerVM) == num_devices_old

    def test_hotplug(self):
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        num_devices_old = number_of_devices(controllerVM)

        controllerVM.succeed("qemu-img create -f raw /tmp/disk.img 100M")
        controllerVM.succeed(
            "virsh attach-disk --domain testvm --target vdb --persistent --source /tmp/disk.img"
        )

        controllerVM.succeed(
            "virsh attach-device --persistent testvm /etc/new_interface.xml"
        )

        num_devices_new = number_of_devices(controllerVM)

        assert num_devices_new == num_devices_old + 2

        controllerVM.succeed("virsh detach-disk --domain testvm --target vdb")
        controllerVM.succeed("virsh detach-device testvm /etc/new_interface.xml")

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
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh shutdown testvm")
        controllerVM.succeed("systemctl restart virtchd")

        controllerVM.succeed("virsh list --all | grep 'shut off'")

        controllerVM.succeed("virsh start testvm")
        controllerVM.succeed("systemctl restart virtchd")
        controllerVM.succeed("virsh list | grep 'running'")

    def test_live_migration_with_hotplug_and_virtchd_restart(self):
        """
        Test that we can restart the libvirt daemon (virtchd) in between live-migrations
        and hotplugging.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh attach-device testvm /etc/new_interface.xml")

        num_devices_controller = number_of_network_devices(controllerVM)
        assert num_devices_controller == 2

        num_disk_controller = number_of_storage_devices(controllerVM)
        assert num_disk_controller == 1

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)
        assert num_devices_compute == 2

        controllerVM.succeed("systemctl restart virtchd")
        computeVM.succeed("systemctl restart virtchd")

        computeVM.succeed("virsh list | grep testvm")
        controllerVM.fail("virsh list | grep testvm")

        computeVM.succeed("virsh detach-device testvm /etc/new_interface.xml")

        computeVM.succeed(
            "virsh attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img"
        )

        num_devices_compute = number_of_network_devices(computeVM)
        assert num_devices_compute == 1

        num_disk_compute = number_of_storage_devices(computeVM)
        assert num_disk_compute == 2

        computeVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("systemctl restart virtchd")
        computeVM.succeed("systemctl restart virtchd")

        computeVM.fail("virsh list | grep testvm")
        controllerVM.succeed("virsh list | grep testvm")

        controllerVM.succeed("virsh detach-disk --domain testvm --target vdb")

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

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh attach-device testvm /etc/new_interface.xml")
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")
        controllerVM.succeed(
            "virsh attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img"
        )

        for i in range(2):
            # Explicitly use IP in desturi as this was already a problem in the past
            controllerVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://192.168.100.2/session --persistent --live --p2p"
            )
            assert wait_for_ssh(computeVM)
            computeVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
            )
            assert wait_for_ssh(controllerVM)

    def test_live_migration_with_hotplug(self):
        """
        Test that transient and persistent devices are correctly handled during live migrations.
        The tests first starts a VM, then attaches a persistent network device. After that, the VM
        is migrated and the new device is detached transiently. Then the VM is destroyed and restarted
        again. The assumption is that the persistent device is still present after the VM has rebooted.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed(
            "virsh attach-device testvm /etc/new_interface.xml --persistent"
        )

        num_devices_controller = number_of_network_devices(controllerVM)

        assert num_devices_controller == 2

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)

        assert num_devices_controller == num_devices_compute

        computeVM.succeed("virsh detach-device testvm /etc/new_interface.xml")

        assert number_of_network_devices(computeVM) == 1

        computeVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(controllerVM)
        assert number_of_network_devices(controllerVM) == 1

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        assert wait_for_ssh(controllerVM)

        assert number_of_network_devices(controllerVM) == 2

    def test_live_migration_with_hugepages(self):
        """
        Test that a VM that utilizes hugepages is still using hugepages after live migration.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages))
        computeVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages))
        status, out = controllerVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        status, out = computeVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        status, out = controllerVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) == 0, "not enough huge pages are in-use"

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        assert wait_for_ssh(computeVM)

        status, out = computeVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) == 0, "not enough huge pages are in-use"

        status, out = controllerVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) == nr_hugepages, "not all huge pages have been freed"

    def test_live_migration_with_hugepages_failure_case(self):
        """
        Test that migrating a VM with hugepages to a destination without huge pages will fail gracefully.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages))
        status, out = controllerVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.fail(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )
        assert wait_for_ssh(controllerVM)

        computeVM.fail("virsh list | grep testvm")

    def test_numa_topology(self):
        """
        We test that a NUMA topology and NUMA tunings are correctly passed to
        Cloud Hypervisor and the VM.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-numa.xml")
        controllerVM.succeed("virsh start testvm")

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
        controllerVM.succeed("virsh define /etc/domain-chv-cirros.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM, user="cirros", password="gocubsgo")

    def test_hugepages(self):
        """
        Test hugepage on-demand usage for a non-NUMA VM.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages))
        controllerVM.succeed("virsh define /etc/domain-chv-hugepages.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that we really use hugepages from the hugepage pool
        status, out = controllerVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) < nr_hugepages, "No huge pages have been used"

    def test_hugepages_prefault(self):
        """
        Test hugepage usage with pre-faulting for a non-NUMA VM.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages))
        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that all huge pages are in use
        status, out = controllerVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) == 0, "Invalid hugepage usage"

    def test_numa_hugepages(self):
        """
        Test hugepage on-demand usage for a NUMA VM.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages))
        controllerVM.succeed("virsh define /etc/domain-chv-numa-hugepages.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node0")
        assert status == 0

        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node1")
        assert status == 0

        # Check that we really use hugepages from the hugepage pool
        status, out = controllerVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) < nr_hugepages, "No huge pages have been used"

    def test_numa_hugepages_prefault(self):
        """
        Test hugepage usage with pre-faulting for a NUMA VM.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages))
        controllerVM.succeed("virsh define /etc/domain-chv-numa-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node0")
        assert status == 0

        status, _ = ssh(controllerVM, "ls /sys/devices/system/node/node1")
        assert status == 0

        # Check that all huge pages are in use
        status, out = controllerVM.execute(
            "cat /proc/meminfo | grep HugePages_Free | awk '{print $2}'"
        )
        assert int(out) == 0, "Invalid huge page usage"

    def test_serial_file_output(self):
        """
        Test that the serial to file configuration works.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-serial-file.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        status, out = controllerVM.execute("cat /tmp/vm_serial.log | wc -l")
        assert int(out) > 50

        status, out = controllerVM.execute(
            "cat /tmp/vm_serial.log | grep 'Welcome to NixOS'"
        )

    def test_managedsave(self):
        """
        Test that the managedsave call results in a state file. Further, we
        ensure the transient xml definition of the domain is deleted correctly
        after the managedsave call, because this was an issue before.
        It is also tested if the restore call is able to restore the domain successfully.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

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

        assert wait_for_ssh(controllerVM)

        status, _ = ssh(controllerVM, "ls /tmp/foo")
        assert status == 0

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

        assert wait_for_ssh(controllerVM)

        # Do some extra magic to not end in a hanging SSH session if the
        # shutdown happens too fast.
        ssh(controllerVM, "\"nohup sh -c 'sleep 5 && shutdown now' >/dev/null 2>&1 &\"")

        def is_shutoff():
            return (
                controllerVM.execute('virsh domstate testvm | grep "shut off"')[0] == 0
            )

        assert wait_until_succeed(is_shutoff)

        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

        controllerVM.succeed("virsh start testvm")
        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh shutdown testvm")
        assert wait_until_succeed(is_shutoff)
        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

        controllerVM.succeed("virsh start testvm")
        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh destroy testvm")
        assert wait_until_succeed(is_shutoff)
        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

    def test_libvirt_event_stop_failed(self):
        """
        Test that a Stopped Failed event is emitted in case the Cloud
        Hypervisor process crashes.
        """

        def eventToString(event):
            eventStrings = (
                "Defined",
                "Undefined",
                "Started",
                "Suspended",
                "Resumed",
                "Stopped",
                "Shutdown",
            )
            return eventStrings[event]

        def detailToString(event, detail):
            eventStrings = (
                ("Added", "Updated"),
                ("Removed"),
                ("Booted", "Migrated", "Restored", "Snapshot", "Wakeup"),
                ("Paused", "Migrated", "IOError", "Watchdog", "Restored", "Snapshot"),
                ("Unpaused", "Migrated", "Snapshot"),
                (
                    "Shutdown",
                    "Destroyed",
                    "Crashed",
                    "Migrated",
                    "Saved",
                    "Failed",
                    "Snapshot",
                ),
                ("Finished"),
            )
            return eventStrings[event][detail]

        stop_failed_event = False

        def eventCallback(conn, dom, event, detail, opaque):
            eventStr = eventToString(event)
            detailStr = detailToString(event, detail)
            print(
                "EVENT: Domain %s(%s) %s %s"
                % (dom.name(), dom.ID(), eventStr, detailStr)
            )
            if eventStr == "Stopped" and detailStr == "Failed":
                nonlocal stop_failed_event
                stop_failed_event = True

        libvirt.virEventRegisterDefaultImpl()

        # The testscript runs in the Host context while we want to connect to
        # the libvirt in the controllerVM
        vc = libvirt.openReadOnly("ch+tcp://localhost:2223/session")

        vc.domainEventRegister(eventCallback, None)

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Simulate crash of the VMM process
        controllerVM.succeed("kill -9 $(pidof cloud-hypervisor)")

        for _ in range(10):
            # Run one iteration of the event loop
            libvirt.virEventRunDefaultImpl()
            time.sleep(0.1)

        assert stop_failed_event
        vc.close()

        # In case we would not detect the crash, Libvirt would still show the
        # domain as running.
        controllerVM.succeed('virsh list --all | grep "shut off"')

        # Check that this case of shutting down a domain also leads to the
        # cleanup of the transient XML correctly.
        controllerVM.fail("find /run/libvirt/ch -name *.xml | grep .")

    def test_serial_tcp(self):
        """
        Test that the TCP serial mode of Cloud Hypervisor works when defined
        via Libvirt. Further, the test checks that simultaneous logging to file
        works.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-serial-tcp.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that port 2222 is used by cloud hypervisor
        controllerVM.succeed(
            "ss --numeric --processes --listening --tcp src :2222 | grep cloud-hyperviso"
        )

        # Check that we log to file in addition to the TCP socket
        def prompt():
            status, _ = controllerVM.execute(
                "cat /var/log/libvirt/ch/testvm.log | grep -q 'Welcome to NixOS'"
            )
            return status == 0

        assert wait_until_succeed(prompt)

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

    def test_serial_tcp_live_migration(self):
        """
        The test checks that a basic live migration is working with TCP serial
        configured, because we had a bug that prevented live migration in
        combination with serial TCP in the past.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-serial-tcp.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Check that port 2222 is used by cloud hypervisor
        controllerVM.succeed(
            "ss --numeric --processes --listening --tcp src :2222 | grep cloud-hyperviso"
        )

        # We define a target domain XML that changes the port of the TCP serial
        # configuration from 2222 to 2223.
        controllerVM.succeed(
            "cp /etc/domain-chv-serial-tcp.xml /tmp/domain-chv-serial-tcp.xml"
        )
        controllerVM.succeed(
            'sed -i \'s/service="2222"/service="2223"/g\' /tmp/domain-chv-serial-tcp.xml'
        )

        controllerVM.succeed(
            "virsh migrate --xml /tmp/domain-chv-serial-tcp.xml --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )
        assert wait_for_ssh(computeVM)

        computeVM.succeed(
            "ss --numeric --processes --listening --tcp src :2223 | grep cloud-hyperviso"
        )

    def test_live_migration_virsh_non_blocking(self):
        """
        We check if reading virsh commands can be executed even there is a live
        migration ongoing. Further, it is checked that modifying virsh commands
        block in the same case.

        Note:
        This test does some coarse timing checks to detect if commands are
        blocking or not. If this turns out to be flaky, we should not hesitate
        to deactivate the test.
        The duration of the migration is very dependent on the system the test
        runs on. We assume that our invocation of 'stress' creates enough load
        to stretch the migration duration to >10 seconds to be able to check if
        commands are blocking or non-blocking as expected.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        status, _ = ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")
        assert status == 0

        # Do migration in a screen session and detach
        controllerVM.succeed(
            "screen -dmS migrate virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        # Wait a moment to let the migration start
        time.sleep(2)

        # Check that 'virsh list' can be done without blocking
        self.assertLess(
            measure_ms(lambda: controllerVM.succeed("virsh list | grep -q testvm")),
            1000,
            msg="Expect virsh list to execute fast",
        )

        # Check that modifying commands like 'virsh shutdown' block until the
        # migration has finished or the timeout hits.
        self.assertGreater(
            measure_ms(lambda: controllerVM.execute("virsh shutdown testvm")),
            3000,
            msg="Expect virsh shutdown execution to take longer",
        )

        # Turn off the stress process to let the migration finish faster
        ssh(controllerVM, "pkill screen")

        # Wait for migration in the screen session to finish
        def migration_finished():
            status, _ = controllerVM.execute("screen -ls | grep migrate")
            return status != 0

        self.assertTrue(wait_until_succeed(migration_finished))

        computeVM.succeed("virsh list | grep testvm | grep running")

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

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("expect /tmp/console.expect")


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
    suite.addTest(
        LibvirtTests("test_network_hotplug_persistent_transient_detach_vm_restart")
    )
    suite.addTest(LibvirtTests("test_serial_file_output"))
    suite.addTest(LibvirtTests("test_managedsave"))
    suite.addTest(LibvirtTests("test_shutdown"))
    suite.addTest(LibvirtTests("test_libvirt_event_stop_failed"))
    suite.addTest(LibvirtTests("test_serial_tcp"))
    suite.addTest(LibvirtTests("test_serial_tcp_live_migration"))
    suite.addTest(LibvirtTests("test_live_migration_virsh_non_blocking"))
    suite.addTest(LibvirtTests("test_virsh_console_works_with_pty"))
    return suite


def measure_ms(func):
    """
    Measure the execution time of a given function in ms.
    """
    start = time.time()
    func()
    return (time.time() - start) * 1000


def wait_until_succeed(func):
    retries = 100
    for i in range(retries):
        if func():
            return True
        time.sleep(1)
    return False


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
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
