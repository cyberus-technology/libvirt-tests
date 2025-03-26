# Module for the machine that mounts but not hosts the shared NFS volume used
# to host the VM image.
#
# This is NOT the mount point that libvirt uses. It is a mount to test that NFS
# works.

{
  config,
  lib,
  ...
}:

let
  cfg = config.livemig.nfs;
  exportedDir = "/nfs-root";
in
{
  options = {
    livemig.nfs.host = lib.mkOption {
      type = lib.types.str;
      description = "Name or IP of the NFS host";
      default = "";
      example = "192.168.123.1";
    };
  };
  config = lib.mkIf (cfg.host != "") {

    boot.supportedFilesystems = [ "nfs" ];
    systemd.mounts = [
      {
        type = "nfs";
        mountConfig = {
          Options = "noatime";
        };
        what = "${cfg.host}:${exportedDir}";
        where = "/mnt/nfs";
      }
    ];

    systemd.automounts = [
      {
        wantedBy = [ "multi-user.target" ];
        automountConfig = {
          TimeoutIdleSec = "600";
        };
        where = "/mnt/nfs";
      }
    ];
  };
}
