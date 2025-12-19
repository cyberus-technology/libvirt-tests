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

  commonArgs = {
    meta = cloud-hypervisor-meta;
    src = craneLib'.cleanCargoSource cloud-hypervisor-src;

    # Pragmatic release profile with debug-ability and faster
    # compilation times in mind.
    env = {
      CARGO_PROFILE_RELEASE_DEBUG_ASSERTIONS = "true";
      CARGO_PROFILE_RELEASE_OPT_LEVEL = 2;
      CARGO_PROFILE_RELEASE_OVERFLOW_CHECKS = "true";
      CARGO_PROFILE_RELEASE_LTO = "no";

      # Fix build. Reference:
      # - https://github.com/sfackler/rust-openssl/issues/1430
      # - https://docs.rs/openssl/latest/openssl/
      OPENSSL_NO_VENDOR = true;
    };

    nativeBuildInputs = [
      pkg-config
    ];
    buildInputs = [
      openssl
    ];
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
      # Don't execute tests here. Too expensive for local development with
      # frequent rebuilds + little benefit.
      doCheck = false;
      cargoExtraArgs = "--features kvm";
    }
  );
in
cargoPackageKvm
