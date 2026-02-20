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
        ssh,
        wait_for_ssh,
    )
except Exception:
    from test_helper import (
        LibvirtTestsBase,
        initialComputeVMSetup,
        initialControllerVMSetup,
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
