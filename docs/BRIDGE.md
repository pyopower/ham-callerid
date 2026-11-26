# Ham CallerID bridge for AllStarLink ⇄ DMR (DVSwitch)

`hcid-usrp-proxy` adds caller identification to an existing AllStarLink ⇄ DMR bridge built with
DVSwitch (Analog_Bridge + MMDVM_Bridge). It is a single Python 3 process (standard library only,
~25 MB RAM, negligible CPU) placed **between** the pieces you already run. Nothing in ASL,
DVSwitch or the DMR server has to be patched.

```
before:  ASL (chan_usrp) ⇄ Analog_Bridge ⇄ MMDVM_Bridge ⇄ DMR master
after:   ASL (chan_usrp) ⇄ [hcid-usrp-proxy] ⇄ Analog_Bridge ⇄ MMDVM_Bridge ⇄ [hcid-usrp-proxy] ⇄ DMR master
                    USRP side                                          HBP side (transparent relay)
```

## What it does

**ASL → DMR**
- Delays the ASL audio a little (default 400 ms), looks for a Ham CallerID burst, removes it
  from the audio and puts the callsign in the **DMR Talker Alias inside the voice frames**
  (embedded LC, FLCO 4-7, alternating with the group LC like a radio does): `EA1ABC/ASL`.
  The alias can enter mid-call, as soon as the burst is recognised (~0.4 s).
- Without a burst, it asks app_rpt over AMI which link is keyed:
  EchoLink station → `EA1ABC/EL` (callsign validated by EchoLink),
  IAX client with a callsign → `EA1ABC/ASL`, another AllStar node → `ASL 12345`,
  otherwise a fixed text (`--unknown-ta`).
- The DMR **source ID stays the bridge's own ID**: the burst is self-declared, so it is never
  turned into somebody's DMR ID (an opt-in `--real-id` exists for future signed bursts).
  Use a bridge/system ID as `gatewayDmrId`, not a person's ID.

**DMR → ASL**
- Analog_Bridge (`exportMetadata = true`) announces every DMR call with its ID and callsign;
  the proxy inserts a Ham CallerID burst with that callsign at the start and end of the audio
  towards ASL, so clients and nodes with a decoder (e.g. Mobile ASL Client) show who is talking.

Servers that honour the source's Talker Alias (e.g. ADN SYSTEMS v2 with
`TALKER_ALIAS_MODE: both`) forward it unchanged; any network that relays the voice frames
as they are carries it to the radios.

## Requirements

- AllStarLink node with a `chan_usrp` link to DVSwitch Analog_Bridge (`rxchannel = USRP/host:rx:tx`).
- DVSwitch Analog_Bridge ≥ 1.6 and MMDVM_Bridge, one pair per bridged network.
- Python ≥ 3.8 on the same host as Analog_Bridge.
- Optional: AMI access to the ASL Asterisk (for EchoLink / source node aliases).
- Senders of the burst: Mobile ASL Client (Android) or any implementation of the
  [specification](SPEC.md) (see `c/hcid.h`).

## Install (example)

The example uses the ports of a typical setup; adapt them to yours.

| | before | after |
|---|---|---|
| ASL sends USRP to | Analog_Bridge `34102` | **proxy** `34102` (unchanged for ASL) |
| Analog_Bridge `[USRP] rxPort / txPort` | 34102 / 32102 | **34202 / 32202** |
| Analog_Bridge `[USRP] address` | ASL's address | **127.0.0.1** |
| MMDVM_Bridge `[DMR Network] Address / Port` | master:62031 | **127.0.0.1:62142** |

1. Copy the repository (only `hamcid/` is needed) to `/opt/hcid`.
2. Back up and edit `Analog_Bridge.ini` (`[USRP]`) and `MMDVM_Bridge.ini` (`[DMR Network]`)
   as in the table. Optionally set `gatewayDmrId` to a bridge/system ID.
3. Optional AMI: put the manager secret in a file readable only by the service user,
   e.g. `/opt/hcid/ami.secret` (mode 600).
4. systemd unit `/etc/systemd/system/hcid-proxy.service`:

```ini
[Unit]
Description=Ham CallerID between ASL and Analog_Bridge
After=network-online.target docker.service
Before=analog_bridge.service mmdvm_bridge.service

[Service]
User=hcid
WorkingDirectory=/opt/hcid
ExecStart=/usr/bin/python3 -m hamcid.usrp_proxy \
    --listen-asl 172.18.0.1:34102 --asl 172.18.0.2:32102 \
    --listen-ab 127.0.0.1:32202 --ab 127.0.0.1:34202 --ab-tlv 127.0.0.1:31110 \
    --gateway-id 1000001 --tg 213 --ts 2 --delay 400 \
    --hbp-listen 127.0.0.1:62142 --hbp-master master.example.org:62031 \
    --origin ASL "--unknown-ta=ASL 12345" \
    --ami 172.18.0.2:5038,admin,/opt/hcid/ami.secret --node 12345 --bridge-node 1932
Restart=always
MemoryMax=80M

[Install]
WantedBy=multi-user.target
```

5. `systemctl enable --now hcid-proxy`, then restart Analog_Bridge and MMDVM_Bridge.
   MMDVM_Bridge must log *Logged into the master successfully: 127.0.0.1:62142*.

**Rollback**: stop the service and restore the two `.ini` backups.

## Options

| Option | Meaning |
|---|---|
| `--listen-asl` / `--asl` | where ASL sends its USRP / where to send USRP back to ASL |
| `--listen-ab` / `--ab` | Analog_Bridge USRP ports (its `txPort` / `rxPort`) |
| `--ab-tlv` | Analog_Bridge TLV port (`[AMBE_AUDIO] rxPort`), only for `--real-id` |
| `--gateway-id`, `--tg`, `--ts` | Analog_Bridge `gatewayDmrId`, talkgroup and slot |
| `--hbp-listen` / `--hbp-master` | transparent HBP relay between MMDVM_Bridge and the master (needed for the Talker Alias) |
| `--origin` | suffix that marks the origin in the alias (`EA1ABC/ASL`) |
| `--unknown-ta` | alias when the talker is unknown |
| `--ami host:port,user,secretfile`, `--node`, `--bridge-node` | ask app_rpt which link is keyed (EchoLink / IAX / node) |
| `--delay` | ASL → DMR audio delay in ms (time to find and remove the burst), default 300, 400 recommended |
| `--drop-src id[,id…]` | needs the HBP relay: calls from these DMR source IDs are not passed to ASL (e.g. the network's system ID used for beacons and announcements). Empty by default |
| `--dmra` | also send HBP `DMRA` packets. **Off by default**: HBlink masters answer them with NAK and the peer reconnects |
| `--real-id`, `--allow` | put the callsign's real DMR ID (not recommended until bursts are signed) |

## Verify

The service log shows each decision:

```
ASL→DMR: ráfaga EA1ABC (n=143)
HBP: Talker Alias «EA1ABC/ASL» en la llamada a0679d12 (ráfaga)
ASL: transmite el EA2XYZ
HBP: Talker Alias «EA2XYZ/EL» en la llamada 5b89d114 (sin ráfaga)
DMR→ASL: llamada de EA3DEF (2140001) en TG 213
```

A receiving MMDVM hotspot logs the alias as
`DMR Talker Alias (Data Format 1, Received 10/10 char): 'EA1ABC/ASL'`.
