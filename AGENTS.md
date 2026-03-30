# Instructions for Agents/LLMs

This is _libvirt-tests_, the test suite we use in the Cyberus Technology Cloud
Team to develop and test our _Cloud Hypervisor_ fork including our work on the
`ch` driver inside _libvirt_.

Project:
- We use the NixOS integration test framework to define multiple test suites
- The general model is: we spawn two QEMU VM hosts on the system that is running
  a test suite. These hosts are `controllerVM` and `computeVM`. We then spawn a
  Cloud Hypervisor VM inside the QEMU VM and optionally migrate it to the other
  QEMU VM host, depending on the test case. QEMU is transparent to Cloud
  Hypervisor and only used for a setup with two VMs and easy networkink between
  them simulating two real VM hosts.
- You find more instructions and information in `./README.md`.

Core rules:
- Correctness > micro performance optimizations
- No speculative or unclear changes
- Keep patches minimal, scoped, and relevant
- No unrelated refactoring, though low-hanging fruits may be proposed
- Do not change behavior unless explicitly required
- Refactors must preserve behavior: Behavior changes are acceptable only for
  severe bug fixes or explicit, documented requirements
- Avoid timing/sleeps to avoid flakiness: aim for robust wait mechanisms

Truthfulness and uncertainty:
- Do not invent information: no fake APIs, functions, behaviors, or
  assumptions
- If uncertain, state uncertainty explicitly and do not guess
- Do not present assumptions as facts
- Ask for clarification if requirements are incomplete
- If clarification is not possible, proceed with minimal, explicit
  assumptions

Development model:
- To run isolated test runs, comment out all test cases from the corresponding
  test suite
- Optionally, update flake inputs or let them point to local forks of the
  necessary components

Testing:
- `$ nix flake check`
- `$ nix run -L .#tests.x86_64-linux.<attribute>.driver`. Check README for all
  available test suites or look into `flake.nix`
