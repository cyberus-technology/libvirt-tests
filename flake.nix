{
  description = "NixOS tests for libvirt development";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-25.05";

    # A local path can be used for developing or testing local changes. Make
    # sure the submodules in a local libvirt checkout are populated.
    libvirt-src = {
      # url = "git+file:<path/to/libvirt>?submodules=1";
      url = "git+https://github.com/cyberus-technology/libvirt?ref=gardenlinux&submodules=1";
      # url = "git+ssh://git@gitlab.cyberus-technology.de/cyberus/cloud/libvirt?ref=managedsave-fix&submodules=1";
      flake = false;
    };
    cloud-hypervisor-src = {
      # url = "git+file::<path/to/cloud-hypervisor>";
      url = "github:cyberus-technology/cloud-hypervisor?ref=gardenlinux";
      flake = false;
    };
    edk2-src = {
      url = "git+https://github.com/cyberus-technology/edk2?ref=gardenlinux&submodules=1";
      flake = false;
    };
    # Nix tooling to build cloud-hypervisor.
    crane.url = "github:ipetkov/crane/master";
    # Get proper Rust toolchain, independent of pkgs.rustc.
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      # Keep list sorted:
      cloud-hypervisor-src,
      crane,
      edk2-src,
      flake-utils,
      libvirt-src,
      nixpkgs,
      rust-overlay,
      ...
    }:
    flake-utils.lib.eachSystem [ "x86_64-linux" ] (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [
            (_final: prev: {
              cloud-hypervisor = pkgs.callPackage ./chv.nix {
                inherit cloud-hypervisor-src;
                craneLib = crane.mkLib pkgs;
                rustToolchain = rust-bin.stable.latest.default;
                cloud-hypervisor-meta = prev.cloud-hypervisor.meta;
              };
            })
          ];
        };
        rust-bin = (rust-overlay.lib.mkRustBin { }) pkgs;

        chv-ovmf = pkgs.OVMF-cloud-hypervisor.overrideAttrs (_old: {
          version = "cbs";
          src = edk2-src;
        });

        nixos-image' =
          (pkgs.callPackage ./images/nixos-image.nix { inherit nixpkgs; }).config.system.build.isoImage;

        nixos-image =
          pkgs.runCommand "nixos.iso"
            {
              nativeBuildInputs = [ pkgs.coreutils ];
            }
            ''
              # The image has a non deterministic name, so we make it
              # deterministic.
              cp ${nixos-image'}/iso/*.iso $out
            '';
      in
      {
        checks =
          let
            fs = pkgs.lib.fileset;
            cleanSrc = fs.toSource {
              root = ./.;
              fileset = fs.gitTracked ./.;
            };
            deadnix =
              pkgs.runCommand "deadnix"
                {
                  nativeBuildInputs = [ pkgs.deadnix ];
                }
                ''
                  deadnix -L ${cleanSrc} --fail
                  mkdir $out
                '';
            pythonFormat =
              pkgs.runCommand "python-format"
                {
                  nativeBuildInputs = with pkgs; [ ruff ];
                }
                ''
                  cp -r ${cleanSrc}/. .
                  ruff format --check ./tests
                  mkdir $out
                '';
            pythonLint =
              pkgs.runCommand "python-lint"
                {
                  nativeBuildInputs = with pkgs; [ ruff ];
                }
                ''
                  cp -r ${cleanSrc}/. .
                  ruff check ./tests
                  mkdir $out
                '';
            typos =
              pkgs.runCommand "spellcheck"
                {
                  nativeBuildInputs = [ pkgs.typos ];
                }
                ''
                  cd ${cleanSrc}
                  typos .
                  mkdir $out
                '';
            all = pkgs.symlinkJoin {
              name = "combined-checks";
              paths = [
                deadnix
                pythonFormat
                pythonLint
                typos
              ];
            };
          in
          {
            inherit
              all
              deadnix
              pythonFormat
              pythonLint
              typos
              ;
            default = all;
          };
        formatter = pkgs.nixfmt-tree;
        devShells.default = pkgs.mkShellNoCC {
          inputsFrom = builtins.attrValues self.checks.${pkgs.system};
          packages = with pkgs; [
          ];
        };
        packages = {
          # Export of the overlay'ed package
          inherit (pkgs) cloud-hypervisor;
          inherit chv-ovmf;
        };
        tests = pkgs.callPackage ./tests/default.nix { inherit libvirt-src nixos-image chv-ovmf; };
      }
    );
}
