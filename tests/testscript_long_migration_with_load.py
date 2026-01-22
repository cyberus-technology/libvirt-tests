# Following import statement allows for proper python IDE support and proper
# nix build support. The duplicate listing of imported functions is a bit
# unfortunate, but it seems to be the best compromise. This way the python IDE
# support works out of the box in VSCode and IntelliJ without requiring
# additional IDE configuration.
try:
    from ..test_helper.test_helper import (  # type: ignore
        ssh,
        wait_for_ssh,
    )
except Exception:
    from test_helper import (
        ssh,
        wait_for_ssh,
    )
import unittest

# pyright: reportPossiblyUnboundVariable=false

# Following is required to allow proper linting of the python code in IDEs.
# Because certain functions like start_all() and certain objects like computeVM
# or other machines are added by Nix, we need to provide certain stub objects
# in order to allow the IDE to lint the python code successfully.
if "start_all" not in globals():
    from test_helper.test_helper.nixos_test_stubs import (  # type: ignore
        start_all,
        computeVM,
        controllerVM,
    )


class LibvirtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_all()
        controllerVM.wait_for_unit("multi-user.target")
        computeVM.wait_for_unit("multi-user.target")
        controllerVM.succeed("cp /etc/nixos.img /nfs-root/")
        controllerVM.succeed("chmod 0666 /nfs-root/nixos.img")

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

        # Destroy any remaining huge page allocations.
        controllerVM.succeed("echo 0 > /proc/sys/vm/nr_hugepages")
        computeVM.succeed("echo 0 > /proc/sys/vm/nr_hugepages")

        # Remove any remaining vm logs.
        controllerVM.succeed("rm -f /tmp/*.log")
        computeVM.succeed("rm -f /tmp/*.log")

    def test_live_migration_long_running_with_load(self):
        """
        This test performs 500 back-and-forth live migrations in a row.
        During live-migration, the VM is under memory load with a working set
        of roughly 1.6GiB.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")

        run_loops = 500
        for i in range(run_loops):
            print(f"Run {i + 1}/{run_loops}")

            controllerVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
            )
            wait_for_ssh(computeVM)

            computeVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p --parallel --parallel-connections 4"
            )
            wait_for_ssh(controllerVM)


def suite():
    suite = unittest.TestSuite()
    suite.addTest(LibvirtTests("test_live_migration_long_running_with_load"))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
