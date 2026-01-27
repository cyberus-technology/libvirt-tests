import unittest

# Following import statement allows for proper python IDE support and proper
# nix build support. The duplicate listing of imported functions is a bit
# unfortunate, but it seems to be the best compromise. This way the python IDE
# support works out of the box in VSCode and IntelliJ without requiring
# additional IDE configuration.
try:
    from ..test_helper.test_helper import (  # type: ignore
        LibvirtTestsBase,
        allocate_hugepages,
        number_of_free_hugepages,
        initialControllerVMSetup,
        initialComputeVMSetup,
        ssh,
        wait_for_ssh,
    )
except Exception:
    from test_helper import (
        LibvirtTestsBase,
        allocate_hugepages,
        number_of_free_hugepages,
        initialControllerVMSetup,
        initialComputeVMSetup,
        ssh,
        wait_for_ssh,
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

# The VM we migrate has 2GiB of memory: 1024 * 2 MiB to cover RAM
NR_HUGEPAGES = 1024


class LibvirtTests(LibvirtTestsBase):  # type: ignore
    def __init__(self, methodName):
        super().__init__(methodName, controllerVM, computeVM)

    @classmethod
    def setUpClass(cls):
        start_all()
        allocate_hugepages(controllerVM, NR_HUGEPAGES)
        allocate_hugepages(computeVM, NR_HUGEPAGES)
        initialControllerVMSetup(controllerVM)
        initialComputeVMSetup(computeVM)

    @classmethod
    def tearDownClass(cls):
        allocate_hugepages(controllerVM, 0)
        allocate_hugepages(computeVM, 0)

    def test_live_migration_with_hugepages(self):
        """
        Test that a VM that utilizes hugepages is still using hugepages after live migration.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        self.assertEqual(
            number_of_free_hugepages(controllerVM),
            0,
            "not enough huge pages are in-use on controllerVM",
        )

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(computeVM)

        self.assertEqual(
            number_of_free_hugepages(computeVM),
            0,
            "not enough huge pages are in-use on computeVM",
        )
        self.assertEqual(
            number_of_free_hugepages(controllerVM),
            NR_HUGEPAGES,
            "not all huge pages have been freed on controllerVM",
        )

    def test_hugepages(self):
        """
        Test hugepage on-demand usage for a non-NUMA VM.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that we really use hugepages from the hugepage pool
        self.assertLess(
            number_of_free_hugepages(controllerVM),
            NR_HUGEPAGES,
            "no huge pages have been used",
        )

    def test_hugepages_prefault(self):
        """
        Test hugepage usage with pre-faulting for a non-NUMA VM.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that all huge pages are in use
        self.assertEqual(
            number_of_free_hugepages(controllerVM), 0, "not all huge pages are in use"
        )

    def test_numa_hugepages(self):
        """
        Test hugepage on-demand usage for a NUMA VM.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-numa-hugepages.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        ssh(controllerVM, "ls /sys/devices/system/node/node0")

        ssh(controllerVM, "ls /sys/devices/system/node/node1")

        # Check that we really use hugepages from the hugepage pool
        self.assertLess(
            number_of_free_hugepages(controllerVM),
            NR_HUGEPAGES,
            "no huge pages have been used",
        )

    def test_numa_hugepages_prefault(self):
        """
        Test hugepage usage with pre-faulting for a NUMA VM.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-numa-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that there are 2 NUMA nodes
        ssh(controllerVM, "ls /sys/devices/system/node/node0")

        ssh(controllerVM, "ls /sys/devices/system/node/node1")

        # Check that all huge pages are in use
        self.assertEqual(
            number_of_free_hugepages(controllerVM), 0, "not all huge pages are in use"
        )

    def free_hugepages_compute(self):
        """Frees all hugepages on the computeVM."""
        allocate_hugepages(computeVM, 0)

    def test_live_migration_with_hugepages_failure_case(self):
        """
        Test that migrating a VM with hugepages to a destination without huge pages will fail gracefully.
        """

        # This test requires no hugepages on the computeVM to be available
        self.free_hugepages_compute()

        controllerVM.succeed("virsh define /etc/domain-chv-hugepages-prefault.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.fail(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )
        wait_for_ssh(controllerVM)

        computeVM.fail("virsh list | grep testvm")


def suite():
    # Test cases sorted by their need of hugepages and in alphabetical order.
    testcases = [
        LibvirtTests.test_live_migration_with_hugepages,
        LibvirtTests.test_hugepages,
        LibvirtTests.test_hugepages_prefault,
        LibvirtTests.test_numa_hugepages,
        LibvirtTests.test_numa_hugepages_prefault,
        # Let following test run last as it deallocates hugepages on the computeVM
        LibvirtTests.test_live_migration_with_hugepages_failure_case,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
