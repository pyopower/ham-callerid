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
- [ ] Decidir trama (propia vs AX.25 UI), FEC y duración
- [ ] Generador/decodificador de referencia
- [ ] Emisión desde Mobile ASL Client / ADN PoC al soltar PTT
- [ ] Firmware libre (Quansheng, OpenGD77 analógico)

## Abierto
- Publicar como estándar abierto cuando el formato esté estable (repo público + versión en inglés).
