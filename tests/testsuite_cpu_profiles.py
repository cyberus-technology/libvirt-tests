import unittest

# Following import statement allows for proper python IDE support and proper
# nix build support. The duplicate listing of imported functions is a bit
# unfortunate, but it seems to be the best compromise. This way the python IDE
# support works out of the box in VSCode and IntelliJ without requiring
# additional IDE configuration.
try:
    from ..test_helper.test_helper import (  # type: ignore
        LibvirtTestsBase,
        initialComputeVMSetup,
        initialControllerVMSetup,
        wait_for_ssh,
    )
except Exception:
    from test_helper import (
        LibvirtTestsBase,
        initialComputeVMSetup,
        initialControllerVMSetup,
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


    def test_live_migration_statistics(self):
        """
        Test if we can properly retrieve the migration statistics via 'virsh
        domjobinfo' while a migration is running.
        The statistics are only retrievable on the sender side, not the
        receiver and only if the migration is ongoing.
        We do not check if the statistics actually contain meaningful and
        consistent data, but only for some chosen samples and entries.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-cpu-skylake.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )


def suite():
    # Test cases involving live migration sorted in alphabetical order.
    testcases = [
        LibvirtTests.test_live_migration_statistics,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
