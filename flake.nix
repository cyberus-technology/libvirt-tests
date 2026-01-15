{
  description = "NixOS tests for libvirt development";

  inputs = {
    dried-nix-flakes.url = "github:cyberus-technology/dried-nix-flakes";
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-25.11";

    # A local path can be used for developing or testing local changes. Make
    # sure the submodules in a local libvirt checkout are populated.
    libvirt-src = {
      # url = "git+file:<path/to/libvirt>?submodules=1";
      url = "git+https://github.com/cyberus-technology/libvirt?ref=gardenlinux&submodules=1";
      # url = "git+ssh://git@gitlab.cyberus-technology.de/cyberus/cloud/libvirt?ref=managedsave-fix&submodules=1";
      flake = false;
    };
    cloud-hypervisor-src = {
      # url = "git+file:<path/to/cloud-hypervisor>";
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
    fcntl-tool = {
      url = "github:phip1611/fcntl-tool";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs:
    let
      dnf = (inputs.dried-nix-flakes.for inputs).override {
        # Expose only platforms that the most restrictive set of packages supports.
        systems =
          let
            # The `x86_64-linux` attribute is used arbitrarily to access lib and the derivation's attributes.
            pkgs = inputs.nixpkgs.legacyPackages.x86_64-linux;
            inherit (pkgs) lib;
            intersectAll =
              lists: builtins.foldl' lib.intersectLists (builtins.head lists) (builtins.tail lists);
          in
          intersectAll [
            pkgs.cloud-hypervisor.meta.platforms
            pkgs.OVMF-cloud-hypervisor.meta.platforms
          ];
      };
      inherit (dnf)
        exportOutputs
        ;
    in
    exportOutputs (
      {
        self,
        # Keep list sorted:
        cloud-hypervisor-src,
        crane,
        edk2-src,
        fcntl-tool,
        libvirt-src,
        nixpkgs,
        rust-overlay,
        ...
      }:
      let
        pkgs = nixpkgs.legacyPackages.appendOverlays [
          (_final: prev: {
            fcntl-tool = fcntl-tool.packages.default;
            cloud-hypervisor = pkgs.callPackage ./chv.nix {
              inherit cloud-hypervisor-src;
              craneLib = crane.mkLib pkgs;
              rustToolchain = rust-bin.stable.latest.default;
              cloud-hypervisor-meta = prev.cloud-hypervisor.meta;
            };
          })
        ];

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
                  ruff format --check .
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
            pythonTypes =
              pkgs.runCommand "python-types"
                {
                  nativeBuildInputs = with pkgs; [ pyright ];
                }
                ''
                  pyright ${cleanSrc}/tests
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
              pythonTypes
              typos
              ;
            default = all;
          };
        formatter = pkgs.nixfmt-tree;
        devShells.default = pkgs.mkShellNoCC {
          inputsFrom = builtins.attrValues self.checks;
          packages = with pkgs; [
            gitlint
          ];
        };
        packages = {
          # Export of the overlay'ed package
          inherit (pkgs) cloud-hypervisor;
          inherit nixos-image;
          chv-ovmf = pkgs.runCommand "OVMF-CLOUHDHV.fd" { } ''
            cp ${chv-ovmf.fd}/FV/CLOUDHV.fd $out
          '';
        };
        tests = import ./tests/default.nix {
          inherit
            pkgs
            libvirt-src
            nixos-image
            chv-ovmf
            ;
        };
      }
    );
}
