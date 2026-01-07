from functools import partial
import libvirt  # type: ignore
import textwrap
import time
import unittest
import weakref

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


class CommandGuard:
    """
    Guard that executes a command after being garbage collected.

    Some test might need to run addition cleanup when exiting/failing.
    This guard ensures that these cleanup function are run
    """

    def __init__(self, command, machine):
        """
        Initializes the guard with a command to work on a given machine.

        :param command: Function that runs a command on the given machine
        :type command: Callable (Machine)
        :param machine: Virtual machine to send the command from
        :type machine: Machine
        """
        self._finilizer = weakref.finalize(self, command, machine)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._finilizer()


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
        # A restart of the libvirt daemon resets the logging configuration, so
        # apply it freshly for every test
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

        print(f"\n\nRunning test: {self._testMethodName}\n\n")

        # In order to be able to differentiate the journal log for different
        # tests, we print a message with the test name as a marker
        controllerVM.succeed(
            f'echo "Running test: {self._testMethodName}" | systemd-cat -t testscript -p info'
        )
        computeVM.succeed(
            f'echo "Running test: {self._testMethodName}" | systemd-cat -t testscript -p info'
        )

    def tearDown(self):
        # Trigger output of the sanitizers. At least the leak sanitizer output
        # is only triggered if the program under inspection terminates.
        controllerVM.execute("systemctl restart virtchd")
        computeVM.execute("systemctl restart virtchd")

        # Make sure there are no reports of the sanitizers. We retrieve the
        # journal for only the recent test run, by looking for the test run
        # marker. We then check for any ERROR messages of the sanitizers.
        jrnCmd = f"journalctl _SYSTEMD_UNIT=virtchd.service + SYSLOG_IDENTIFIER=testscript | sed -n '/Running test: {self._testMethodName}/,$p' | grep ERROR"
        statusController, outController = controllerVM.execute(jrnCmd)
        statusCompute, outCompute = computeVM.execute(jrnCmd)

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

        self.assertNotEqual(
            statusController, 0, msg=f"Sanitizer detected an issue: {outController}"
        )
        self.assertNotEqual(
            statusCompute, 0, msg=f"Sanitizer detected an issue: {outCompute}"
        )

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
        """
        Tests device hot plugging with multiple devices of different types:
        - attaching a disk (persistent)
        - attaching a network with type ethernet (persistent)

        Also connects into the VM via each attached network interface.
        :return:
        """

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

        # Test attached network interface (type ethernet)
        self.assertTrue(wait_for_ssh(controllerVM, ip="192.168.2.2"))

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
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
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
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p --parallel --parallel-connections 4"
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
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        assert wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)

        assert num_devices_controller == num_devices_compute

        computeVM.succeed("virsh detach-device testvm /etc/new_interface.xml")

        assert number_of_network_devices(computeVM) == 1

        computeVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p --parallel --parallel-connections 4"
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
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        status, out = computeVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        assert int(out) == 0, "not enough huge pages are in-use"

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        assert wait_for_ssh(computeVM)

        status, out = computeVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        assert int(out) == 0, "not enough huge pages are in-use"

        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        assert int(out) == nr_hugepages, "not all huge pages have been freed"

    def test_live_migration_with_hugepages_failure_case(self):
        """
        Test that migrating a VM with hugepages to a destination without huge pages will fail gracefully.
        """

        nr_hugepages = 1024

        controllerVM.succeed("echo {} > /proc/sys/vm/nr_hugepages".format(nr_hugepages))
        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        assert int(out) == nr_hugepages, "unable to allocate hugepages"

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.fail(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
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

        status, out = ssh(controllerVM, "lscpu | grep Thread\\( | awk '{print $4}'")
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
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
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
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
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
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
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
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
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
                "grep -q 'Welcome to NixOS' /var/log/libvirt/ch/testvm.log"
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

    def test_live_migration_with_serial_tcp(self):
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
            "virsh migrate --xml /tmp/domain-chv-serial-tcp.xml --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
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
            "screen -dmS migrate virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        # Wait a moment to let the migration start
        time.sleep(2)

        # Check that 'virsh list' can be done without blocking
        self.assertLess(
            measure_ms(lambda: controllerVM.succeed("grep -q testvm < <(virsh list)")),
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

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("qemu-img create -f raw /tmp/disk.img 100M")
        controllerVM.succeed(
            "virsh attach-disk --domain testvm --target vdb --persistent --source /tmp/disk.img"
        )
        status, disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        assert status == 0
        assert int(disk_size_guest) == disk_size_bytes_100M
        assert int(disk_size_host) == disk_size_bytes_100M

        # Use full file path instead of virtual device name here because both should work with --path
        controllerVM.succeed(
            f"virsh blockresize --domain testvm --path /tmp/disk.img --size {disk_size_bytes_10M // 1024}"
        )

        status, disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        assert status == 0
        assert int(disk_size_guest) == disk_size_bytes_10M
        assert int(disk_size_host) == disk_size_bytes_10M

        # Use virtual device name as --path
        controllerVM.succeed(
            f"virsh blockresize --domain testvm --path vdb --size {disk_size_bytes_200M // 1024}"
        )

        status, disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        assert status == 0
        assert int(disk_size_guest) == disk_size_bytes_200M
        assert int(disk_size_host) == disk_size_bytes_200M

        # Use bytes instead of KiB
        controllerVM.succeed(
            f"virsh blockresize --domain testvm --path vdb --size {disk_size_bytes_100M}b"
        )

        status, disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        assert status == 0
        assert int(disk_size_guest) == disk_size_bytes_100M
        assert int(disk_size_host) == disk_size_bytes_100M

        # Changing to capacity must fail and not change the disk size because it
        # is not supported for file-based disk images.
        controllerVM.fail("virsh blockresize --domain testvm --path vdb --capacity")

        status, disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )
        disk_size_host = controllerVM.succeed("ls /tmp/disk.img -l | awk '{print $5}'")

        assert status == 0
        assert int(disk_size_guest) == disk_size_bytes_100M
        assert int(disk_size_host) == disk_size_bytes_100M

    def test_disk_is_locked(self):
        """
        Test that Cloud Hypervisor indeed locks images using advisory OFD locks.
        """
        # Using define + start creates a "persistent" domain rather than a transient
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("qemu-img create -f raw /tmp/disk.img 100M")

        controllerVM.succeed("fcntl-tool test-lock /tmp/disk.img | grep Unlocked")

        controllerVM.succeed(
            "virsh attach-disk --domain testvm --target vdb --source /tmp/disk.img --mode readonly"
        )
        # Check for shared read lock
        controllerVM.succeed("fcntl-tool test-lock /tmp/disk.img | grep SharedRead")
        controllerVM.succeed("virsh detach-disk --domain testvm --target vdb")

        controllerVM.succeed(
            "virsh attach-disk --domain testvm --target vdb --source /tmp/disk.img"
        )
        # Check for exclusive write lock
        controllerVM.succeed("fcntl-tool test-lock /tmp/disk.img | grep ExclusiveWrite")
        controllerVM.succeed("virsh detach-disk --domain testvm --target vdb")

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

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("qemu-img create -f qcow2 /tmp/disk.img 100M")
        controllerVM.succeed(
            "virsh attach-disk --domain testvm --target vdb --persistent --source /tmp/disk.img"
        )
        status, disk_size_guest = ssh(
            controllerVM, "lsblk --raw -b /dev/vdb | awk '{print $4}' | tail -n1"
        )

        assert status == 0
        assert int(disk_size_guest) == disk_size_bytes_100M

        controllerVM.fail(
            f"virsh blockresize --domain testvm --path vdb --size {disk_size_bytes_10M // 1024}"
        )

    def test_live_migration_parallel_connections(self):
        """
        We test that specifying --parallel and --parallel-connections results
        in a successful migration. We further check the Cloud Hypervisor logs
        to verify that multiple threads were used during migration.
        """

        parallel_connections = 4

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed(
            f"virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections {parallel_connections}"
        )
        assert wait_for_ssh(computeVM)

        num_threads = controllerVM.succeed(
            'grep -c "Spawned thread to send VM memory" /var/log/libvirt/ch/testvm.log'
        ).strip()

        # CHV starts one thread less than the given parallel connections because
        # the main thread also utilized
        self.assertEqual(parallel_connections - 1, int(num_threads))

    def test_live_migration_with_vcpu_pinning(self):
        """
        This tests checks that the configured vcpu affinity is still in
        use after a live migration.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-numa.xml")
        controllerVM.succeed("virsh start testvm")
        assert wait_for_ssh(controllerVM)

        chv_pid_controller = controllerVM.succeed("pidof cloud-hypervisor").rstrip()

        tid_vcpu0_controller = controllerVM.succeed(
            f"ps -Lo tid,comm --pid {chv_pid_controller} | grep vcpu0 | awk '{{print $1}}'"
        ).rstrip()
        tid_vcpu2_controller = controllerVM.succeed(
            f"ps -Lo tid,comm --pid {chv_pid_controller} | grep vcpu2 | awk '{{print $1}}'"
        ).rstrip()

        taskset_vcpu0_controller = controllerVM.succeed(
            f"taskset -p {tid_vcpu0_controller} | awk '{{print $6}}'"
        ).rstrip()
        taskset_vcpu2_controller = controllerVM.succeed(
            f"taskset -p {tid_vcpu2_controller} | awk '{{print $6}}'"
        ).rstrip()

        assert int(taskset_vcpu0_controller, 16) == 0x3
        assert int(taskset_vcpu2_controller, 16) == 0xC

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )
        assert wait_for_ssh(computeVM)

        chv_pid_compute = computeVM.succeed("pidof cloud-hypervisor").rstrip()

        tid_vcpu0_compute = computeVM.succeed(
            f"ps -Lo tid,comm --pid {chv_pid_compute} | grep vcpu0 | awk '{{print $1}}'"
        ).rstrip()
        tid_vcpu2_compute = computeVM.succeed(
            f"ps -Lo tid,comm --pid {chv_pid_compute} | grep vcpu2 | awk '{{print $1}}'"
        ).rstrip()

        taskset_vcpu0_compute = computeVM.succeed(
            f"taskset -p {tid_vcpu0_compute} | awk '{{print $6}}'"
        ).rstrip()
        taskset_vcpu2_compute = computeVM.succeed(
            f"taskset -p {tid_vcpu2_compute} | awk '{{print $6}}'"
        ).rstrip()

        assert int(taskset_vcpu0_controller, 16) == int(taskset_vcpu0_compute, 16)
        assert int(taskset_vcpu2_controller, 16) == int(taskset_vcpu2_compute, 16)

    def test_live_migration_kill_chv_on_sender_side(self):
        """
        Test that libvirt survives a CHV crash of the sender side during
        a live migration. We expect that libvirt does the correct cleanup
        and no VM is present on both sides.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        status, _ = ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")
        assert status == 0

        # Do migration in a screen session and detach
        controllerVM.succeed(
            "screen -dmS migrate virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        # Wait a moment to let the migration start
        time.sleep(5)

        # Kill the cloud-hypervisor on the sender side
        controllerVM.succeed("kill -9 $(pidof cloud-hypervisor)")

        # Ensure the VM is really gone and we have no zombie VMs
        def check_virsh_list(vm):
            status, _ = vm.execute("virsh list | grep testvm > /dev/null")
            return status == 0

        status = wait_until_fail(lambda: check_virsh_list(controllerVM))
        assert status

        status = wait_until_fail(lambda: check_virsh_list(computeVM))
        assert status

    def test_live_migration_kill_chv_on_receiver_side(self):
        """
        Test that libvirt survives a CHV crash of the sender side during
        a live migration. We expect that libvirt does the correct cleanup
        and no VM is present on both sides.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        status, _ = ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")
        assert status == 0

        # Do migration in a screen session and detach
        controllerVM.succeed(
            "screen -dmS migrate virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        # Wait a moment to let the migration start
        time.sleep(5)

        # Kill the cloud-hypervisor on the sender side
        computeVM.succeed("kill -9 $(pidof cloud-hypervisor)")

        # Ensure the VM is really gone and we have no zombie VMs
        def check_virsh_list(vm):
            status, _ = vm.execute("virsh list | grep testvm > /dev/null")
            return status == 0

        status = wait_until_fail(lambda: check_virsh_list(computeVM))
        assert status

        status = wait_until_succeed(lambda: check_virsh_list(controllerVM))
        assert status

        controllerVM.succeed("virsh list | grep 'running'")

        assert wait_for_ssh(controllerVM)

    def test_live_migration_tls(self):
        """
        Test the TLS encrypted live migration via virsh between 2 hosts. With
        the right certificates, using IP addresses and DNS names should work,
        thus we test all combinations.
        To be extra sure we also check whether a TLS connection is established
        by checking for TLS handshakes. We should see a TLS handshake per
        connection used for the live migration.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        parallel_connections = 4
        parallel_string = f"--parallel --parallel-connections {parallel_connections}"
        for parallel in [True, False]:
            for dst_host, dst, src in [
                ("192.168.100.2", computeVM, controllerVM),
                ("controllerVM", controllerVM, computeVM),
            ]:
                # To check for established TLS connections, we count the number
                # of successful TLS handshakes.
                #
                # TLS handshakes consist of a ClientHello and a ServerHello.
                # We can capture the network traffic using tcpdump and then
                # analyze it using tshark. Because the capture can get really
                # big, we want to apply a filter that only captures TLS packets.
                #
                # Unfortunately, ClientHello packets can be really big, because
                # the ClientHello has some fields that contain lists of variable
                # size (see RFC8446). Thus, when capturing only packets that
                # look like TLS with tcpdump, the ClientHello packet may be
                # split into two packets, the second packet is not captured and
                # tshark does not see the ClientHello ... (although when you
                # look at the packet with wireshark, it looks awfully like a
                # TLS packet)
                #
                # But we know for sure that a ServerHello packet is only sent by
                # the TLS server after receiving a ClientHello. Thus, we count
                # the ServerHello packets, and to be extra sure we extract the
                # TCP stream using tshark and make sure that there is a
                # ServerHello for every TCP stream.

                # (tcp[12] & 0xf0) >> 2 gives the TCP header size, the TLS
                # header comes after that
                tls_filter = "(tcp[((tcp[12] & 0xf0) >> 2)] = 0x16)"  # filters for TLS handshake packets

                port = 49152  # first port of the libvirt default port range for live migrations
                port_filter = f"tcp port {port}"

                # To make sure tcpdump is ready to capture, we wait until it
                # reports "listening on eth1"
                dst.succeed(
                    f"systemd-run --unit tcpdump-mig -- bash -lc 'tcpdump -i eth1 -w /tmp/tls.pcap \"{port_filter} and {tls_filter}\" 2> /tmp/tls.log'"
                )
                dst.wait_until_succeeds("grep -q 'listening on eth1' /tmp/tls.log")

                src.succeed(
                    f"virsh migrate --domain testvm --desturi ch+tcp://{dst_host}/session --persistent --live --p2p --tls {parallel_string if parallel else ''}"
                )

                dst.succeed("systemctl stop tcpdump-mig")

                # tls.handshake.type == 2 filters for ServerHello. This invocation
                # gives us a string like "0\n1\n2\n3\n" and we immediately split it
                # into a list.
                server_hello = (
                    dst.succeed(
                        'tshark -r /tmp/tls.pcap -Y "tls.handshake.type == 2" -T fields -e tcp.stream'
                    )
                    .strip()
                    .split("\n")
                )

                expected = parallel_connections if parallel else 1
                server_hellos = len(server_hello)  # count ServerHellos
                tcp_streams = len(
                    set(server_hello)
                )  # creating a set will discard duplicates.

                assert server_hellos == expected
                assert tcp_streams == expected

                assert wait_for_ssh(dst)

    def test_live_migration_tls_without_certificates(self):
        """
        Test that live migration fails when TLS is requested but no
        certificates have been deployed. The assumption is that the
        migration fails, and the VM is still running on the sender side.
        Both sides should also have a non-blocking virsh.

        There are two different cases we have to test:
          1. The whole folder is missing, this case should be handled by
             libvirt.
          2. Only the files are missing. libvirt only checks whether the folder
             exists (because it doesn't know which files are necessary), thus
             Cloud Hypervisor has to handle that without a panic.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        assert wait_for_ssh(controllerVM)

        certificate_dir = "/var/lib/libvirt/ch/pki"

        # Function to move the certificates into a temporary directory.
        def remove_certificates(remove_dir, machine):
            tmp_dir = machine.succeed("mktemp -d").strip()
            machine.succeed(f"mv {certificate_dir}/* {tmp_dir}/")
            # If remove_dir is true, we delete the files and the directory.
            # Otherwise we only delete the files but keep the directory.
            machine.succeed(f"rm -rf {certificate_dir}{'' if remove_dir else '/*'}")
            return tmp_dir

        # Function to reset the certificates.
        def reset_certs(tmp_dir, machine):
            machine.succeed(f"mkdir -p {certificate_dir}")
            machine.succeed(f"mv {tmp_dir}/* {certificate_dir}/")

        # Function check that all certificates and keys exist.
        def check_certificates(machine):
            expected_files = ["ca-cert.pem", "server-cert.pem", "server-key.pem"]
            files = machine.succeed(f"ls {certificate_dir}").strip()
            for expected_file in expected_files:
                assert expected_file in files, f"{expected_file} not in {files}"

        # We first check case one, then case two (see comment above).
        for remove_cert_dir in [True, False]:
            remove_certs = partial(remove_certificates, remove_cert_dir)
            # Certificates are missing on both machines.
            reset_certs_controller = partial(reset_certs, remove_certs(controllerVM))
            reset_certs_compute = partial(reset_certs, remove_certs(computeVM))
            with (
                CommandGuard(reset_certs_controller, controllerVM) as _,
                CommandGuard(reset_certs_compute, computeVM) as _,
            ):
                controllerVM.fail(
                    "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --tls"
                )
                assert wait_for_ssh(controllerVM)

                controllerVM.succeed("virsh list | grep 'testvm'")
                computeVM.fail("virsh list | grep 'testvm'")

            check_certificates(controllerVM)
            check_certificates(computeVM)

            # Certificates are missing only on the source machine.
            reset_certs_controller = partial(reset_certs, remove_certs(controllerVM))
            with CommandGuard(reset_certs_controller, controllerVM) as _:
                controllerVM.fail(
                    "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --tls"
                )
                assert wait_for_ssh(controllerVM)

                controllerVM.succeed("virsh list | grep 'testvm'")
                computeVM.fail("virsh list | grep 'testvm'")

            check_certificates(controllerVM)

            # Certificates are missing only on the target machine.
            reset_certs_compute = partial(reset_certs, remove_certs(computeVM))
            with CommandGuard(reset_certs_compute, computeVM) as _:
                controllerVM.fail(
                    "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --tls"
                )
                assert wait_for_ssh(controllerVM)

                controllerVM.succeed("virsh list | grep 'testvm'")
                computeVM.fail("virsh list | grep 'testvm'")

            check_certificates(computeVM)


def suite():
    # Test cases in alphabetical order
    testcases = [
        LibvirtTests.test_disk_is_locked,
        LibvirtTests.test_disk_resize_qcow2,
        LibvirtTests.test_disk_resize_raw,
        LibvirtTests.test_hotplug,
        LibvirtTests.test_hugepages,
        LibvirtTests.test_hugepages_prefault,
        LibvirtTests.test_libvirt_event_stop_failed,
        LibvirtTests.test_libvirt_restart,
        LibvirtTests.test_live_migration,
        LibvirtTests.test_live_migration_kill_chv_on_receiver_side,
        LibvirtTests.test_live_migration_kill_chv_on_sender_side,
        LibvirtTests.test_live_migration_parallel_connections,
        LibvirtTests.test_live_migration_tls,
        LibvirtTests.test_live_migration_tls_without_certificates,
        LibvirtTests.test_live_migration_virsh_non_blocking,
        LibvirtTests.test_live_migration_with_hotplug,
        LibvirtTests.test_live_migration_with_hotplug_and_virtchd_restart,
        LibvirtTests.test_live_migration_with_hugepages,
        LibvirtTests.test_live_migration_with_hugepages_failure_case,
        LibvirtTests.test_live_migration_with_serial_tcp,
        LibvirtTests.test_live_migration_with_vcpu_pinning,
        LibvirtTests.test_managedsave,
        LibvirtTests.test_network_hotplug_attach_detach_persistent,
        LibvirtTests.test_network_hotplug_attach_detach_transient,
        LibvirtTests.test_network_hotplug_persistent_transient_detach_vm_restart,
        LibvirtTests.test_network_hotplug_persistent_vm_restart,
        LibvirtTests.test_network_hotplug_transient_vm_restart,
        LibvirtTests.test_numa_hugepages,
        LibvirtTests.test_numa_hugepages_prefault,
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


def measure_ms(func):
    """
    Measure the execution time of a given function in ms.
    """
    start = time.time()
    func()
    return (time.time() - start) * 1000


def wait_until_succeed(func):
    retries = 600
    for i in range(retries):
        if func():
            return True
        time.sleep(0.1)
    return False


def wait_until_fail(func):
    retries = 600
    for i in range(retries):
        if not func():
            return True
        time.sleep(0.1)
    return False


def wait_for_ssh(machine, user="root", password="root", ip="192.168.1.2"):
    """
    Waits for SSH to become available to connect into the Cloud Hypervisor VM
    hosted on the corresponding machine.

    Effectively we use it to wait until the Cloud Hypervisor VM's network is up
    and available.

    :param machine: VM host
    :param user: user for SSH login
    :param password: password for SSH login
    :param ip: SSH host to log into
    :return: True if the VM became available in a reasonable amount of time
    """
    retries = 100
    for i in range(retries):
        print(f"Wait for ssh {i}/{retries}")
        status, _ = ssh(machine, "echo hello", user, password, ip)
        if status == 0:
            return True
        time.sleep(0.1)
    return False


def ssh(machine, cmd, user="root", password="root", ip="192.168.1.2"):
    """
    Runs the specified command in the Cloud Hypervisor VM via SSH.

    The specified machine is used as SSH jump host.

    :param machine: Machine to run SSH on
    :param cmd: The command to execute via SSH
    :param user: user for SSH login
    :param password: password for SSH login
    :param ip: SSH host to log into
    :return: status and output
    """
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


def reset_system_image(machine):
    """
    Replaces the (possibly modified) system image with its original
    image.

    This helps avoid situations where a VM hangs during boot after the
    underlying disk’s BDF was changed, since OVMF may store NVRAM
    entries that reference specific BDF values.
    """
    machine.succeed(
        "rsync -aL --no-perms --inplace --checksum /etc/nixos.img /nfs-root/nixos.img"
    )


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
