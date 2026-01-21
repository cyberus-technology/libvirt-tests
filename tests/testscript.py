from functools import partial
import libvirt  # type: ignore
import os
import textwrap
import time
import unittest
import weakref

# pyright: reportPossiblyUnboundVariable=false

# Following is required to allow proper linting of the python code in IDEs.
# Because certain functions like start_all() and certain objects like computeVM
# or other machines are added by Nix, we need to provide certain stub objects
# in order to allow the IDE to lint the python code successfully.
if "start_all" not in globals():
    from nixos_test_stubs import start_all, computeVM, controllerVM, Machine  # type: ignore


VIRTIO_NETWORK_DEVICE = "1af4:1041"
VIRTIO_BLOCK_DEVICE = "1af4:1042"
VIRTIO_ENTROPY_SOURCE = "1af4:1044"

# The VM we migrate has 2GiB of memory: 1024 * 2 MiB to cover RAM
NR_HUGEPAGES = 1024


class SaveLogsOnErrorTestCase(unittest.TestCase):
    """
    Custom TestCase class that saves interesting logs in error case.
    """

    def run(self, result=None):
        if result is None:
            result = self.defaultTestResult()

        original_addError = result.addError
        original_addFailure = result.addFailure

        def custom_addError(test, err):
            self.save_logs(test, f"Error in {test._testMethodName}")
            original_addError(test, err)

        def custom_addFailure(test, err):
            self.save_logs(test, f"Failure in {test._testMethodName}")
            original_addFailure(test, err)

        result.addError = custom_addError
        result.addFailure = custom_addFailure

        return super().run(result)

    def save_machine_log(self, machine: Machine, log_path, dst_path):
        try:
            machine.copy_from_vm(log_path, dst_path)
        # Non-existing logs lead to an Exception that we ignore
        except Exception:
            pass

    def save_logs(self, test, message):
        print(f"{message}")

        if "DBG_LOG_DIR" not in os.environ:
            return

        for machine in [controllerVM, computeVM]:
            dst_path = os.path.join(
                os.environ["DBG_LOG_DIR"], f"{test._testMethodName}", f"{machine.name}"
            )
            self.save_machine_log(machine, "/var/log/libvirt/ch/testvm.log", dst_path)
            self.save_machine_log(machine, "/var/log/libvirt/libvirtd.log", dst_path)


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

        self._finilizer = weakref.finalize(self, command, machine)  # pyright: ignore[reportCallIssue]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._finilizer()


class LibvirtTests(SaveLogsOnErrorTestCase):
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

        # Define a libvirt network and automatically starts it
        controllerVM.succeed("virsh net-create /etc/libvirt_test_network.xml")

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

    def test_live_migration_with_hotplug_and_virtchd_restart(self):
        """
        Test that we can restart the libvirt daemon (virtchd) in between live-migrations
        and hotplugging.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")

        wait_for_ssh(controllerVM)

        hotplug(controllerVM, "virsh attach-device testvm /etc/new_interface.xml")

        num_devices_controller = number_of_network_devices(controllerVM)
        self.assertEqual(num_devices_controller, 2)

        num_disk_controller = number_of_storage_devices(controllerVM)
        self.assertEqual(num_disk_controller, 1)

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)
        self.assertEqual(num_devices_compute, 2)

        controllerVM.succeed("systemctl restart virtchd")
        computeVM.succeed("systemctl restart virtchd")

        computeVM.succeed("virsh list | grep testvm")
        controllerVM.fail("virsh list | grep testvm")

        hotplug(computeVM, "virsh detach-device testvm /etc/new_interface.xml")
        hotplug(
            computeVM,
            "virsh attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img",
        )

        num_devices_compute = number_of_network_devices(computeVM)
        self.assertEqual(num_devices_compute, 1)

        num_disk_compute = number_of_storage_devices(computeVM)
        self.assertEqual(num_disk_compute, 2)

        computeVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(controllerVM)

        controllerVM.succeed("systemctl restart virtchd")
        computeVM.succeed("systemctl restart virtchd")

        computeVM.fail("virsh list | grep testvm")
        controllerVM.succeed("virsh list | grep testvm")

        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")

        num_disk_compute = number_of_storage_devices(controllerVM)
        self.assertEqual(num_disk_compute, 1)

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

        wait_for_ssh(controllerVM)

        hotplug(controllerVM, "virsh attach-device testvm /etc/new_interface.xml")
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img",
        )

        for i in range(2):
            # Explicitly use IP in desturi as this was already a problem in the past
            controllerVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://192.168.100.2/session --persistent --live --p2p"
            )
            wait_for_ssh(computeVM)
            computeVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
            )
            wait_for_ssh(controllerVM)

    def test_live_migration_with_hotplug(self):
        """
        Test that transient and persistent devices are correctly handled during live migrations.
        The tests first starts a VM, then attaches a persistent network device. After that, the VM
        is migrated and the new device is detached transiently. Then the VM is destroyed and restarted
        again. The assumption is that the persistent device is still present after the VM has rebooted.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface.xml --persistent",
        )

        num_devices_controller = number_of_network_devices(controllerVM)
        self.assertEqual(num_devices_controller, 2)

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)
        self.assertEqual(num_devices_controller, num_devices_compute)
        hotplug(computeVM, "virsh detach-device testvm /etc/new_interface.xml")
        self.assertEqual(number_of_network_devices(computeVM), 1)

        computeVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(controllerVM)
        self.assertEqual(number_of_network_devices(controllerVM), 1)

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)
        self.assertEqual(number_of_network_devices(controllerVM), 2)

    def test_live_migration_with_hugepages(self):
        """
        Test that a VM that utilizes hugepages is still using hugepages after live migration.
        """

        controllerVM.succeed(f"echo {NR_HUGEPAGES} > /proc/sys/vm/nr_hugepages")
        computeVM.succeed(f"echo {NR_HUGEPAGES} > /proc/sys/vm/nr_hugepages")
        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertEqual(
            int(out), NR_HUGEPAGES, "unable to allocate hugepages on controllerVM"
        )

        status, out = computeVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertEqual(
            int(out), NR_HUGEPAGES, "unable to allocate hugepages on computeVM"
        )

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertEqual(
            int(out), 0, "not enough huge pages are in-use on controllerVM"
        )

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(computeVM)

        status, out = computeVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertEqual(int(out), 0, "not enough huge pages are in-use on computeVM")

        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertEqual(
            int(out), NR_HUGEPAGES, "not all huge pages have been freed on controllerVM"
        )

    def test_live_migration_with_hugepages_failure_case(self):
        """
        Test that migrating a VM with hugepages to a destination without huge pages will fail gracefully.
        """

        controllerVM.succeed(f"echo {NR_HUGEPAGES} > /proc/sys/vm/nr_hugepages")
        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertEqual(
            int(out), NR_HUGEPAGES, "not all huge pages have been freed on controllerVM"
        )

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.fail(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )
        wait_for_ssh(controllerVM)

        computeVM.fail("virsh list | grep testvm")

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

    def test_hugepages(self):
        """
        Test hugepage on-demand usage for a non-NUMA VM.
        """

        controllerVM.succeed(f"echo {NR_HUGEPAGES} > /proc/sys/vm/nr_hugepages")
        controllerVM.succeed("virsh define /etc/domain-chv-hugepages.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that we really use hugepages from the hugepage pool
        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertLess(int(out), NR_HUGEPAGES, "no huge pages have been used")

    def test_hugepages_prefault(self):
        """
        Test hugepage usage with pre-faulting for a non-NUMA VM.
        """

        controllerVM.succeed(f"echo {NR_HUGEPAGES} > /proc/sys/vm/nr_hugepages")
        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that all huge pages are in use
        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertEqual(int(out), 0, "not all huge pages are in use")

    def test_numa_hugepages(self):
        """
        Test hugepage on-demand usage for a NUMA VM.
        """

        controllerVM.succeed(f"echo {NR_HUGEPAGES} > /proc/sys/vm/nr_hugepages")
        controllerVM.succeed("virsh define /etc/domain-chv-numa-hugepages.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        ssh(controllerVM, "ls /sys/devices/system/node/node0")

        ssh(controllerVM, "ls /sys/devices/system/node/node1")

        # Check that we really use hugepages from the hugepage pool
        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertLess(int(out), NR_HUGEPAGES, "no huge pages have been used")

    def test_numa_hugepages_prefault(self):
        """
        Test hugepage usage with pre-faulting for a NUMA VM.
        """

        controllerVM.succeed(f"echo {NR_HUGEPAGES} > /proc/sys/vm/nr_hugepages")
        controllerVM.succeed("virsh define /etc/domain-chv-numa-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        ssh(controllerVM, "ls /sys/devices/system/node/node0")

        ssh(controllerVM, "ls /sys/devices/system/node/node1")

        # Check that all huge pages are in use
        status, out = controllerVM.execute(
            "awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo"
        )
        self.assertEqual(int(out), 0, "not all huge pages are in use")

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

        wait_for_ssh(controllerVM)

        # Simulate crash of the VMM process
        controllerVM.succeed("kill -9 $(pidof cloud-hypervisor)")

        for _ in range(10):
            # Run one iteration of the event loop
            libvirt.virEventRunDefaultImpl()
            time.sleep(0.1)

        self.assertTrue(stop_failed_event)
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

    def test_live_migration_with_serial_tcp(self):
        """
        The test checks that a basic live migration is working with TCP serial
        configured, because we had a bug that prevented live migration in
        combination with serial TCP in the past.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-serial-tcp.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

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
        wait_for_ssh(computeVM)

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

        wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")

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

        wait_until_succeed(migration_finished)

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

    def test_live_migration_parallel_connections(self):
        """
        We test that specifying --parallel and --parallel-connections results
        in a successful migration. We further check the Cloud Hypervisor logs
        to verify that multiple threads were used during migration.
        """

        parallel_connections = 4

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.succeed(
            f"virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections {parallel_connections}"
        )
        wait_for_ssh(computeVM)

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
        wait_for_ssh(controllerVM)

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

        self.assertEqual(int(taskset_vcpu0_controller, 16), 0x3)
        self.assertEqual(int(taskset_vcpu2_controller, 16), 0xC)

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )
        wait_for_ssh(computeVM)

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

        self.assertEqual(
            int(taskset_vcpu0_controller, 16), int(taskset_vcpu0_compute, 16)
        )
        self.assertEqual(
            int(taskset_vcpu2_controller, 16), int(taskset_vcpu2_compute, 16)
        )

    def test_live_migration_kill_chv_on_sender_side(self):
        """
        Test that libvirt survives a CHV crash of the sender side during
        a live migration. We expect that libvirt does the correct cleanup
        and no VM is present on both sides.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")

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

        wait_until_fail(lambda: check_virsh_list(controllerVM))
        wait_until_fail(lambda: check_virsh_list(computeVM))

    def test_live_migration_kill_chv_on_receiver_side(self):
        """
        Test that libvirt survives a CHV crash of the sender side during
        a live migration. We expect that libvirt does the correct cleanup
        and no VM is present on both sides.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")

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

        wait_until_fail(lambda: check_virsh_list(computeVM))

        wait_until_succeed(lambda: check_virsh_list(controllerVM))

        controllerVM.succeed("virsh list | grep 'running'")

        wait_for_ssh(controllerVM)

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

        wait_for_ssh(controllerVM)

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

                self.assertEqual(server_hellos, expected)
                self.assertEqual(tcp_streams, expected)
                wait_for_ssh(dst)

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

        wait_for_ssh(controllerVM)

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
                self.assertIn(
                    expected_file,
                    files,
                    f"didn't find file '{expected_file}' in '{certificate_dir}'",
                )

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
                wait_for_ssh(controllerVM)

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
                wait_for_ssh(controllerVM)

                controllerVM.succeed("virsh list | grep 'testvm'")
                computeVM.fail("virsh list | grep 'testvm'")

            check_certificates(controllerVM)

            # Certificates are missing only on the target machine.
            reset_certs_compute = partial(reset_certs, remove_certs(computeVM))
            with CommandGuard(reset_certs_compute, computeVM) as _:
                controllerVM.fail(
                    "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --tls"
                )
                wait_for_ssh(controllerVM)

                controllerVM.succeed("virsh list | grep 'testvm'")
                computeVM.fail("virsh list | grep 'testvm'")

            check_certificates(computeVM)

    def test_bdf_implicit_assignment(self):
        """
        Test if all BDFs are correctly assigned in a scenario where some
        are fixed in the XML and some are assigned by libvirt.

        The domain XML we use here leaves a slot ID 0x03 free, but
        allocates IDs 0x01, 0x02 and 0x04. 0x01 and 0x02 are dynamically
        assigned by libvirt and not given in the domain XML. As 0x04 is
        the first free ID, we expect this to be selected for the first
        device we add to show that libvirt uses gaps. We add another
        disk to show that all succeeding BDFs would be allocated
        dynamically. Moreover, we show that all BDF assignments
        survive live migration.
        """
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface_explicit_bdf.xml",
        )
        # Add a disks that receive the first free BDFs
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdb.img 5M"
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img",
        )
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdc.img 5M"
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdc --source /var/lib/libvirt/storage-pools/nfs-share/vdc.img",
        )

        devices = pci_devices_by_bdf(controllerVM)
        # Implicitly added fixed to 0x01
        self.assertEqual(devices["00:01.0"], VIRTIO_ENTROPY_SOURCE)
        # Added by XML; dynamic BDF
        self.assertEqual(devices["00:02.0"], VIRTIO_NETWORK_DEVICE)
        # Add through XML
        self.assertEqual(devices["00:03.0"], VIRTIO_BLOCK_DEVICE)
        # Defined fixed BDF in XML; Hotplugged
        self.assertEqual(devices["00:04.0"], VIRTIO_NETWORK_DEVICE)
        # Hotplugged by this test (vdb)
        self.assertEqual(devices["00:05.0"], VIRTIO_BLOCK_DEVICE)
        # Hotplugged by this test (vdc)
        self.assertEqual(devices["00:06.0"], VIRTIO_BLOCK_DEVICE)

        # Check that we can reuse the same non-statically allocated BDF
        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")

        self.assertIsNone(pci_devices_by_bdf(controllerVM).get("00:05.0"))
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img",
        )
        self.assertEqual(
            pci_devices_by_bdf(controllerVM).get("00:05.0"), VIRTIO_BLOCK_DEVICE
        )

        # We free slot 4 and 5 ...
        hotplug(
            controllerVM,
            "virsh detach-device testvm /etc/new_interface_explicit_bdf.xml",
        )
        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")
        self.assertIsNone(pci_devices_by_bdf(controllerVM).get("00:04.0"))
        self.assertIsNone(pci_devices_by_bdf(controllerVM).get("00:05.0"))
        # ...and expect the same disk that was formerly attached non-statically to slot 5 now to pop up in slot 4
        # through implicit BDF allocation.
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img",
        )
        self.assertEqual(
            pci_devices_by_bdf(controllerVM).get("00:04.0"), VIRTIO_BLOCK_DEVICE
        )

        # Check that BDFs stay the same after migration
        devices_before_livemig = pci_devices_by_bdf(controllerVM)
        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --live --p2p"
        )
        wait_for_ssh(computeVM)
        devices_after_livemig = pci_devices_by_bdf(computeVM)
        self.assertEqual(devices_before_livemig, devices_after_livemig)

    def test_bdf_explicit_assignment(self):
        """
        Test if all BDFs are correctly assigned when binding them
        explicitly to BDFs.

        This test also shows that we can freely define the BDF that is
        given to the RNG device. Moreover, we show that all BDF
        assignments survive live migration, that allocating the same
        BDF twice fails and that we can reuse BDFs if the respective
        device was detached.

        Developer Note: This test resets the NixOS image.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-static-bdf.xml")
        # Reset the image to purge any information about boot devices when terminating
        with CommandGuard(reset_system_image, controllerVM) as _:
            controllerVM.succeed("virsh start testvm")
            wait_for_ssh(controllerVM)

            hotplug(
                controllerVM,
                "virsh attach-device testvm /etc/new_interface_explicit_bdf.xml",
            )
            hotplug(
                controllerVM,
                "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/cirros.img --address pci:0.0.17.0",
            )

            devices = pci_devices_by_bdf(controllerVM)
            self.assertEqual(devices["00:01.0"], VIRTIO_BLOCK_DEVICE)
            self.assertEqual(devices["00:02.0"], VIRTIO_NETWORK_DEVICE)
            self.assertIsNone(devices.get("00:03.0"))
            self.assertEqual(devices["00:04.0"], VIRTIO_NETWORK_DEVICE)
            self.assertEqual(devices["00:05.0"], VIRTIO_ENTROPY_SOURCE)
            self.assertIsNone(devices.get("00:06.0"))
            self.assertEqual(devices["00:17.0"], VIRTIO_BLOCK_DEVICE)

            # Check that BDF is freed and can be reallocated when de-/attaching a (entirely different) device
            hotplug(
                controllerVM,
                "virsh detach-device testvm /etc/new_interface_explicit_bdf.xml",
            )
            hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")
            self.assertIsNone(pci_devices_by_bdf(controllerVM).get("00:04.0"))
            self.assertIsNone(pci_devices_by_bdf(controllerVM).get("00:17.0"))
            hotplug(
                controllerVM,
                "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/cirros.img --address pci:0.0.04.0",
            )
            devices_before_livemig = pci_devices_by_bdf(controllerVM)
            self.assertEqual(devices_before_livemig["00:04.0"], VIRTIO_BLOCK_DEVICE)

            # Adding to the same bdf twice fails
            hotplug_fail(
                controllerVM,
                "virsh attach-device testvm /etc/new_interface_explicit_bdf.xml",
            )

            # Check that BDFs stay the same after migration
            controllerVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --live --p2p"
            )
            wait_for_ssh(computeVM)
            devices_after_livemig = pci_devices_by_bdf(computeVM)
            self.assertEqual(devices_before_livemig, devices_after_livemig)

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

    def test_bdfs_dont_conflict_after_transient_unplug(self):
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
        with CommandGuard(reset_system_image, controllerVM) as _:
            # Using define + start creates a "persistent" domain rather than a transient
            controllerVM.succeed("virsh define /etc/domain-chv.xml")
            controllerVM.succeed("virsh start testvm")
            wait_for_ssh(controllerVM)

            # Add a persistent disk.
            controllerVM.succeed(
                "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdb.img 5M"
            )
            hotplug(
                controllerVM,
                "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img --persistent",
            )
            # Remove transient. The device is removed from the transient config but not from the persistent one. The
            # transient config has to mark the BDF as still in use nevertheless.
            hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")

            # Attach another device persistently. If we did not respect in the transient config that the disk we
            # detached before is still present in persistent config, then we now try to assign BDF 4 twice in the
            # persistent config. In other words: Persistent and transient config's BDF management are out of sync if
            # this command fails.
            hotplug(
                controllerVM,
                "virsh attach-device testvm /etc/new_interface.xml --persistent",
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


def suite():
    # Test cases in alphabetical order
    testcases = [
        # First everything with huge-pages to prevent memory-fragmentation
        # preventing huge-page allocation
        LibvirtTests.test_numa_hugepages,
        LibvirtTests.test_numa_hugepages_prefault,
        LibvirtTests.test_hugepages,
        LibvirtTests.test_hugepages_prefault,
        LibvirtTests.test_live_migration_with_hugepages,
        LibvirtTests.test_live_migration_with_hugepages_failure_case,
        # Next, all other test cases.
        LibvirtTests.test_bdf_explicit_assignment,
        LibvirtTests.test_bdf_implicit_assignment,
        LibvirtTests.test_bdf_invalid_device_id,
        LibvirtTests.test_bdf_valid_device_id_with_function_id,
        LibvirtTests.test_bdfs_dont_conflict_after_transient_unplug,
        LibvirtTests.test_bdfs_implicitly_assigned_same_after_recreate,
        LibvirtTests.test_cirros_image,
        LibvirtTests.test_disk_is_locked,
        LibvirtTests.test_disk_resize_qcow2,
        LibvirtTests.test_disk_resize_raw,
        LibvirtTests.test_hotplug,
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
        LibvirtTests.test_live_migration_with_serial_tcp,
        LibvirtTests.test_live_migration_with_vcpu_pinning,
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


def measure_ms(func):
    """
    Measure the execution time of a given function in ms.
    """
    start = time.time()
    func()
    return (time.time() - start) * 1000


def wait_until_succeed(func, retries=600):
    """
    Waits for the command to succeed.
    After each failure, it waits 100ms.

    :param func: Function that is expected to succeed.
    :param retries: Amount of retries.
    """
    for _i in range(retries):
        if func():
            return
        time.sleep(0.1)
    raise RuntimeError("function didn't succeed")


def wait_until_fail(func, retries=600):
    """
    Waits for the command to fail.
    After each success, it waits 100ms.

    :param func: Function that is expected to succeed.
    :param retries: Amount of retries.
    """
    for i in range(retries):
        if not func():
            return
        time.sleep(0.1)
    raise RuntimeError("function didn't fail")


def wait_for_ssh(
    machine: Machine,
    user: str = "root",
    password: str = "root",
    ip: str = "192.168.1.2",
    retries: int = 100,
):
    """
    Waits for SSH to become available to connect into the Cloud Hypervisor VM
    hosted on the corresponding machine.

    Effectively we use it to wait until the Cloud Hypervisor VM's network is up
    and available.

    :param machine: VM host
    :param user: user for SSH login
    :param password: password for SSH login
    :param ip: SSH host to log into
    :param retries: number of retries
    """
    for i in range(retries):
        print(f"Wait for ssh {i}/{retries}")
        try:
            ssh(machine, "echo hello", user, password, ip)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Could not establish SSH connection to {ip}")


def ssh(
    machine: Machine,
    cmd: str,
    user: str = "root",
    password: str = "root",
    ip: str = "192.168.1.2",
) -> str:
    """
    Runs the specified command in the Cloud Hypervisor VM via SSH.

    The specified machine is used as SSH jump host.

    :param machine: Machine to run SSH on
    :param cmd: The command to execute via SSH
    :param user: user for SSH login
    :param password: password for SSH login
    :param ip: SSH host to log into
    """
    status, out = machine.execute(
        f"sshpass -p {password} ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no {user}@{ip} {cmd}"
    )
    if status != 0:
        raise RuntimeError(f"failed to execute cmd in VM: `{cmd}`")
    return out


def number_of_devices(machine: Machine, filter: str = "") -> int:
    """
    Returns the number of PCI devices in the VM.

    :param filter: Optional filter for the PCI device, e.g., vendor ID or device class.
    :param machine: VM host
    :return: number of PCI devices in VM
    :return: output from command
    """
    if filter == "":
        cmd = "lspci | wc -l"
    else:
        cmd = f"lspci -n | grep {filter} | wc -l"
    out = ssh(machine, cmd)
    return int(out)


def number_of_network_devices(machine: Machine) -> int:
    """
    Returns the number of PCI virtio-net devices in the VM.

    :param machine: VM host
    :return: number of PCI devices in VM
    """
    PCI_CLASS_GENERIC_ETHERNET_CONTROLLER = "0200"
    return number_of_devices(machine, PCI_CLASS_GENERIC_ETHERNET_CONTROLLER)


def number_of_storage_devices(machine: Machine) -> int:
    """
    Returns the number of PCI virtio-blk devices in the VM.

    :param machine: VM host
    :return: number of PCI devices in VM
    """
    PCI_CLASS_GENERIC_STORAGE_CONTROLLER = "0180"
    return number_of_devices(machine, PCI_CLASS_GENERIC_STORAGE_CONTROLLER)


def hotplug(machine: Machine, cmd: str, expect_success: bool = True):
    """
    Hotplugs (attaches or detaches) a device and waits for the guest to
    acknowledge that.

    :param machine: The VM host
    :param cmd: virsh command to perform the detach or attach
    :param expect_success: whether the command is expected to succeed
    :return:
    """
    if cmd.startswith("virsh attach-"):
        is_attach = True
    elif cmd.startswith("virsh detach-"):
        is_attach = False
    else:
        raise RuntimeError(
            f"command is neither `virsh attach-*` nor `virsh detach-*`: `{cmd}`"
        )

    num_old = number_of_devices(machine)
    num_new_expected = -1

    match (is_attach, expect_success):
        case (True, True):
            machine.succeed(cmd)
            num_new_expected = num_old + 1
        case (True, False):
            machine.fail(cmd)
            num_new_expected = num_old
        case (False, True):
            machine.succeed(cmd)
            num_new_expected = num_old - 1
        case (False, False):
            machine.fail(cmd)
            num_new_expected = num_old

    wait_for_guest_pci_device_enumeration(machine, num_new_expected)


def hotplug_fail(machine: Machine, cmd: str):
    """
    Hotplugs (attaches or detaches) a device and expect that to fail.

    :param machine: The VM host
    :param cmd: virsh command to perform the detach or attach
    :return:
    """
    hotplug(machine, cmd, False)


def reset_system_image(machine: Machine):
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


def pci_devices_by_bdf(machine: Machine) -> dict[str, str]:
    """
    Creates a dict of all PCI devices addressable by their BDF in the VM.

    BDFs are keys, while the combination of vendor and device IDs form the
    associated value.

    :param machine: Host machine of the nested VM
    :return: BDF mapped to devices, example: {'00:00.0': '8086:0d57'}
    """
    lines = ssh(
        machine,
        "lspci -n | awk '/^[0-9a-f]{2}:[0-9a-f]{2}\\.[0-9]/{bdf=$1}{class=$3} {print bdf \",\" class}'",
    )
    out = {}
    for line in lines.splitlines():
        bdf, device_class = line.split(",")
        out[bdf] = device_class
    return out


def wait_for_guest_pci_device_enumeration(machine: Machine, new_count: int):
    """
    Block until the guest operating system has observed a PCI topology change
    (hotplug or unplug) by verifying that the number of enumerated PCI devices
    matches the expected value.

    Guest-side acknowledgment is inferred by polling the PCI device count
    (via `lspci`) until it converges to `expected_count`.

    Raises a RuntimeError if the expected PCI device count is not observed
    within the retry window, otherwise continues.

    :param machine: VM host
    :param new_count: New device count
    :return:
    """
    # retries=20 => max 2s => we expect hotplug events to be relatively quick
    wait_until_succeed(lambda: number_of_devices(machine) == new_count, 20)


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
