import unittest

# Following import statement allows for proper python IDE support and proper
# nix build support. The duplicate listing of imported functions is a bit
# unfortunate, but it seems to be the best compromise. This way the python IDE
# support works out of the box in VSCode and IntelliJ without requiring
# additional IDE configuration.
try:
    from ..test_helper.test_helper import (  # type: ignore
        LibvirtTestsBase,
        initialControllerVMSetup,
        initialComputeVMSetup,
        wait_for_ssh,
    )
except Exception:
    from test_helper import (
        LibvirtTestsBase,
        initialControllerVMSetup,
        initialComputeVMSetup,
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


class LibvirtTests(LibvirtTestsBase):  # type: ignore
    def __init__(self, methodName):
        super().__init__(methodName, controllerVM, computeVM)

    @classmethod
    def setUpClass(cls):
        start_all()
        initialControllerVMSetup(controllerVM)
        initialComputeVMSetup(computeVM)

    def test_live_migration_between_different_numa_host_configs(self):
        """
        Test that we can update the pinning of memory when migrating to a host
        on which a certain numa node is not available.

        The actual magic is using the `--xml` option of `virsh migrate` and to
        provide a domain configuration with a different numa pinning along with
        it. If it is not provided, we expect the migration to fail gracefully.
        This means, that while we expect an error, we also expect no further
        effects on the runtime, e.g. we can still migrate afterwards and none of
        the services crashes.
        """

        # Note the difference in the XML that is used for instantiation and during live migration.
        controllerVM.succeed("virsh define /etc/domain-numa-init.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)
        # We try to migrate with a NUMA config incompatible to the destination
        # host. We expect  the migration to fail gracefully.
        controllerVM.fail(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --live --p2p"
        )
        # Check that the VM is still running on the sender side and that there are no zombi VMs on the sender side
        controllerVM.succeed("virsh list | grep 'testvm' | grep 'running'")
        computeVM.fail("virsh list | grep 'testvm'")
        # Now we try to migrate with a compatible NUMA configuration. As we failed gracefully before, this migration
        # should succeed.
        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --live --p2p --xml /etc/domain-numa-update.xml"
        )
        # Check that the VM is running on the receiver side and that there are no zombi VMs on the sender side
        controllerVM.fail("virsh list | grep 'testvm'")
        computeVM.succeed("virsh list | grep 'testvm' | grep 'running'")


def suite():
    # Test cases involving live migration sorted in alphabetical order.
    testcases = [
        LibvirtTests.test_live_migration_between_different_numa_host_configs,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
