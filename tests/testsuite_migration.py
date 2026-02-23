from functools import partial
import time
import unittest

# Following import statement allows for proper python IDE support and proper
# nix build support. The duplicate listing of imported functions is a bit
# unfortunate, but it seems to be the best compromise. This way the python IDE
# support works out of the box in VSCode and IntelliJ without requiring
# additional IDE configuration.
try:
    from ..test_helper.test_helper import (  # type: ignore
        CommandGuard,
        LibvirtTestsBase,
        VIRTIO_BLOCK_DEVICE,
        VIRTIO_ENTROPY_SOURCE,
        VIRTIO_NETWORK_DEVICE,
        hotplug,
        hotplug_fail,
        initialComputeVMSetup,
        initialControllerVMSetup,
        measure_ms,
        number_of_network_devices,
        number_of_storage_devices,
        pci_devices_by_bdf,
        ssh,
        wait_for_ssh,
        wait_until_fail,
        wait_until_succeed,
    )
except Exception:
    from test_helper import (
        CommandGuard,
        LibvirtTestsBase,
        VIRTIO_BLOCK_DEVICE,
        VIRTIO_ENTROPY_SOURCE,
        VIRTIO_NETWORK_DEVICE,
        hotplug,
        hotplug_fail,
        initialComputeVMSetup,
        initialControllerVMSetup,
        measure_ms,
        number_of_network_devices,
        number_of_storage_devices,
        pci_devices_by_bdf,
        ssh,
        wait_for_ssh,
        wait_until_fail,
        wait_until_succeed,
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

    def test_live_migration_with_hotplug_and_virtchd_restart(self):
        """
        Test that we can restart the libvirt daemon (virtchd) in between live-migrations
        and hotplugging.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")

        wait_for_ssh(controllerVM)

        hotplug(controllerVM, "virsh attach-device testvm /etc/new_interface.xml")

        num_devices_controller = number_of_network_devices(controllerVM)
        self.assertEqual(
            num_devices_controller, 2, "number of network devices should match"
        )

        num_disk_controller = number_of_storage_devices(controllerVM)
        self.assertEqual(
            num_disk_controller, 1, "number of storage devices should match"
        )

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)
        self.assertEqual(
            num_devices_compute, 2, "number of network devices should match"
        )

        controllerVM.succeed("systemctl restart virtchd")
        computeVM.succeed("systemctl restart virtchd")

        computeVM.succeed("virsh list | grep testvm")
        controllerVM.fail("virsh list | grep testvm")

        hotplug(computeVM, "virsh detach-device testvm /etc/new_interface.xml")
        hotplug(
            computeVM,
            "virsh attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img",
        )

        num_devices_compute = number_of_network_devices(computeVM)
        self.assertEqual(
            num_devices_compute, 1, "number of network devices should match"
        )

        num_disk_compute = number_of_storage_devices(computeVM)
        self.assertEqual(num_disk_compute, 2, "number of storage devices should match")

        computeVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(controllerVM)

        controllerVM.succeed("systemctl restart virtchd")
        computeVM.succeed("systemctl restart virtchd")

        computeVM.fail("virsh list | grep testvm")
        controllerVM.succeed("virsh list | grep testvm")

        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")

        num_disk_compute = number_of_storage_devices(controllerVM)
        self.assertEqual(num_disk_compute, 1, "number of storage devices should match")

    def test_live_migration(self):
        """
        Test the live migration via virsh between 2 hosts. We want to use the
        "--p2p" flag as this is the one used by OpenStack Nova. Using "--p2p"
        results in another control flow of the migration, which is the one we
        want to test.
        We also hot-attach some devices before migrating, in order to cover
        proper migration of those devices.

        This test also checks that the destination host sends out RARP packets
        to announce its new location to the network.

        We also include some negative-tests, such as migrating a non-existent VM
        must result in a gracefully handled failure.
        """

        # Test we cannot migrate a non-existent VM
        controllerVM.fail(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        hotplug(controllerVM, "virsh attach-device testvm /etc/new_interface.xml")
        controllerVM.succeed("qemu-img create -f raw /nfs-root/disk.img 100M")
        controllerVM.succeed("chmod 0666 /nfs-root/disk.img")
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --persistent --source /var/lib/libvirt/storage-pools/nfs-share/disk.img",
        )

        # We use tcpdump and tshark to check for the RARP packets.
        ethertype_rarp = "0x8035"

        def start_capture(machine):
            machine.succeed(
                f"systemd-run --unit tcpdump-mig -- bash -lc 'tcpdump -i any -w /tmp/rarp.pcap \"ether proto {ethertype_rarp}\" 2> /tmp/rarp.log'"
            )
            machine.wait_until_succeeds("grep -q 'listening on any' /tmp/rarp.log")

        def stop_capture_and_count_packets(machine):
            machine.succeed("systemctl stop tcpdump-mig")
            rarps = (
                machine.succeed(
                    f'tshark -r /tmp/rarp.pcap -Y "sll.etype == {ethertype_rarp}" -T fields -e sll.src.eth'
                )
                .strip()
                .splitlines()
            )

            # We only check whether we got rarp packets for both NICs, by
            # looking at the source MAC addresses.
            self.assertEqual(len(set(rarps)), 2, "number of rarp packets should match")

        for _ in range(2):
            start_capture(computeVM)
            # Explicitly use IP in desturi as this was already a problem in the past
            controllerVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://192.168.100.2/session --persistent --live --p2p"
            )
            wait_for_ssh(computeVM)
            stop_capture_and_count_packets(computeVM)
            # Test we cannot migrate a VM that is already migrated
            controllerVM.fail(
                "virsh migrate --domain testvm --desturi ch+tcp://192.168.100.2/session --persistent --live --p2p"
            )

            start_capture(controllerVM)
            computeVM.succeed(
                "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
            )
            wait_for_ssh(controllerVM)
            stop_capture_and_count_packets(controllerVM)
            # Test we cannot migrate a VM that is already migrated
            computeVM.fail(
                "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p"
            )

    def test_live_migration_with_hotplug(self):
        """
        Test that transient and persistent devices are correctly handled during live migrations.
        The tests first starts a VM, then attaches a persistent network device. After that, the VM
        is migrated and the new device is detached transiently. Then the VM is destroyed and restarted
        again. The assumption is that the persistent device is still present after the VM has rebooted.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface.xml --persistent",
        )

        num_devices_controller = number_of_network_devices(controllerVM)
        self.assertEqual(
            num_devices_controller, 2, "number of network devices should match"
        )

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(computeVM)

        num_devices_compute = number_of_network_devices(computeVM)
        self.assertEqual(
            num_devices_controller,
            num_devices_compute,
            "number of network devices should match",
        )
        hotplug(computeVM, "virsh detach-device testvm /etc/new_interface.xml")
        self.assertEqual(
            number_of_network_devices(computeVM),
            1,
            "number of network devices should match",
        )

        computeVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        wait_for_ssh(controllerVM)
        self.assertEqual(
            number_of_network_devices(controllerVM),
            1,
            "number of network devices should match",
        )

        controllerVM.succeed("virsh destroy testvm")

        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)
        self.assertEqual(
            number_of_network_devices(controllerVM),
            2,
            "number of network devices should match",
        )

    def test_live_migration_with_serial_tcp(self):
        """
        The test checks that a basic live migration is working with TCP serial
        configured, because we had a bug that prevented live migration in
        combination with serial TCP in the past.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-serial-tcp.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Check that port 2222 is used by cloud hypervisor
        controllerVM.succeed(
            "ss --numeric --processes --listening --tcp src :2222 | grep cloud-hyperviso"
        )

        # We define a target domain XML that changes the port of the TCP serial
        # configuration from 2222 to 2223.
        controllerVM.succeed(
            "cp /etc/domain-chv-serial-tcp.xml /tmp/domain-chv-serial-tcp.xml"
        )
        controllerVM.succeed(
            'sed -i \'s/service="2222"/service="2223"/g\' /tmp/domain-chv-serial-tcp.xml'
        )

        controllerVM.succeed(
            "virsh migrate --xml /tmp/domain-chv-serial-tcp.xml --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )
        wait_for_ssh(computeVM)

        computeVM.succeed(
            "ss --numeric --processes --listening --tcp src :2223 | grep cloud-hyperviso"
        )

    def test_live_migration_virsh_non_blocking(self):
        """
        We check if reading virsh commands can be executed even there is a live
        migration ongoing. Further, it is checked that modifying virsh commands
        block in the same case.

        Note:
        This test does some coarse timing checks to detect if commands are
        blocking or not. If this turns out to be flaky, we should not hesitate
        to deactivate the test.
        The duration of the migration is very dependent on the system the test
        runs on. We assume that our invocation of 'stress' creates enough load
        to stretch the migration duration to >10 seconds to be able to check if
        commands are blocking or non-blocking as expected.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")

        # Do migration in a screen session and detach
        controllerVM.succeed(
            "screen -dmS migrate virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        # Wait a moment to let the migration start
        time.sleep(2)

        # Check that 'virsh list' can be done without blocking
        self.assertLess(
            measure_ms(lambda: controllerVM.succeed("grep -q testvm < <(virsh list)")),
            1000,
            msg="Expect virsh list to execute fast",
        )

        # Check that modifying commands like 'virsh shutdown' block until the
        # migration has finished or the timeout hits.
        self.assertGreater(
            measure_ms(lambda: controllerVM.execute("virsh shutdown testvm")),
            3000,
            msg="Expect virsh shutdown execution to take longer",
        )

        try:
            # Turn off the stress process to let the migration finish faster
            ssh(
                controllerVM,
                "pkill -9 screen",
                extra_ssh_params="-o ConnectTimeout=3 -o TCPKeepAlive=yes -o ServerAliveInterval=2 -o ServerAliveCountMax=3",
            )
        except RuntimeError:
            # The VM might already be migrated and SSH fails. This is no
            # problem in this test scenario.
            pass

        # Wait for migration in the screen session to finish
        def migration_finished():
            status, _ = controllerVM.execute("screen -ls | grep migrate")
            return status != 0

        wait_until_succeed(migration_finished)

        computeVM.succeed("virsh list | grep testvm | grep running")

    def test_live_migration_parallel_connections(self):
        """
        We test that specifying --parallel and --parallel-connections results
        in a successful migration. We further check the Cloud Hypervisor logs
        to verify that multiple threads were used during migration.
        """

        parallel_connections = 4

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.succeed(
            f"virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections {parallel_connections}"
        )
        wait_for_ssh(computeVM)

        num_threads = controllerVM.succeed(
            'grep -c "Spawned thread to send VM memory" /var/log/libvirt/ch/testvm.log'
        ).strip()

        self.assertEqual(
            parallel_connections,
            int(num_threads),
            "number of parallel connections should match threads",
        )

    def test_live_migration_with_vcpu_pinning(self):
        """
        This tests checks that the configured vcpu affinity is still in
        use after a live migration.
        """

        controllerVM.succeed("virsh define /etc/domain-chv-numa.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        chv_pid_controller = controllerVM.succeed("pidof cloud-hypervisor").rstrip()

        tid_vcpu0_controller = controllerVM.succeed(
            f"ps -Lo tid,comm --pid {chv_pid_controller} | grep vcpu0 | awk '{{print $1}}'"
        ).rstrip()
        tid_vcpu2_controller = controllerVM.succeed(
            f"ps -Lo tid,comm --pid {chv_pid_controller} | grep vcpu2 | awk '{{print $1}}'"
        ).rstrip()

        taskset_vcpu0_controller = controllerVM.succeed(
            f"taskset -p {tid_vcpu0_controller} | awk '{{print $6}}'"
        ).rstrip()
        taskset_vcpu2_controller = controllerVM.succeed(
            f"taskset -p {tid_vcpu2_controller} | awk '{{print $6}}'"
        ).rstrip()

        self.assertEqual(
            int(taskset_vcpu0_controller, 16), 0x3, "vCPU taskset should match"
        )
        self.assertEqual(
            int(taskset_vcpu2_controller, 16), 0xC, "vCPU taskset should match"
        )

        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )
        wait_for_ssh(computeVM)

        chv_pid_compute = computeVM.succeed("pidof cloud-hypervisor").rstrip()

        tid_vcpu0_compute = computeVM.succeed(
            f"ps -Lo tid,comm --pid {chv_pid_compute} | grep vcpu0 | awk '{{print $1}}'"
        ).rstrip()
        tid_vcpu2_compute = computeVM.succeed(
            f"ps -Lo tid,comm --pid {chv_pid_compute} | grep vcpu2 | awk '{{print $1}}'"
        ).rstrip()

        taskset_vcpu0_compute = computeVM.succeed(
            f"taskset -p {tid_vcpu0_compute} | awk '{{print $6}}'"
        ).rstrip()
        taskset_vcpu2_compute = computeVM.succeed(
            f"taskset -p {tid_vcpu2_compute} | awk '{{print $6}}'"
        ).rstrip()

        self.assertEqual(
            int(taskset_vcpu0_controller, 16),
            int(taskset_vcpu0_compute, 16),
            "vCPU taskset should match",
        )
        self.assertEqual(
            int(taskset_vcpu2_controller, 16),
            int(taskset_vcpu2_compute, 16),
            "vCPU taskset should match",
        )

    def test_live_migration_kill_chv_on_sender_side(self):
        """
        Test that libvirt survives a CHV crash of the sender side during
        a live migration. We expect that libvirt does the correct cleanup
        and no VM is present on both sides.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")

        # Do migration in a screen session and detach
        controllerVM.succeed(
            "screen -dmS migrate virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

        # Wait a moment to let the migration start
        time.sleep(5)

        # Kill the cloud-hypervisor on the sender side
        controllerVM.succeed("kill -9 $(pidof cloud-hypervisor)")

        # Ensure the VM is really gone and we have no zombie VMs
        def check_virsh_list(vm):
            status, _ = vm.execute("virsh list | grep testvm > /dev/null")
            return status == 0

        wait_until_fail(lambda: check_virsh_list(controllerVM))
        wait_until_fail(lambda: check_virsh_list(computeVM))

    def test_live_migration_tls(self):
        """
        Test the TLS encrypted live migration via virsh between 2 hosts. With
        the right certificates, using IP addresses and DNS names should work,
        thus we test all combinations.
        To be extra sure we also check whether a TLS connection is established
        by checking for TLS handshakes. We should see a TLS handshake per
        connection used for the live migration.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        parallel_connections = 4
        parallel_string = f"--parallel --parallel-connections {parallel_connections}"
        for parallel in [True, False]:
            for dst_host, dst, src in [
                ("192.168.100.2", computeVM, controllerVM),
                ("controllerVM", controllerVM, computeVM),
            ]:
                # To check for established TLS connections, we count the number
                # of successful TLS handshakes.
                #
                # TLS handshakes consist of a ClientHello and a ServerHello.
                # We can capture the network traffic using tcpdump and then
                # analyze it using tshark. Because the capture can get really
                # big, we want to apply a filter that only captures TLS packets.
                #
                # Unfortunately, ClientHello packets can be really big, because
                # the ClientHello has some fields that contain lists of variable
                # size (see RFC8446). Thus, when capturing only packets that
                # look like TLS with tcpdump, the ClientHello packet may be
                # split into two packets, the second packet is not captured and
                # tshark does not see the ClientHello ... (although when you
                # look at the packet with wireshark, it looks awfully like a
                # TLS packet)
                #
                # But we know for sure that a ServerHello packet is only sent by
                # the TLS server after receiving a ClientHello. Thus, we count
                # the ServerHello packets, and to be extra sure we extract the
                # TCP stream using tshark and make sure that there is a
                # ServerHello for every TCP stream.

                # (tcp[12] & 0xf0) >> 2 gives the TCP header size, the TLS
                # header comes after that
                tls_filter = "(tcp[((tcp[12] & 0xf0) >> 2)] = 0x16)"  # filters for TLS handshake packets

                port = 49152  # first port of the libvirt default port range for live migrations
                port_filter = f"tcp port {port}"

                # To make sure tcpdump is ready to capture, we wait until it
                # reports "listening on eth1"
                dst.succeed(
                    f"systemd-run --unit tcpdump-mig -- bash -lc 'tcpdump -i eth1 -w /tmp/tls.pcap \"{port_filter} and {tls_filter}\" 2> /tmp/tls.log'"
                )
                dst.wait_until_succeeds("grep -q 'listening on eth1' /tmp/tls.log")

                src.succeed(
                    f"virsh migrate --domain testvm --desturi ch+tcp://{dst_host}/session --persistent --live --p2p --tls {parallel_string if parallel else ''}"
                )

                dst.succeed("systemctl stop tcpdump-mig")

                # tls.handshake.type == 2 filters for ServerHello. This invocation
                # gives us a string like "0\n1\n2\n3\n" and we immediately split it
                # into a list.
                server_hello = (
                    dst.succeed(
                        'tshark -r /tmp/tls.pcap -Y "tls.handshake.type == 2" -T fields -e tcp.stream'
                    )
                    .strip()
                    .split("\n")
                )

                expected = (parallel_connections + 1) if parallel else 1
                server_hellos = len(server_hello)  # count ServerHellos
                tcp_streams = len(
                    set(server_hello)
                )  # creating a set will discard duplicates.

                self.assertEqual(
                    server_hellos, expected, "number of server_hellos should match"
                )
                self.assertEqual(
                    tcp_streams, expected, "number of tcp_streams should match"
                )
                wait_for_ssh(dst)

    def test_live_migration_tls_without_certificates(self):
        """
        Test that live migration fails when TLS is requested but no
        certificates have been deployed. The assumption is that the
        migration fails, and the VM is still running on the sender side.
        Both sides should also have a non-blocking virsh.

        There are two different cases we have to test:
          1. The whole folder is missing, this case should be handled by
             libvirt.
          2. Only the files are missing. libvirt only checks whether the folder
             exists (because it doesn't know which files are necessary), thus
             Cloud Hypervisor has to handle that without a panic.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        certificate_dir = "/var/lib/libvirt/ch/pki"

        # Function to move the certificates into a temporary directory.
        def remove_certificates(remove_dir, machine):
            tmp_dir = machine.succeed("mktemp -d").strip()
            machine.succeed(f"mv {certificate_dir}/* {tmp_dir}/")
            # If remove_dir is true, we delete the files and the directory.
            # Otherwise we only delete the files but keep the directory.
            machine.succeed(f"rm -rf {certificate_dir}{'' if remove_dir else '/*'}")
            return tmp_dir

        # Function to reset the certificates.
        def reset_certs(tmp_dir, machine):
            machine.succeed(f"mkdir -p {certificate_dir}")
            machine.succeed(f"mv {tmp_dir}/* {certificate_dir}/")

        # Function check that all certificates and keys exist.
        def check_certificates(machine):
            expected_files = ["ca-cert.pem", "server-cert.pem", "server-key.pem"]
            files = machine.succeed(f"ls {certificate_dir}").strip()
            for expected_file in expected_files:
                self.assertIn(
                    expected_file,
                    files,
                    f"didn't find file '{expected_file}' in '{certificate_dir}'",
                )

        # We first check case one, then case two (see comment above).
        for remove_cert_dir in [True, False]:
            remove_certs = partial(remove_certificates, remove_cert_dir)
            # Certificates are missing on both machines.
            reset_certs_controller = partial(reset_certs, remove_certs(controllerVM))
            reset_certs_compute = partial(reset_certs, remove_certs(computeVM))
            with (
                CommandGuard(reset_certs_controller, controllerVM) as _,
                CommandGuard(reset_certs_compute, computeVM) as _,
            ):
                controllerVM.fail(
                    "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --tls"
                )
                wait_for_ssh(controllerVM)

                controllerVM.succeed("virsh list | grep 'testvm'")
                computeVM.fail("virsh list | grep 'testvm'")

            check_certificates(controllerVM)
            check_certificates(computeVM)

            # Certificates are missing only on the source machine.
            reset_certs_controller = partial(reset_certs, remove_certs(controllerVM))
            with CommandGuard(reset_certs_controller, controllerVM) as _:
                controllerVM.fail(
                    "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --tls"
                )
                wait_for_ssh(controllerVM)

                controllerVM.succeed("virsh list | grep 'testvm'")
                computeVM.fail("virsh list | grep 'testvm'")

            check_certificates(controllerVM)

            # Certificates are missing only on the target machine.
            reset_certs_compute = partial(reset_certs, remove_certs(computeVM))
            with CommandGuard(reset_certs_compute, computeVM) as _:
                controllerVM.fail(
                    "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --tls"
                )
                wait_for_ssh(controllerVM)

                controllerVM.succeed("virsh list | grep 'testvm'")
                computeVM.fail("virsh list | grep 'testvm'")

            check_certificates(computeVM)

    def test_bdf_implicit_assignment(self):
        """
        Test if all BDFs are correctly assigned in a scenario where some
        are fixed in the XML and some are assigned by libvirt.

        The domain XML we use here leaves a slot ID 0x03 free, but
        allocates IDs 0x01, 0x02 and 0x04. 0x01 and 0x02 are dynamically
        assigned by libvirt and not given in the domain XML. As 0x04 is
        the first free ID, we expect this to be selected for the first
        device we add to show that libvirt uses gaps. We add another
        disk to show that all succeeding BDFs would be allocated
        dynamically. Moreover, we show that all BDF assignments
        survive live migration.
        """
        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface_explicit_bdf.xml",
        )
        # Add a disks that receive the first free BDFs
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdb.img 5M"
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img",
        )
        controllerVM.succeed(
            "qemu-img create -f raw /var/lib/libvirt/storage-pools/nfs-share/vdc.img 5M"
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdc --source /var/lib/libvirt/storage-pools/nfs-share/vdc.img",
        )

        devices = pci_devices_by_bdf(controllerVM)
        # Implicitly added fixed to 0x01
        self.assertEqual(
            devices["00:01.0"],
            VIRTIO_ENTROPY_SOURCE,
            "device type at BDF 00:01.0 should match",
        )
        # Added by XML; dynamic BDF
        self.assertEqual(
            devices["00:02.0"],
            VIRTIO_NETWORK_DEVICE,
            "device type at BDF 00:02.0 should match",
        )
        # Add through XML
        self.assertEqual(
            devices["00:03.0"],
            VIRTIO_BLOCK_DEVICE,
            "device type at BDF 00:03.0 should match",
        )
        # Defined fixed BDF in XML; Hotplugged
        self.assertEqual(
            devices["00:04.0"],
            VIRTIO_NETWORK_DEVICE,
            "device type at BDF 00:04.0 should match",
        )
        # Hotplugged by this test (vdb)
        self.assertEqual(
            devices["00:05.0"],
            VIRTIO_BLOCK_DEVICE,
            "device type at BDF 00:05.0 should match",
        )
        # Hotplugged by this test (vdc)
        self.assertEqual(
            devices["00:06.0"],
            VIRTIO_BLOCK_DEVICE,
            "device type at BDF 00:06.0 should match",
        )

        # Check that we can reuse the same non-statically allocated BDF
        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")

        self.assertIsNone(
            pci_devices_by_bdf(controllerVM).get("00:05.0"),
            "no device should exist at BDF 00:05.0",
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img",
        )
        self.assertEqual(
            pci_devices_by_bdf(controllerVM).get("00:05.0"),
            VIRTIO_BLOCK_DEVICE,
            "device type at BDF 00:05.0 should match",
        )

        # We free slot 4 and 5 ...
        hotplug(
            controllerVM,
            "virsh detach-device testvm /etc/new_interface_explicit_bdf.xml",
        )
        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")
        self.assertIsNone(
            pci_devices_by_bdf(controllerVM).get("00:04.0"),
            "no device should exist at BDF 00:04.0",
        )
        self.assertIsNone(
            pci_devices_by_bdf(controllerVM).get("00:05.0"),
            "no device should exist at BDF 00:05.0",
        )
        # ...and expect the same disk that was formerly attached non-statically to slot 5 now to pop up in slot 4
        # through implicit BDF allocation.
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/vdb.img",
        )
        self.assertEqual(
            pci_devices_by_bdf(controllerVM).get("00:04.0"),
            VIRTIO_BLOCK_DEVICE,
            "device type at BDF 00:04.0 should match",
        )

        # Check that BDFs stay the same after migration
        devices_before_livemig = pci_devices_by_bdf(controllerVM)
        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --live --p2p"
        )
        wait_for_ssh(computeVM)
        devices_after_livemig = pci_devices_by_bdf(computeVM)
        self.assertEqual(
            devices_before_livemig,
            devices_after_livemig,
            "devices before and after migration should match",
        )

    def test_bdf_explicit_assignment(self):
        """
        Test if all BDFs are correctly assigned when binding them
        explicitly to BDFs.

        This test also shows that we can freely define the BDF that is
        given to the RNG device. Moreover, we show that all BDF
        assignments survive live migration, that allocating the same
        BDF twice fails and that we can reuse BDFs if the respective
        device was detached.

        Developer Note: This test resets the NixOS image.
        """
        controllerVM.succeed("virsh define /etc/domain-chv-static-bdf.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        hotplug(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface_explicit_bdf.xml",
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/cirros.img --address pci:0.0.17.0",
        )

        devices = pci_devices_by_bdf(controllerVM)
        self.assertEqual(
            devices["00:01.0"],
            VIRTIO_BLOCK_DEVICE,
            "device type at BDF 00:01.0 should match",
        )
        self.assertEqual(
            devices["00:02.0"],
            VIRTIO_NETWORK_DEVICE,
            "device type at BDF 00:02.0 should match",
        )
        self.assertIsNone(
            devices.get("00:03.0"), "no device should exist at BDF 00:03.0"
        )
        self.assertEqual(
            devices["00:04.0"],
            VIRTIO_NETWORK_DEVICE,
            "device type at BDF 00:04.0 should match",
        )
        self.assertEqual(
            devices["00:05.0"],
            VIRTIO_ENTROPY_SOURCE,
            "device type at BDF 00:05.0 should match",
        )
        self.assertIsNone(
            devices.get("00:06.0"), "no device should exist at BDF 00:06.0"
        )
        self.assertEqual(
            devices["00:17.0"],
            VIRTIO_BLOCK_DEVICE,
            "device type at BDF 00:17.0 should match",
        )

        # Check that BDF is freed and can be reallocated when de-/attaching a (entirely different) device
        hotplug(
            controllerVM,
            "virsh detach-device testvm /etc/new_interface_explicit_bdf.xml",
        )
        hotplug(controllerVM, "virsh detach-disk --domain testvm --target vdb")
        self.assertIsNone(
            pci_devices_by_bdf(controllerVM).get("00:04.0"),
            "no device should exist at BDF 00:04.0",
        )
        self.assertIsNone(
            pci_devices_by_bdf(controllerVM).get("00:17.0"),
            "no device should exist at BDF 00:17.0",
        )
        hotplug(
            controllerVM,
            "virsh attach-disk --domain testvm --target vdb --source /var/lib/libvirt/storage-pools/nfs-share/cirros.img --address pci:0.0.04.0",
        )
        devices_before_livemig = pci_devices_by_bdf(controllerVM)
        self.assertEqual(
            devices_before_livemig["00:04.0"],
            VIRTIO_BLOCK_DEVICE,
            "device type at BDF 00:04.0 should match",
        )

        # Adding to the same bdf twice fails
        hotplug_fail(
            controllerVM,
            "virsh attach-device testvm /etc/new_interface_explicit_bdf.xml",
        )

        # Check that BDFs stay the same after migration
        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --live --p2p"
        )
        wait_for_ssh(computeVM)
        devices_after_livemig = pci_devices_by_bdf(computeVM)
        self.assertEqual(
            devices_before_livemig,
            devices_after_livemig,
            "devices before and after migration should match",
        )

    def test_live_migration_to_self_is_rejected(self):
        """
        Test that an attempt to migrate to yourself fails.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.fail(
            "virsh migrate --domain testvm --desturi ch+tcp://controllerVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )

    def test_live_migration_non_peer2peer_is_not_supported(self):
        """
        Test that an attempt to migrate without specifying --p2p fails. The
        OpenStack driver always uses the P2P flag. As the migration functions
        very different when P2P is specified, we drop the support for non P2P
        migrations.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")

        wait_for_ssh(controllerVM)

        controllerVM.fail(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --parallel --parallel-connections 4"
        )

    def test_live_migration_after_failed_migration(self):
        """
        Test that a live migration can fail, that the VM is still usable, and
        that a new migration can succeed afterwards.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        # Stress the CH VM in order to make the migration take longer
        ssh(controllerVM, "screen -dmS stress stress -m 4 --vm-bytes 400M")

        # Next, we attempt multiple times to migrate a VM but each fails with a
        # certain variance in timing. After each failed migration, the VM must
        # still be usable and the VMM in operational state.
        times = 10
        for i in range(times):
            print(f"Attempt {i + 1}/{times}")
            # Ensure there is no Cloud Hypervisor running
            computeVM.fail("ps aux | grep -E '[c]loud-hypervisor'")
            # We use non-parallel transport to avoid timing issues.
            controllerVM.succeed(
                "screen -dmS migrate virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
            )
            # Wait for receiving VMM to come up
            wait_until_succeed(
                lambda: computeVM.execute("ps aux | grep -E '[c]loud-hypervisor'")[0]
                == 0
            )

            # Wait some time to interrupt the migration at some point
            time.sleep(i)

            # Check VM is still responsive
            out = ssh(controllerVM, "echo -n Hello Cyberus!")
            self.assertEqual(out, "Hello Cyberus!", "VM should still be responsive")

            computeVM.succeed("kill -9 $(pidof cloud-hypervisor)")
            # Wait until `virsh migrate` returns (finished its cleanup)
            wait_until_fail(
                lambda: controllerVM.execute("screen -ls | grep migrate")[0] == 0
            )

            # Check VM is still responsive
            out = ssh(controllerVM, "echo -n Hello Cyberus!")
            self.assertEqual(out, "Hello Cyberus!", "VM should still be responsive")

        # Ensure migration can now continue quickly
        ssh(controllerVM, "pkill -9 screen")

        # Test that a new migration indeed works
        controllerVM.succeed(
            "virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p --parallel --parallel-connections 4"
        )
        wait_for_ssh(computeVM)

    def test_live_migration_statistics(self):
        """
        Test if we can properly retrieve the migration statistics via 'virsh
        domjobinfo' while a migration is running.
        The statistics are only retrievable on the sender side, not the
        receiver and only if the migration is ongoing.
        We do not check if the statistics actually contain meaningful and
        consistent data, but only for some chosen samples and entries.
        """

        controllerVM.succeed("virsh define /etc/domain-chv.xml")
        controllerVM.succeed("virsh start testvm")
        wait_for_ssh(controllerVM)

        out = controllerVM.succeed("virsh domjobinfo --rawstats testvm")

        # No actual stats when no migration is running
        self.assertNotIn(
            "memory_total:",
            out,
            "should not have domjobinfo metric when no migration is outgoing",
        )

        # Stress the CH VM in order to make the migration take longer
        ssh(controllerVM, "screen -dmS stress stress -m 2 --vm-bytes 1000M")

        # Do migration in a screen session and detach
        controllerVM.succeed(
            "screen -dmS migrate virsh migrate --domain testvm --desturi ch+tcp://computeVM/session --persistent --live --p2p"
        )

        # Wait a moment to let the migration start
        time.sleep(1)

        out = controllerVM.succeed("virsh domjobinfo --rawstats testvm")

        for entry in [
            "downtime:",
            "memory_iteration:",
            "memory_total:",
            "time_elapsed",
        ]:
            self.assertIn(entry, out, "should have domjobinfo metric")

        # Receiving side does not offer statistics about the incoming migration
        out = computeVM.succeed("virsh domjobinfo --rawstats testvm")

        # No actual stats when no migration is running
        self.assertNotIn(
            "memory_total:",
            out,
            "should not have domjobinfo metric when no migration is outgoing",
        )

        try:
            # Turn off the stress process to let the migration finish faster
            ssh(
                controllerVM,
                "pkill -9 screen",
                extra_ssh_params="-o ConnectTimeout=3 -o TCPKeepAlive=yes -o ServerAliveInterval=2 -o ServerAliveCountMax=3",
            )
        except RuntimeError:
            # The VM might already be migrated and SSH fails. This is no
            # problem in this test scenario.
            pass

        # Wait for migration in the screen session to finish
        def migration_finished():
            status, _ = controllerVM.execute("screen -ls | grep migrate")
            return status != 0

        wait_until_succeed(migration_finished)


def suite():
    # Test cases involving live migration sorted in alphabetical order.
    testcases = [
        LibvirtTests.test_bdf_explicit_assignment,
        LibvirtTests.test_bdf_implicit_assignment,
        LibvirtTests.test_live_migration,
        LibvirtTests.test_live_migration_after_failed_migration,
        LibvirtTests.test_live_migration_kill_chv_on_sender_side,
        LibvirtTests.test_live_migration_non_peer2peer_is_not_supported,
        LibvirtTests.test_live_migration_parallel_connections,
        LibvirtTests.test_live_migration_statistics,
        LibvirtTests.test_live_migration_tls,
        LibvirtTests.test_live_migration_tls_without_certificates,
        LibvirtTests.test_live_migration_to_self_is_rejected,
        LibvirtTests.test_live_migration_virsh_non_blocking,
        LibvirtTests.test_live_migration_with_hotplug,
        LibvirtTests.test_live_migration_with_hotplug_and_virtchd_restart,
        LibvirtTests.test_live_migration_with_serial_tcp,
        LibvirtTests.test_live_migration_with_vcpu_pinning,
    ]

    suite = unittest.TestSuite()
    for testcaseMethod in testcases:
        suite.addTest(LibvirtTests(testcaseMethod.__name__))
    return suite


runner = unittest.TextTestRunner()
if not runner.run(suite()).wasSuccessful():
    raise Exception("Test Run unsuccessful")
