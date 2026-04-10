from enum import Enum
import unittest

# Following import statement allows for proper python IDE support and proper
# nix build support. The duplicate listing of imported functions is a bit
# unfortunate, but it seems to be the best compromise. This way the python IDE
# support works out of the box in VSCode and IntelliJ without requiring
# additional IDE configuration.
try:
    from ..test_helper.test_helper import (  # type: ignore
        LibvirtTestsBase,
        hotplug,
        initialComputeVMSetup,
        initialControllerVMSetup,
        wait_for_ssh,
    )
except Exception:
    from test_helper import (
        LibvirtTestsBase,
        hotplug,
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


# Config options for single connection/multiple connections
class Connections(Enum):
    SINGLE_CONNECTION = ""
    MULTIPLE_CONNECTIONS = "--parallel --parallel-connections 4"


# Config options for tls/no tls
class Tls(Enum):
    WITH_TLS = "--tls"
    WITHOUT_TLS = ""


def test_migration(
    sender: Machine, receiver: Machine, connections: Connections, tls: Tls
):
    """
    The test implementation itself. This test
    - starts a VM
    - hotplugs an additional NIC
    - hotplgs a disk
    - executes the configured migration
    - checks that SSH via both NICs works
    - checks that unplugging the disk works
    """
    sender.succeed("virsh define /etc/domain-chv.xml")
    sender.succeed("virsh start testvm")

    wait_for_ssh(sender)

    # Attach some devices
    hotplug(sender, "virsh attach-device testvm /etc/new_interface.xml")
    wait_for_ssh(sender, ip="192.168.2.2")

    sender.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
    sender.succeed("chmod 0666 /nfs-root/disk.img")

    sender.succeed("virsh list | grep testvm")
    receiver.fail("virsh list | grep testvm")

    hotplug(
        sender,
        "virsh attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img",
    )

    # Assemble the migration command
    dst_host = receiver.name
    migration_command = f"virsh migrate --domain testvm --desturi ch+tcp://{dst_host}/session --persistent --live --p2p {tls.value} {connections.value}"

    sender.succeed(migration_command)

    wait_for_ssh(receiver)
    wait_for_ssh(receiver, ip="192.168.2.2")
    hotplug(receiver, "virsh detach-disk --domain testvm --target vdb")

    sender.fail("virsh list | grep testvm")
    receiver.succeed("virsh list | grep testvm")


class LibvirtTests(LibvirtTestsBase):  # type: ignore
    def __init__(self, methodName):
        super().__init__(methodName, controllerVM, computeVM)

    @classmethod
    def setUpClass(cls):
        start_all()
        initialControllerVMSetup(controllerVM)
        initialComputeVMSetup(computeVM)

    def test_migrate_single_connection_no_tls(self):
        """Migration with a single connection and no TLS."""
        test_migration(
            controllerVM,
            computeVM,
            Connections.SINGLE_CONNECTION,
            Tls.WITHOUT_TLS,
        )

    def test_migrate_multiple_connections_no_tls(self):
        """Migration with multiple connections and no TLS."""
        test_migration(
            controllerVM, computeVM, Connections.MULTIPLE_CONNECTIONS, Tls.WITHOUT_TLS
        )

    def test_migrate_single_connection_with_tls(self):
        """Migration with a single connection and TLS."""
        test_migration(
            controllerVM, computeVM, Connections.SINGLE_CONNECTION, Tls.WITH_TLS
        )

    def test_migrate_multiple_connections_with_tls(self):
        """Migration with multiple connections and TLS."""
        test_migration(
            controllerVM, computeVM, Connections.MULTIPLE_CONNECTIONS, Tls.WITH_TLS
        )


def suite():
    # Test cases sorted in alphabetical order.
    testcases = [
        LibvirtTests.test_migrate_multiple_connections_no_tls,
        LibvirtTests.test_migrate_multiple_connections_with_tls,
        LibvirtTests.test_migrate_single_connection_no_tls,
        LibvirtTests.test_migrate_single_connection_with_tls,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
