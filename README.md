# Ham CallerID

**Identificación del indicativo en transmisiones analógicas** — repetidores FM,
AllStarLink, EchoLink y puentes analógico ↔ digital (ADN SYSTEMS, DVSwitch...).

En analógico no viaja quién habla. Ham CallerID propone una ráfaga corta al pulsar o soltar
el PTT, que se **descodifica en el primer punto de entrada** y se convierte en **metadato**:
texto en AllStarLink, información en EchoLink, **ID + Talker Alias** en DMR. La ráfaga se
silencia en el audio reenviado.

- **Fase 1 — DTMF PTT-ID**: funciona con radios que ya existen (secuencia `D<ID DMR>#`).
- **Fase 2 — ráfaga de datos** (AFSK 1200) con el indicativo en texto, para apps y firmware libre.

📄 Especificación: [docs/SPEC.md](docs/SPEC.md) · 🗺️ Hoja de ruta: [docs/HOJA-DE-RUTA.md](docs/HOJA-DE-RUTA.md)

## Estado

Borrador. Incluye un decodificador DTMF de referencia en Python (`hamcid/dtmf.py`):

```bash
python3 -m unittest -v tests.test_dtmf
```

> 🇬🇧 *Ham CallerID: identify who is talking on analog voice links (FM repeaters,
> AllStarLink, EchoLink, analog↔digital bridges) with a short PTT burst decoded at the
> network edge and carried on as metadata. Early draft.*
