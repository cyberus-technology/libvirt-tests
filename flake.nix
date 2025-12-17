{
  description = "NixOS tests for libvirt development";

  inputs = {
    dried-nix-flakes.url = "github:cyberus-technology/dried-nix-flakes";
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-25.11";

    # Our patched libvirt for Cloud Hypervisor.
    libvirt-chv = {
      # A local path can be used for developing or testing local changes.
      url = "git+file:/home/pschuster/dev/libvirt";
      #url = "git+https://github.com/phip1611/libvirt?ref=nix-2&submodules=1";
      # url = "git+ssh://git@gitlab.cyberus-technology.de/pschuster/libvirt?ref=nix-2&submodules=1";
    };
    cloud-hypervisor-src = {
      url = "git+file:/home/pschuster/dev/cloud-hypervisor";
      # url = "github:cyberus-technology/cloud-hypervisor?ref=gardenlinux";
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
        systems = [ "x86_64-linux" ];
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
        libvirt-chv,
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
        inherit inputs;
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
          inputsFrom = builtins.attrValues self.checks;
          packages = with pkgs; [
            gitlint
          ];
        };
        packages =
          let
            # For quicker rebuilds, which we experience quite often during development
            # and prototyping,, we remove all unneeded functionality.
            libvirt-chv-testsuite = libvirt-chv.packages.libvirt-debugoptimized.overrideAttrs (old: {
              # Reduce files needed to compile. We cut the build-time in half.
              mesonFlags = old.mesonFlags ++ [
                # Disabling tests: 1500 -> 1200
                "-Dtests=disabled"
                "-Dexpensive_tests=disabled"
                # Disabling docs: 1200 -> 800
                "-Ddocs=disabled"
                # Disabling unneeded backends: 800 -> 685
                "-Ddriver_ch=enabled"
                "-Ddriver_qemu=disabled"
                "-Ddriver_bhyve=disabled"
                "-Ddriver_esx=disabled"
                "-Ddriver_hyperv=disabled"
                "-Ddriver_libxl=disabled"
                "-Ddriver_lxc=disabled"
                "-Ddriver_openvz=disabled"
                "-Ddriver_secrets=disabled"
                "-Ddriver_vbox=disabled"
                "-Ddriver_vmware=disabled"
                "-Ddriver_vz=disabled"
                # Disabling unneeded backends: 685 -> 608
                "-Dstorage_dir=disabled"
                "-Dstorage_disk=disabled"
                "-Dstorage_fs=enabled" # for netfs
                "-Dstorage_gluster=disabled"
                "-Dstorage_iscsi=disabled"
                "-Dstorage_iscsi_direct=disabled"
                "-Dstorage_lvm=disabled"
                "-Dstorage_mpath=disabled"
                "-Dstorage_rbd=disabled"
                "-Dstorage_scsi=disabled"
                "-Dstorage_vstorage=disabled"
                "-Dstorage_zfs=disabled"
                "-Dapparmor=disabled"
                "-Dwireshark_dissector=disabled"
                "-Dselinux=disabled"
                "-Dsecdriver_apparmor=disabled"
                "-Dsecdriver_selinux=disabled"
                "-Db_sanitize=leak"
                "-Db_sanitize=address,undefined"
                # Enabling the sanitizers has led to warnings about inlining macro
                # generated cleanup methods of the glib which spam the build log.
                # Ignoring and suppressing the warnings seems like the only option.
                # "warning: inlining failed in call to 'glib_autoptr_cleanup_virNetlinkMsg': call is unlikely and code size would grow [-Winline]"
                "-Dc_args=-Wno-inline"
              ];
            });
          in
          {
            # Export of the overlay'ed package
            inherit (pkgs) cloud-hypervisor;
            inherit libvirt-chv-testsuite;
            inherit nixos-image;
            chv-ovmf = pkgs.runCommand "OVMF-CLOUHDHV.fd" { } ''
              cp ${chv-ovmf.fd}/FV/CLOUDHV.fd $out
            '';
          }
          // libvirt-chv.packages;
        tests = import ./tests/default.nix {
          inherit
            pkgs
            nixos-image
            chv-ovmf
            ;
          libvirt-chv = self.packages.libvirt-chv-testsuite;
        };
      }
    );
}
