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
        Machine,
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

    def test_live_migration_with_cpu_profile(self):
        """
        Check if the live migration works when the skylake CPU profile is used.
        The nixos test should make sure that controllerVM and computeVM use
        different CPU generations, to really test we are able to migrate across
        CPU generations.

        Note:
        Must be run on a system with an Intel processor recent enough so QEMU
        can emulate the Icelake-Server CPU profile.
        """

        print("Note: This test can only run on Intel hardware!")

        def test_cycle(src: Machine, dst: Machine):
            src.succeed("virsh define /etc/domain-chv-cpu-skylake.xml")
            src.succeed("virsh start testvm")
            wait_for_ssh(src)

            run_loops = 2
            for i in range(run_loops):
                print(f"Run {i + 1}/{run_loops}")
                src.succeed(
                    f"virsh migrate --domain testvm --desturi ch+tcp://{dst.name}/session --persistent --live --p2p --parallel --parallel-connections 4"
                )
                wait_for_ssh(dst)

                dst.succeed(
                    f"virsh migrate --domain testvm --desturi ch+tcp://{src.name}/session --persistent --live --p2p --parallel --parallel-connections 4"
                )
                wait_for_ssh(src)

            src.succeed("virsh shutdown testvm")
            src.succeed("virsh undefine testvm")

        # Check creating the CHV VM on both, the newer and the older CPU
        # generation and then migrate to the other
        test_cycle(controllerVM, computeVM)
        test_cycle(computeVM, controllerVM)


def suite():
    # Test cases involving live migration sorted in alphabetical order.
    testcases = [
        LibvirtTests.test_live_migration_with_cpu_profile,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
