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
        pass

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

    def test_hotplug(self):
        # Using define + start creates a "persistant" domain rather than a transient
        controllerVM.succeed("virsh -c ch:///session define /etc/cirros-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start cirros")

        assert wait_for_ssh(controllerVM)

        num_devices_old = number_of_devices(controllerVM)

        controllerVM.succeed("qemu-img create -f raw /tmp/disk.img 100M")
        controllerVM.succeed(
            "virsh -c ch:///session attach-disk --domain cirros --target vdb --persistent --source /tmp/disk.img"
        )

        controllerVM.succeed(
            "virsh -c ch:///session attach-device --persistent cirros /etc/new_interface.xml"
        )

        num_devices_new = number_of_devices(controllerVM)

        assert num_devices_new == num_devices_old + 2

        controllerVM.succeed(
            "virsh -c ch:///session detach-disk --domain cirros --target vdb"
        )
        controllerVM.succeed(
            "virsh -c ch:///session detach-device cirros /etc/new_interface.xml"
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
        # Using define + start creates a "persistant" domain rather than a transient
        controllerVM.succeed("virsh -c ch:///session define /etc/cirros-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start cirros")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed("virsh -c ch:///session shutdown cirros")
        controllerVM.succeed("systemctl restart virtchd")

        controllerVM.succeed("virsh -c ch:///session list --all | grep 'shut off'")

        controllerVM.succeed("virsh -c ch:///session start cirros")
        controllerVM.succeed("systemctl restart virtchd")
        controllerVM.succeed("virsh -c ch:///session list | grep 'running'")

    def test_live_migration(self):
        """
        Test the live migration via virsh between 2 hosts. We want to use the
        "--p2p" flag as this is the one used by OpenStack Nova. Using "--p2p"
        results in another control flow of the migration, which is the one we
        want to test.
        We also hot-attach some devices before migrating, in order to cover
        proper migration of those devices.
        """

        controllerVM.succeed("virsh -c ch:///session define /etc/cirros-chv.xml")
        controllerVM.succeed("virsh -c ch:///session start cirros")

        assert wait_for_ssh(controllerVM)

        controllerVM.succeed(
            "virsh -c ch:///session attach-device cirros /etc/new_interface.xml"
        )
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")
        controllerVM.succeed(
            "virsh -c ch:///session attach-disk --domain cirros --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img"
        )

        for i in range(2):
            # Explicitly use IP in desturi as this was already a problem in the past
            controllerVM.succeed(
                "virsh -c ch:///session migrate --domain cirros --desturi ch+tcp://192.168.100.2/session --persistent --live --p2p"
            )
            time.sleep(5)
            assert wait_for_ssh(computeVM)
            computeVM.succeed(
                "virsh -c ch:///session migrate --domain cirros --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
            )
            time.sleep(5)
            assert wait_for_ssh(controllerVM)

    def test_numa_topology(self):
        """
        We test that a NUMA topology and NUMA tunings are correctly passed to
        Cloud Hypervisor and the VM.
        """
        controllerVM.succeed("virsh -c ch:///session define /etc/cirros-chv-numa.xml")
        controllerVM.succeed("virsh -c ch:///session start cirros")

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

        status, out = ssh(controllerVM, "lscpu | grep Thread | awk '{print $4}'")
        assert status == 0, "cmd failed"
        assert int(out) == 2, "Expect to find 2 threads per core"


def suite():
    suite = unittest.TestSuite()
    suite.addTest(LibvirtTests("test_hotplug"))
    suite.addTest(LibvirtTests("test_libvirt_restart"))
    suite.addTest(LibvirtTests("test_live_migration"))
    suite.addTest(LibvirtTests("test_numa_topology"))
    return suite


def wait_for_ssh(machine, user="cirros", password="gocubsgo", ip="192.168.1.2"):
    retries = 500
    for i in range(retries):
        print(f"Wait for ssh {i}/{retries}")
        status, _ = ssh(machine, "echo hello")
        if status == 0:
            return True
        time.sleep(1)
    return False


def ssh(machine, cmd, user="cirros", password="gocubsgo", ip="192.168.1.2"):
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
