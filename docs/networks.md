# Host VM and Test VM Networks Overview

| IP net             | Host Interface (MAC, Name)   | Host Bridge | Guest Interface (Name) | Comment                             |
|--------------------|------------------------------|-------------|------------------------|-------------------------------------|
| `192.168.1.0/24`   | `52:54:00:e5:b8:01`, `vtap0` | `br0`       | `eth1337`              | Main network into test VM           |
| `192.168.2.0/24`   | `52:54:00:e5:b8:02`, `vtap1` | `br0`       | `eth1338`              | Hotplugged device (type `ethernet`) |
| `192.168.3.0/24`   | `52:54:00:e5:b8:03`, `tap3`  | `br3`       | `eth1339`              | Hotplugged device (type `network`)  |
| `192.168.4.0/24`   | `52:54:00:e5:b8:04`, `tap4`  | `br4`       | `eth1340`              | Hotplugged device (type `bridge`)   |
| `192.168.100.0/24` | <dynamic>          , `eth1`  | -           | -                      | Network between host VMs            |
