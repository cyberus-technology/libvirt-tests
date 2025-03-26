# Module for the machine that hosts the shared NFS volume used to host the VM
# image.

{
  ...
}:

let
  exportedDir = "/nfs-root";
in
{
  config = {
    services.nfs.server.enable = true;
    services.nfs.server.exports = ''
      ${exportedDir}  *(rw,fsid=0,rw,sync,no_subtree_check)
    '';

    # Create directory on start
    systemd.tmpfiles.rules = [
      "d ${exportedDir} 1777 nobody nogroup 999d"
    ];

    # https://docs.portworx.com/portworx-enterprise/operations/operate-kubernetes/storage-operations/create-pvcs/open-nfs-ports
    networking.firewall.allowedTCPPorts = [
      111
      2049
      20048
    ];

    # Bind mount: mirror directory in file system
    fileSystems."${exportedDir}" = {
      device = "/mnt/nfs";
      options = [ "bind" ];
    };
  };
}
