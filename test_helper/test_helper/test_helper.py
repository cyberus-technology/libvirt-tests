import ipaddress
import json
import time
import weakref

try:
    from .nixos_test_stubs import Machine  # type: ignore
except ImportError:
    pass

from test_driver.machine import Machine  # type: ignore
from typing import Callable, List, Any


class CommandGuard:
    """
    Guard that executes a command after being garbage collected.

    Some test might need to run addition cleanup when exiting/failing.
    This guard ensures that these cleanup function are run
    """

    def __init__(self, command, machine):
        """
        Initializes the guard with a command to work on a given machine.

        :param command: Function that runs a command on the given machine
        :type command: Callable (Machine)
        :param machine: Virtual machine to send the command from
        :type machine: Machine
        """

        self._finilizer = weakref.finalize(self, command, machine)  # pyright: ignore[reportCallIssue]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._finilizer()


def measure_ms(func: Callable[[], Any]) -> float:
    """
    Measure the execution time of a given function in ms.
    """
    start = time.time()
    func()
    return (time.time() - start) * 1000


def wait_until_succeed(func: Callable[[], bool], retries: int = 600) -> None:
    """
    Waits for the command to succeed.
    After each failure, it waits 100ms.

    :param func: Function that is expected to succeed. It must not throw
           exceptions but translate its exceptions to True/False.
    :param retries: Amount of retries.
    """
    for _i in range(retries):
        if func():
            return
        time.sleep(0.1)
    raise RuntimeError("function didn't succeed")


def wait_until_fail(func: Callable[[], bool], retries: int = 600) -> None:
    """
    Waits for the command to fail.
    After each success, it waits 100ms.

    :param func: Function that is expected to fail. It must not throw
           exceptions but translate its exceptions to True/False.
    :param retries: Amount of retries.
    """
    for i in range(retries):
        if not func():
            return
        time.sleep(0.1)
    raise RuntimeError("function didn't fail")


def wait_for_host_shares_ipv4_network(
    machine: Machine, ip: str = "192.168.1.2", retries=20
):
    """
    Wait until the host has an IPv4 address in the same /24 network as the
    given IP address.

    This is used in tests that dynamically create TAP devices. Since udev and
    systemd-networkd configure interfaces asynchronously, the host may need a
    short grace period before the correct IPv4 address is assigned. To avoid
    race conditions, the check is retried a small amount of times.

    Under normal conditions, this returns immediately. If the condition is not
    met after all retries, a RuntimeError is raised and includes the output of
    `ip a` for debugging.

    :param machine: Host
    :param ip: IP to wait for
    :param retries: Amount of retries
    """

    try:
        wait_until_succeed(
            lambda: ip_in_local_192_168_net24(machine, ip), retries=retries
        )
    except RuntimeError as e:
        _, output = machine.execute("ip a")
        msg = f"Host doesn't have an IP in the same IPv4 network as {ip}!\nip a:\n{output}"
        raise RuntimeError(msg) from e


def wait_for_ping(
    machine: Machine, ip: str = "192.168.1.2", retries: int = 200
) -> None:
    """
    Waits for the VM to become pingable.

    Calls `wait_for_host_shares_ipv4_network()` beforehand.

    :param machine: VM host
    :param ip: IP to ping from `machine`
    :param retries: number of retries
    :raises RuntimeError: If the IP could not be pinged after `retries` times.
    """

    wait_for_host_shares_ipv4_network(machine, ip)

    for i in range(retries):
        print(f"Checking ping to {ip} ({i + 1}/{retries}) ...")
        status, _ = machine.execute(f"ping -c 1 -W 1 {ip}")
        if status == 0:
            return
        time.sleep(0.2)

    raise RuntimeError(f"{ip} does not respond to pings after {retries} attempts")


def wait_for_ssh(
    machine: Machine,
    user: str = "root",
    password: str = "root",
    ip: str = "192.168.1.2",
    retries: int = 100,
) -> None:
    """
    Waits for the VM to become accessible via SSH.

    Calls `wait_for_ping()` beforehand.

    :param machine: VM host
    :param user: user for SSH login
    :param password: password for SSH login
    :param ip: SSH host to log into
    :param retries: Retries for the SSH. Note that this adds on top of the
           retries that are done in `wait_for_ping()`.
    """

    # We first check pings, before we connect to the SSH daemon.
    wait_for_ping(machine, ip)

    for i in range(retries):
        print(f"Wait for ssh {i}/{retries}")
        try:
            ssh(
                machine,
                "echo hello",
                user,
                password,
                ip,
                # 1: we checked ping above already
                # 2: we want to prevent unnecessary log clutter
                ping_check=False,
            )
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"Could not establish SSH connection to {ip}")


def ssh(
    machine: Machine,
    cmd: str,
    user: str = "root",
    password: str = "root",
    ip: str = "192.168.1.2",
    ping_check: bool = True,
) -> str:
    """
    Runs the specified command in the Cloud Hypervisor VM via SSH.

    The specified machine is used as SSH jump host.

    :param machine: Machine to run SSH on
    :param cmd: The command to execute via SSH
    :param user: user for SSH login
    :param password: password for SSH login
    :param ip: SSH host to log into
    :param ping_check: whether the function should check first if the VM is
           pingable
    """

    # Check VM is still pingable and we didn't lose the network.
    # This way, we prevent spammy logs.
    if ping_check:
        # One retry is fine as tests gracefully wait for pings+ssh before
        # calling this function.
        wait_for_ping(machine, ip, retries=1)

    # And here we check if the guest also responds via SSH.
    status, out = machine.execute(
        f"sshpass -p {password} ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no {user}@{ip} {cmd}"
    )
    if status != 0:
        raise RuntimeError(f"failed to execute cmd in VM: `{cmd}`")
    return out


def number_of_devices(machine: Machine, filter: str = "") -> int:
    """
    Returns the number of PCI devices in the VM.

    :param filter: Optional filter for the PCI device, e.g., vendor ID or device class.
    :param machine: VM host
    :return: number of PCI devices in VM
    :return: output from command
    """
    if filter == "":
        cmd = "lspci | wc -l"
    else:
        cmd = f"lspci -n | grep {filter} | wc -l"
    out = ssh(machine, cmd)
    return int(out)


def number_of_network_devices(machine: Machine) -> int:
    """
    Returns the number of PCI virtio-net devices in the VM.

    :param machine: VM host
    :return: number of PCI devices in VM
    """
    PCI_CLASS_GENERIC_ETHERNET_CONTROLLER = "0200"
    return number_of_devices(machine, PCI_CLASS_GENERIC_ETHERNET_CONTROLLER)


def number_of_storage_devices(machine: Machine) -> int:
    """
    Returns the number of PCI virtio-blk devices in the VM.

    :param machine: VM host
    :return: number of PCI devices in VM
    """
    PCI_CLASS_GENERIC_STORAGE_CONTROLLER = "0180"
    return number_of_devices(machine, PCI_CLASS_GENERIC_STORAGE_CONTROLLER)


def hotplug(machine: Machine, cmd: str, expect_success: bool = True) -> None:
    """
    Hotplugs (attaches or detaches) a device and waits for the guest to
    acknowledge that.

    :param machine: The VM host
    :param cmd: virsh command to perform the detach or attach
    :param expect_success: whether the command is expected to succeed
    :return:
    """
    if cmd.startswith("virsh attach-"):
        is_attach = True
    elif cmd.startswith("virsh detach-"):
        is_attach = False
    else:
        raise RuntimeError(
            f"command is neither `virsh attach-*` nor `virsh detach-*`: `{cmd}`"
        )

    num_old = number_of_devices(machine)
    num_new_expected = -1

    match (is_attach, expect_success):
        case (True, True):
            machine.succeed(cmd)
            num_new_expected = num_old + 1
        case (True, False):
            machine.fail(cmd)
            num_new_expected = num_old
        case (False, True):
            machine.succeed(cmd)
            num_new_expected = num_old - 1
        case (False, False):
            machine.fail(cmd)
            num_new_expected = num_old

    wait_for_guest_pci_device_enumeration(machine, num_new_expected)


def hotplug_fail(machine: Machine, cmd: str) -> None:
    """
    Hotplugs (attaches or detaches) a device and expect that to fail.

    :param machine: The VM host
    :param cmd: virsh command to perform the detach or attach
    :return:
    """
    hotplug(machine, cmd, False)


def reset_system_image(machine: Machine) -> None:
    """
    Replaces the (possibly modified) system image with its original
    image.

    This helps avoid situations where a VM hangs during boot after the
    underlying disk’s BDF was changed, since OVMF may store NVRAM
    entries that reference specific BDF values.
    """
    machine.succeed(
        "rsync -aL --no-perms --inplace --checksum /etc/nixos.img /nfs-root/nixos.img"
    )


def pci_devices_by_bdf(machine: Machine) -> dict[str, str]:
    """
    Creates a dict of all PCI devices addressable by their BDF in the VM.

    BDFs are keys, while the combination of vendor and device IDs form the
    associated value.

    :param machine: Host machine of the nested VM
    :return: BDF mapped to devices, example: {'00:00.0': '8086:0d57'}
    :rtype: dict[str, str]
    """
    lines = ssh(
        machine,
        "lspci -n | awk '/^[0-9a-f]{2}:[0-9a-f]{2}\\.[0-9]/{bdf=$1}{class=$3} {print bdf \",\" class}'",
    )
    out = {}
    for line in lines.splitlines():
        bdf, device_class = line.split(",")
        out[bdf] = device_class
    return out


def wait_for_guest_pci_device_enumeration(machine: Machine, new_count: int) -> None:
    """
    Block until the guest operating system has observed a PCI topology change
    (hotplug or unplug) by verifying that the number of enumerated PCI devices
    matches the expected value.

    Guest-side acknowledgment is inferred by polling the PCI device count
    (via `lspci`) until it converges to `expected_count`.

    Raises a RuntimeError if the expected PCI device count is not observed
    within the retry window, otherwise continues.

    :param machine: VM host
    :param new_count: New device count
    :return:
    """
    # retries=20 => max 2s => we expect hotplug events to be relatively quick
    wait_until_succeed(lambda: number_of_devices(machine) == new_count, 20)


def number_of_free_hugepages(machine: Machine) -> int:
    """
    Returns the number of free hugepages on the given machine.

    :param machine: VM host
    :return: Number of free hugepages
    """
    _, out = machine.execute("awk '/HugePages_Free/ { print $2; exit }' /proc/meminfo")
    return int(out)


def allocate_hugepages(machine: Machine, nr_hugepages: int) -> None:
    """
    Allocates the given amount of hugepages on the given machine, and checks
    whether the allocation was successful.

    Raises a RuntimeError if the amount of available hugepages after allocation
    is below the number of expected hugepages.

    :param machine: VM host
    :param nr_hugepages: The amount of desired hugepages
    :return:
    """
    machine.succeed(f"echo {nr_hugepages} > /proc/sys/vm/nr_hugepages")

    # To make sure that allocating the hugepages doesn't just take a moment,
    # we check few times.
    wait_until_succeed(lambda: number_of_free_hugepages(machine) == nr_hugepages, 10)


def get_local_192_168_net24_networks(machine: Machine) -> List[ipaddress.IPv4Network]:
    """
    Discover local IPv4 interface networks that match all the following:
      - IPv4 only
      - Prefix length exactly /24
      - Network is within 192.168.0.0/16

    :param machine: Machine to execute the SSH command on.
    """
    status, result = machine.execute("ip -j a")
    assert status == 0

    interfaces: list[dict[str, object]] = json.loads(result)
    networks: List[ipaddress.IPv4Network] = []

    for iface in interfaces:
        addr_info = iface.get("addr_info")
        if not isinstance(addr_info, list):
            continue

        for addr in addr_info:
            if not isinstance(addr, dict):
                continue

            family = addr.get("family")
            ip = addr.get("local")
            prefix = addr.get("prefixlen")

            if (
                family == "inet"
                and isinstance(ip, str)
                and isinstance(prefix, int)
                and prefix == 24
            ):
                network = ipaddress.IPv4Network(f"{ip}/24", strict=False)

                if network.network_address in ipaddress.IPv4Network("192.168.0.0/16"):
                    networks.append(network)

    return sorted(networks)


def ip_in_local_192_168_net24(machine: Machine, ip: str) -> bool:
    """
    Checks if the given IPv4 address belongs to one of the machine's local
    192.168.x.0/24 networks.

    The function enumerates all local /24 networks in the private
    192.168.0.0/16 range configured on the machine's network interfaces and
    checks whether the provided IP address is contained in any of them.

    This is primarily intended as a sanity check to verify that the host
    is still on a network that can directly reach a guest VM via a
    192.168.x.x address (e.g. after network reconfiguration, hotplug,
    or unintended interface changes).

    :param machine: Machine on which local network interfaces are inspected.
    :param ip: Target IPv4 address expected to be reachable via a local /24
               192.168.x.0 network.
    :return: Whether the host shares a local IPv4 network with the given IP.
    """
    target = ipaddress.IPv4Address(ip)

    if not target.is_private or not target.exploded.startswith("192.168."):
        raise RuntimeError(f"invalid IP: {ip} / {target}")

    networks = get_local_192_168_net24_networks(machine)
    for network in networks:
        if target in network:
            return True

    return False


def parse_devices_from_dom_def(machine: Machine, path: str) -> dict[str, str]:
    """
    Parses `devices` from a domain XML given by `path` on `machine` and returns them in a dict.

    The dict returned contains the device PCI slot as keys and an identification string of a device as value. The string
    differs between device types.

    :param path: Location of the domain definition on `machine`
    :param machine: Host on which the domain definition is located
    :return: dict[str, str] = ['<PCI slot in hex>' : '<info:about:device>']

    :raises RuntimeError: If we couldn't find the domain XML on the target machine
    """
    import xml.etree.ElementTree as ET

    command = "cat " + path
    status, cat_result = machine.execute(command)
    if status != 0:
        raise RuntimeError(
            f"unable to retrieve domain config from {path} on machine {machine}"
        )

    root = ET.fromstring(cat_result)
    result: dict[str, str] = dict()
    # Use ".//devices" because in persistent conf, `devices` is direct child and in transient config it's one more level
    # of nesting.
    for device in root.find(".//devices") or []:
        value = ""
        value += device.tag
        match device.tag:
            case "disk":
                source = device.find("source")
                if source is not None:
                    value += ":" + source.get("file", "").strip()
                target = device.find("target")
                if target is not None:
                    value += ":" + target.get("dev", "").strip()
            case "interface":
                value += ":" + device.attrib.get("type", "")
                mac = device.find("mac")
                if mac is not None:
                    value += ":" + mac.get("address", "")
                target = device.find("target")
                if target is not None:
                    value += ":" + target.get("dev", "").strip()
            case "rng":
                backend = device.find("backend")
                if backend is not None:
                    value += ":" + (backend.text or "").strip()
        address = device.find("address")
        if address is not None:
            if address.get("type") == "pci":
                slot = address.get("slot") or ""
                if slot != "":
                    result[slot] = (value or "").strip()
    return result
