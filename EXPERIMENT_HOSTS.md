# Experiment hosts and virtual machines

This is the persistent operations reference for the two concurrent ns-3
experiment VMs.  Read it before launching, recovering, or combining a remote
campaign.  The values below were last verified on 2026-08-07.

## Direct guest access

Both guests expose SSH on host port `30022`, and the current local public key
is authorized on both:

```bash
ssh -p 30022 jingweili@10.120.16.105
ssh -p 30022 jingweili@10.120.17.30
```

The same port is safe because it belongs to a different physical host in each
case.  Direct access is preferred; a `ProxyJump` through the physical host is
not required.

| Property | Original VM | Cloned VM |
| --- | --- | --- |
| Physical-host SSH | `jingweili@10.120.16.105` | `jingweili@10.120.17.30` |
| Physical hostname | `bld` | `bld` |
| Host logical CPUs | 64 | 96 |
| Host RAM observed | 125 GiB | 503 GiB |
| Libvirt domain | `wifi-exp-f2e354c` | `wifi-exp-17-30` |
| Domain UUID | `c834d45f-e485-4667-a415-39b65e757c73` | `33d98129-aeb3-4b22-9498-5b17290854d2` |
| Autostart | enabled | enabled |
| Guest hostname | `wifi-exp-f2e354c` | `wifi-exp-17-30` |
| Guest machine ID | `c834d45fe4854667a41539b65e757c73` | `33d98129aeb34b2294985b17290854d2` |
| Guest vCPUs | 64 | 64 |
| Guest RAM | 48 GiB | 48 GiB |
| Guest MAC | `52:54:00:fb:9d:1c` | `52:54:00:17:30:01` |
| Guest NAT address | `192.168.122.140` | `192.168.122.140` |
| Host SSH port | `30022` | `30022` |
| ED25519 host-key fingerprint | `SHA256:FhMo/kiZkrU9C9cfa32tCAgva4hePd3qraz3ADWll0M` | `SHA256:ENp2XS2ZiQn8oUseTuhUT7VqCbCpDhjCH60GeU0jdzg` |

The repeated guest NAT address is intentional.  Each physical host owns an
independent libvirt `default` NAT network, so the two `192.168.122.0/24`
namespaces do not meet.

## VM and port-forward management

The `jingweili` account belongs to the `kvm` and `libvirt` groups on both
physical hosts.  Routine VM inspection and lifecycle operations do not need
`sudo`:

```bash
ssh jingweili@10.120.16.105 \
  'virsh -c qemu:///system dominfo wifi-exp-f2e354c'
ssh jingweili@10.120.17.30 \
  'virsh -c qemu:///system dominfo wifi-exp-17-30'
```

Use `virsh shutdown DOMAIN` and `virsh start DOMAIN` when a guest lifecycle
operation is necessary.  Do not reboot either physical host merely to restart
a guest.

Each physical host uses a systemd socket proxy from `0.0.0.0:30022` to its
guest's `192.168.122.140:22`:

| Host | Socket unit | Service unit |
| --- | --- | --- |
| `10.120.16.105` | `wifi-exp-f2e354c-ssh.socket` | `wifi-exp-f2e354c-ssh.service` |
| `10.120.17.30` | `wifi-exp-17-30-ssh.socket` | `wifi-exp-17-30-ssh.service` |

The cloned guest uses a static netplan address at `.140`; its libvirt DHCP
configuration also reserves `.140` for `52:54:00:17:30:01`.  If that address
or MAC changes, update the guest netplan, libvirt network host entry, and
socket-proxy destination together.

## Common simulation checkout

At clone verification, both guests had this checkout:

```text
/home/jingweili/wifi_streaming_qualification_47e1996
```

Both resolved to simulation-project commit
`47e19962420bb7623784bc91b0c0d40fbf462b35` and ns-3 upstream commit
`d2add90b452d600cfb4859baed8e9ea633519447`.  On both VMs,
`./ns3 run "streaming-experiment --help"` completed with no build work.  The
executable on both was
`build/contrib/wifi-streaming/examples/ns3.48-streaming-experiment-default`,
with SHA-256
`1138e2b47047e20b6e142ffab983ba09c0d8ae5eb82c631efb17370fc28e95d9`.

Do not assume the checkouts remain identical after development.  Before every
two-host campaign, record and compare at least:

```bash
git rev-parse HEAD
git status --short
sha256sum \
  build/contrib/wifi-streaming/examples/ns3.48-streaming-experiment-default
```

Adjust the executable path if the ns-3 build layout changes.

## Clone provenance

The clone was made without stopping the original physical host or VM:

1. The idle original guest flushed its filesystem with `sync`.
2. Libvirt atomically pivoted the running VM to a temporary external qcow2
   overlay.
3. The now-immutable 50 GiB base and seed ISO transferred directly from
   `10.120.16.105` to `10.120.17.30` at about 111 MB/s.
4. Both physical hosts independently hashed the transferred files.
5. `qemu-img check` reported no errors on the destination image.
6. The original overlay was block-committed and pivoted back into its base,
   then the unreferenced 6.88 MiB temporary volume was deleted.

Transfer-point identities:

- qcow2 file size: `50275024896` bytes;
- qcow2 SHA-256:
  `6e50831dbe9a8d2394d74c8b71e85353a92f9cc8ccde677b7adde88613199cc8`;
- seed ISO file size: `374784` bytes;
- seed ISO SHA-256:
  `423d6a249df22164234d674b0cb014a78522352e6bac5e8f0309e85697536c4e`.

Disk locations:

- original:
  `/home/jingweili/vms/wifi-exp-f2e354c/wifi-exp-f2e354c.qcow2`;
- clone:
  `/home/jingweili/vms/wifi-exp-17-30/wifi-exp-f2e354c.qcow2`.

The qcow2 hash describes the immutable transfer point.  The original image
continued changing after its overlay was committed, so its current hash is
not expected to remain equal to the clone.

The cloned guest received an independent domain UUID, machine ID, hostname,
MAC, and SSH host-key set.  Its copied SSH host keys were moved to
`/root/ssh-host-keys-pre-clone` for rollback.  User `authorized_keys` was
retained, and batch-mode login from the current local key was verified after
host-key rotation.

## Splitting campaigns safely

Each guest exposes 64 vCPUs, so a two-host campaign can schedule up to two
independent 64-worker waves concurrently.  Split work by complete frozen
manifest units rather than allowing both hosts to claim from one mutable
directory.

For every distributed campaign:

1. Pin identical project, ns-3, executable, configuration, model, and
   validator identities on both VMs.
2. Assign disjoint run IDs or complete paired/scenario units to each host.
3. Use separate result roots and per-host manifests.
4. Preserve and retrieve valid output from either host before diagnosing a
   failed run.
5. Strictly validate every run independently before merging manifests.
6. Reject duplicate run IDs, missing paired arms, mixed build identities, and
   checksum conflicts during the merge.
7. Analyze and plot only the closed merged manifest, while retaining the two
   source manifests for provenance.

At setup time both guest root filesystems were 77% used, with about 12 GiB
free.  The original physical host also had only about 53 GiB free.  Check
`df -h` on both the physical host and guest before a large campaign, and fetch
valid evidence before deleting rebuildable caches or old results.

## Quick concurrent health check

```bash
ssh -p 30022 jingweili@10.120.16.105 \
  'hostname; nproc; uptime; df -h /'
ssh -p 30022 jingweili@10.120.17.30 \
  'hostname; nproc; uptime; df -h /'
```

Expected hostnames are `wifi-exp-f2e354c` and `wifi-exp-17-30`; both should
report 64 processors.  If port `30022` accepts TCP but SSH hangs, compare the
guest address with the corresponding socket-proxy destination before changing
firewall or libvirt state.
