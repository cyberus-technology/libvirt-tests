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

    def test_cirros_with_cpu_profiles(self):
        """
        Check if the cirros image can boot when a sapphire-rapids CPU profile is used.

        Note:
        Must be run on a system with an Intel processor recent enough so Cloud Hypervisor
        can emulate a sapphire-rapids CPU profile.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-cirros-sapphire-rapids.xml")
        controllerVM.succeed("virsh start testvm")

        # Attach a network where libvirt performs DHCP as the cirros image has
        # no static IP in it. We can't use our hotplug() helper here, as it's
        # network check would fail at this point.
        controllerVM.succeed(
            "virsh attach-device testvm /etc/new_interface_type_network.xml"
        )

        wait_for_ssh(
            controllerVM,
            user="cirros",
            password="gocubsgo",
            ip="192.168.3.42",
            retries=350,
        )

    def test_ubuntu_with_cpu_profiles(self):
        controllerVM.succeed("virsh define /etc/domain-chv-ubuntu-sapphire-rapids.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(
            controllerVM,
            user="ubuntu",
            password="ubuntu",
            retries=350,
        )


def suite():
    # Test cases involving live migration sorted in alphabetical order.
    testcases = [
        LibvirtTests.test_cirros_with_cpu_profiles,
        LibvirtTests.test_ubuntu_with_cpu_profiles,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
