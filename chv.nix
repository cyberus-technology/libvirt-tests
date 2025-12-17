{
  # from flake inputs
  craneLib,
  cloud-hypervisor-src,
  rustToolchain,
  # from nixpkgs,
  lib,
  openssl,
  pkg-config,
  # other
  cloud-hypervisor-meta,
}:
let
  # Crane lib with proper Rust toolchain
  craneLib' = craneLib.overrideToolchain rustToolchain;

  commonArgs =
    let
      src = craneLib'.cleanCargoSource cloud-hypervisor-src;
    in
    {
      inherit src;
      # Since Nov 2025 (v50), Cloud Hypervisor has a virtual manifest and the
      # main package was moved into a sub directory.
      cargoToml = "${src}/cloud-hypervisor/Cargo.toml";
      meta = cloud-hypervisor-meta;

      # Pragmatic release profile with debug-ability and faster
      # compilation times in mind.
      env = {
        CARGO_PROFILE_RELEASE_DEBUG_ASSERTIONS = "true";
        CARGO_PROFILE_RELEASE_OPT_LEVEL = 2;
        CARGO_PROFILE_RELEASE_OVERFLOW_CHECKS = "true";
        CARGO_PROFILE_RELEASE_LTO = "no";
      };

      nativeBuildInputs = [
        pkg-config
      ];
      buildInputs = [
        openssl
      ];
      # Fix build. Reference:
      # - https://github.com/sfackler/rust-openssl/issues/1430
      # - https://docs.rs/openssl/latest/openssl/
      OPENSSL_NO_VENDOR = true;
    };

  # Downloaded and compiled dependencies.
  cargoArtifacts = craneLib'.buildDepsOnly (
    commonArgs
    // {
      doCheck = false;
    }
  );

  cargoPackageKvm = craneLib'.buildPackage (
    commonArgs
    // {
      inherit cargoArtifacts;
      # Don't execute tests here. We want this in a dedicated step.
      doCheck = false;
      cargoExtraArgs = "--features kvm";
    }
  );
in
cargoPackageKvm
