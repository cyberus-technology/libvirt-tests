# Libvirt NixOS Tests

A minimal set of NixOS integration tests for validating specific Libvirt
features and supporting libvirt development.

These tests provide a convenient environment for:

- Running automated checks against Libvirt.
- Testing patches to virtualization backends (e.g., Cloud Hypervisor) by running
  the libvirt test suite in a reproducible NixOS VM environment.

## Running Tests

_These tests utilize the NixOS integration test framework
(`<nixpkgs>/nixos/lib/testing`). Each test is a Bash script build by Nix and is
available in interactive mode (attribute `.driverInteractive`) as well as in
non-interactive mode (attribute `.driver`). For just running tests and seeing
test results, the non-interactive mode is fine. For interactive debugging,
please consider using the interactive mode. In interactive mode, you can type
`test_script()` into the Python REPL to run the test cases._

Build and run the default set of test cases:

```bash
$ nix run -L .#tests.x86_64-linux.default.driver
```

It might happen that the integration test runs out of resources when the user's
tmp directory space is too small. You can try to mitigate this by setting
`XDG_RUNTIME_DIR=/tmp/libvirt` before invoking the test script.

### Long-running Tests

If you want to perform a long-running migration series with a VM that is under
heavy memory load use:

```bash
$ nix run -L .#tests.x86_64-linux.long_migration_with_load.driver
```

## Using a Custom Libvirt or Cloud Hypervisor

To test against a specific version or local build, you should update your
`flake.nix` to refer to the new input, for example:

`libvirt-src.url = "git+file:/home/pschuster/dev/libvirt?submodules=1";`


### SSH into the VMs

To access the QEMU VMs, you can run

- `ssh -o StrictHostKeyChecking=no root@localhost -p 2222` for the
  ***controllerVM***, and
- `ssh -o StrictHostKeyChecking=no root@localhost -p 3333` for the
  ***computeVM***.

Inside one of those VMs, you can use

`ssh -o StrictHostKeyChecking=no root@192.168.1.2`

with password `root` to access the Cloud Hypervisor VM (***testvm***).

To directly access the Cloud Hypervisor VM, you can run

`ssh -o StrictHostKeyChecking=no -J root@localhost:2222 root@192.168.1.2`.

## More Documentation
- [VM networks overview](./docs/networks.md)
