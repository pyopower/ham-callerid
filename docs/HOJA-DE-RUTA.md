# Hoja de ruta

## Fase 1 — DTMF PTT-ID
- [x] Borrador de especificación (`docs/SPEC.md`)
- [x] Decodificador de referencia en Python + pruebas sintéticas
- [ ] Inventario de radios: ¿qué modelos tienen PTT-ID, longitud máxima, letras A–D, BOT/EOT?
- [ ] Medir con una radio real a través de un repetidor y de ASL (µ-law)
- [ ] Ver qué hace app_rpt hoy con `D…` desde RF y desde enlaces (¿ejecuta, ignora, silencia?)
- [ ] Decodificador para ASL: proceso junto al nodo (USRP / AMI) que publique el indicativo
- [ ] Mostrar el indicativo en Mobile ASL Client y en Allmon3
- [ ] Puente ADN: ID DMR real como origen + Talker Alias

## Fase 2 — ráfaga con texto
- [x] Trama v0 propia (sin FEC; CRC-16) — revisar FEC y AX.25 UI más adelante
- [x] Generador/decodificador de referencia (`hamcid/burst.py`) + pruebas
- [x] Mobile ASL Client: emite al pulsar/soltar y descodifica lo recibido (probado OP8T ↔ ZUK por el nodo 1922)
- [x] Voz retrasada tras la ráfaga de inicio (no se pierde voz si hablas al pulsar); fin = voz pendiente → ráfaga → unkey
- [x] Medido OP8T ← ZUK por 1922: 22/24 ráfagas, 12/12 transmisiones identificadas
- [x] v1: Golay(24,12) + entrelazado + sincronismo de 32 bits; receptores rellenan huecos con silencio
- [x] Biblioteca C (c/hcid.h) + vectores de conformidad
- [x] EchoLink por metadatos (AMI: RPT_ALINKS + echolink dbget) en el puente; GSM no admite FSK
- [x] Puente ASL⇄ADN en producción (alias en la voz, ráfaga hacia ASL)
- [~] Silenciar la ráfaga en recepción: descartado de momento en la app (el usuario prefiere oírla a añadir retardo); sí en nodos/puentes
- [ ] Probar a través de EchoLink (GSM) y de radio FM real
- [ ] ADN PoC
- [ ] Firmware libre (Quansheng, OpenGD77 analógico)

## Abierto
- Publicar como estándar abierto cuando el formato esté estable (repo público + versión en inglés).
