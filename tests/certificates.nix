{ pkgs, ... }:
let
  caTemplate = pkgs.writeText "ca.info" ''
    cn = Cyberus Livemig Testing
    ca
    cert_signing_key
  '';

  # rustls expects the dns_name to be set, just the cn is not enough. When
  # using an IP address as the url (or desturi), the ip_address field must be
  # set.
  serverTemplate =
    cn: ip:
    pkgs.writeText "server.info" ''
      organization = Cyberus Livemig Testing
      cn = ${cn}
      tls_www_server
      signing_key
      dns_name = ${cn}
      ip_address = ${ip}
    '';

  tlsCA = pkgs.runCommand "migration-tls-ca" { buildInputs = [ pkgs.gnutls ]; } ''
    mkdir -p $out

    certtool --generate-privkey \
             --key-type rsa \
             --bits 4096 \
             --outfile $out/ca-key.pem

    certtool --generate-self-signed \
             --load-privkey $out/ca-key.pem \
             --template ${caTemplate} \
             --outfile $out/ca-cert.pem
  '';

  mkHostCert =
    cn: ip:
    pkgs.runCommand "migration-${cn}-tls-material"
      {
        buildInputs = [ pkgs.gnutls ];
      }
      ''
        mkdir -p $out

        certtool --generate-privkey \
                 --key-type rsa \
                 --bits 4096 \
                 --outfile $out/server-key.pem

        certtool --generate-certificate \
                 --load-ca-certificate ${tlsCA}/ca-cert.pem \
                 --load-ca-privkey ${tlsCA}/ca-key.pem \
                 --load-privkey $out/server-key.pem \
                 --template ${serverTemplate cn ip} \
                 --outfile $out/server-cert.pem
      '';

in
{
  inherit tlsCA mkHostCert;
}
