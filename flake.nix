{
  description = "NixOS tests for libvirt development";

  inputs = {
    dried-nix-flakes.url = "github:cyberus-technology/dried-nix-flakes";
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-25.11";

    # A local path can be used for developing or testing local changes. Make
    # sure the submodules in a local libvirt checkout are populated.
    # libvirt-src.url = "git+file:<path/to/libvirt>?submodules=1";
    libvirt.url = "git+https://github.com/cyberus-technology/libvirt?ref=gardenlinux&submodules=1";
    libvirt.inputs.cloud-hypervisor.follows = "cloud-hypervisor";
    libvirt.inputs.nixpkgs.follows = "nixpkgs";

    # cloud-hypervisor.url = "git+file:<path/to/cloud-hypervisor>";
    cloud-hypervisor.url = "github:cyberus-technology/cloud-hypervisor?ref=gardenlinux";
    cloud-hypervisor.inputs.nixpkgs.follows = "nixpkgs";

    edk2-src.url = "git+https://github.com/cyberus-technology/edk2?ref=gardenlinux&submodules=1";
    edk2-src.flake = false;

    fcntl-tool.url = "github:phip1611/fcntl-tool";
    fcntl-tool.inputs.nixpkgs.follows = "nixpkgs";
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
        cloud-hypervisor,
        edk2-src,
        fcntl-tool,
        libvirt,
        nixpkgs,
        ...
      }:
      let
        pkgs = nixpkgs.legacyPackages.appendOverlays [
          (_final: prev: {
            fcntl-tool = fcntl-tool.packages.default;
            # Debug optimized build, suited for quicker rebuilds with
            # reasonable good performance.
            cloud-hypervisor = cloud-hypervisor.packages.default.overrideAttrs (old: {
              env = (old.env or { }) // {
                CARGO_PROFILE_RELEASE_DEBUG_ASSERTIONS = "true";
                CARGO_PROFILE_RELEASE_OPT_LEVEL = 2;
                CARGO_PROFILE_RELEASE_OVERFLOW_CHECKS = "true";
                CARGO_PROFILE_RELEASE_LTO = "thin";
              };
            });
            python3Packages = prev.python3Packages.overrideScope (
              _: _: {
                inherit test-helper;
              }
            );
          })
        ];

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

        test-helper = pkgs.callPackage ./test_helper.nix {
          inherit nixos-test-driver;
          inherit (pkgs.python3Packages) buildPythonPackage setuptools;
        };

        # The nixos python test-driver is currently not exported, but we
        # require it for our test helper lib to get all required type
        # information.
        nixos-test-driver = pkgs.callPackage "${pkgs.path}/nixos/lib/test-driver/default.nix" { };
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
                  ruff check ${cleanSrc}/test_helper
                  ruff check ${cleanSrc}/tests
                  mkdir $out
                '';
            pythonTypes =
              pkgs.runCommand "python-types"
                {
                  nativeBuildInputs = with pkgs; [
                    pyright
                    test-helper
                  ];
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
                  # By cd'ing first, we prevent that typos complains about
                  # weird path names (Nix store).
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
          shellHook =
            # We need our `test_helper` Python library for the NixOS integration
            # tests, which are Python projects themselves. For this reason, we
            # assemble a Python toolchain that includes this package. We however
            # also want full convenience for local development flows.
            #
            # The very same Nix Python toolchain is also used by the Nix
            # development shell. When developers run `nix run #<test>.driver`,
            # the Python process executes in the host environment of the caller
            # and resolves modules via `PYTHONPATH` - from the caller who might
            # have opened a Nix development shell. In that situation,
            # `test_helper` will be imported from the likely outdated location
            # in PYTHONPATH rather than from the local (potentially modified)
            # files.
            #
            # This results in a confusing and very poor developer experience
            # where an outdated version of `test_helper` is used even though
            # local changes exist. To ensure the local version always takes
            # precedence, we explicitly prepend the local path to `PYTHONPATH`.
            ''
              export PYTHONPATH=$PWD/test_helper/test_helper:$PYTHONPATH
            '';
        };
        # We export all artifacts that we also have in the tests.
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
            nixos-image
            chv-ovmf
            ;
          libvirt = libvirt.packages.libvirt-debugoptimized;
        };
      }
    );
}
