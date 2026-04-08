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
        ssh,
        FORBIDDEN_ARCHITECTURAL_MSRS,
        FORBIDDEN_NON_ARCHITECTURAL_MSRS,
        BENIGN_FORBIDDEN_MSRS,
    )
except Exception:
    from test_helper import (
        LibvirtTestsBase,
        initialComputeVMSetup,
        initialControllerVMSetup,
        wait_for_ssh,
        ssh,
        FORBIDDEN_ARCHITECTURAL_MSRS,
        FORBIDDEN_NON_ARCHITECTURAL_MSRS,
        BENIGN_FORBIDDEN_MSRS,
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

    def test_forbidden_msrs_inaccessible(self):
        """
        Test that MSRs known to be forbidden for every non-host CPU profile
        cannot be accessed by the guest.

        CHV forbids MSRs by applying MSR filters that deny MSRs outside of the
        range of MSRs the chosen CPU profile explicitly permits with a few
        exceptions that are implicitly permitted. The exceptions are MSRs that
        have special treatment in KVM such as for example the x2APIC related
        ones.

        Note: This test needs to run on a CPU that fulfills the respective
        requirements for the CPU profiles used in this test (as of time writing
        this, that is Qemu's Icelake-Server-v7 profile).
        """
        print(
            "Note: This test can only run on hardware compatible to the Icelake-Server-v7 CPU profile!"
        )

        def test_cycle(machine: Machine):
            machine.succeed("virsh define /etc/domain-chv-cpu-skylake.xml")
            machine.succeed("virsh start testvm")
            wait_for_ssh(machine)
            msr_output = list()
            command = "msr -1 --numeric-msrs"
            msrs_to_check = (
                FORBIDDEN_ARCHITECTURAL_MSRS | FORBIDDEN_NON_ARCHITECTURAL_MSRS
            ) - BENIGN_FORBIDDEN_MSRS
            for msr_id in msrs_to_check:
                command = " ".join([command, f"{msr_id}"])
            # Don't spam stderr with MSRs that we could not access (which is the good case)
            command = " ".join([command, "2>/dev/null"])
            cmd_output = ssh(machine, command)
            # cmd_output will contain a line for each MSR that `msr` could access. Each line will start with the
            # respective MSRs address in Hex, either followed by the respective value (1) if `msr` doesn't know the MSR
            # in detail, or by a detailed list of the respective MSR's bits (2). So we have one of the following outputs
            # for each accessed MSR:
            # 1) 0xc001001a = 0x00000000d0000000
            # 2) 0xc0010019:
            #       V (Valid) = false (0)
            #       PhysBase  = 0x0 (0)
            #
            # Note: For each accessed MSR the respective address is part of the output in any case.
            for msr_id in msrs_to_check:
                # As each MSR that was accessed contains the address as stated above, we simply can check if the Hex
                # representation is contained in the command output. If so, we now that `msr` was able to access the
                # respective MSR.
                # We collect offending MSRs to later report on them.
                if f"0x{msr_id:08x}" in cmd_output:
                    msr_output.append(msr_id)
            # We want to print the offending MSR indices in case of an error for easier debugging
            if len(msr_output) != 0:
                for msr in msr_output:
                    print(f"Could access forbidden msr: 0x{msr:08x}")
                raise RuntimeError(
                    f"Failed: {len(msr_output)} of total {len(msrs_to_check)} forbidden MSRs were accessed!"
                )

            machine.succeed("virsh shutdown testvm")
            machine.succeed("virsh undefine testvm")

        # Check creating the CHV VM on both, the newer and the older CPU
        test_cycle(controllerVM)
        test_cycle(computeVM)


def suite():
    # Test cases involving live migration sorted in alphabetical order.
    testcases = [
        LibvirtTests.test_live_migration_with_cpu_profile,
        LibvirtTests.test_forbidden_msrs_inaccessible,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
