# Windows Server 2025 Images Provision

* The image originates from a standard Windows Server 2025 installation
* An SSH server is running, it was activated by executing the command below
  * The firewall is turned off as described in the basic setup
  * As a side note, Windows does not support Post Quantum KEX algorithms.

```powershell
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

* There exists a script that binds the IP address 192.186.1.2 to the MAC `52:54:00:e5:b8:01`
  * This script can be found at `C:\bind-mac.ps1`
  * It contains the following code:

```powershell
$targetMac = "52-54-00-E5-B8-01"

for ($i=0; $i -lt 10; $i++) {
    $iface = Get-NetAdapter | Where-Object {
        $_.MacAddress -eq $targetMac -and $_.Status -eq "Up"
    }
    if ($iface) { break }
    Start-Sleep -Seconds 2
}

if ($iface) {
    New-NetIPAddress -InterfaceIndex $iface.ifIndex `
        -IPAddress 192.168.1.2 -PrefixLength 24 `
        -DefaultGateway 192.168.1.1 -ErrorAction SilentlyContinue
}
```

* There is a service scheduled that runs the binding script. It was scheduled with the command below.
  * Running the script on startup is necessary as we can guarantee this way that the interface with the correct MAC receives the desired IP
  * Windows creates Interfaces in a weird way, so we cannot guarantee that the VM has the same interface as when we provisioned the image in Qemu
  * Otherwise running `bind-mac.ps1` once would be enough

```powershell
schtasks /create /tn "BindIPToMAC" `
  /tr "powershell -ExecutionPolicy Bypass -File C:\bind-mac.ps1" `
  /sc onstart /ru SYSTEM
```
