# Ham CallerID

**Who is talking on analog amateur links?** FM repeaters, AllStarLink and EchoLink carry no
callsign, and when that audio is bridged into DMR every call shows up as the bridge.

Ham CallerID fixes that with a short data burst at key-up and release, **decoded once at the
network edge and carried on as metadata** — never as audio through the digital vocoders:

```
 analog (ASL / RF)                        DMR (ADN, BrandMeister, TGIF…)
 voice + callsign burst  ──▶  BRIDGE  ──▶  voice + Talker Alias "EA1ABC/ASL"
 voice + generated burst ◀──  BRIDGE  ◀──  voice + DMR ID / Talker Alias
```

- **Burst v1**: AFSK 1200 baud, Golay(24,12) FEC + interleaving, 32-bit sync, ~220 ms for a
  callsign. Survives µ-law/IAX, FM radio paths with noise, Opus 16 kbps and lost packets.
  ([measurements](docs/SPEC.md#511-trama-v1-actual-23-nov-2026))
- **Bridge for AllStarLink ⇄ DMR** (`hamcid/usrp_proxy.py`): sits between `chan_usrp` and
  DVSwitch Analog_Bridge, no changes to ASL, DVSwitch or the DMR network. Puts the talker's
  callsign in the **Talker Alias inside the voice frames** (works with any network or radio
  that shows TA), marks the origin (`/ASL`, `/EL`), reads **EchoLink** callsigns from app_rpt,
  and plays the burst towards ASL for calls coming from DMR. → [Install guide](docs/BRIDGE.md)
- **Reference implementations**, sample-identical: Python (`hamcid/`), Kotlin
  ([Mobile ASL Client](https://play.google.com/store/apps/details?id=ovh.adan.allstar)) and a
  single-file C library (`c/hcid.h`, no malloc) + conformance vectors (`vectors/`).
- **Trust**: the burst is self-declared (as analog itself is). Bridges keep their own DMR ID and
  only put the callsign, with its origin, in the Talker Alias.

Status: working end to end on a production AllStarLink ⇄ DMR bridge. Specification and roadmap
are in Spanish for now (`docs/`); contributions and translations welcome.

```bash
python3 -m unittest discover -s tests      # Python
cd c && make test                          # C, against vectors/
python3 tools/channels.py 200              # channel survival bench (needs sox/ffmpeg)
```

License: code MIT, specification CC BY 4.0.

---

🇪🇸 **Resumen**: identificación del indicativo en enlaces analógicos con una ráfaga corta al
pulsar y soltar el PTT, que se descodifica en el puente y viaja como metadato (Talker Alias en
DMR). Especificación: [docs/SPEC.md](docs/SPEC.md) · Puente: [docs/PUENTE-DVSWITCH.md](docs/PUENTE-DVSWITCH.md) ·
Adopción: [docs/ADOPCION.md](docs/ADOPCION.md).
