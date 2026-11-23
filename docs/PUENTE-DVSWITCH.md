# Puente ASL ↔ DMR con DVSwitch: lo que permite hoy (medido 23-nov-2026)

Laboratorio en micro2: dos pares DVSwitch (`lab1` → peer 50051 / pasarela 50002,
`lab2` → peer 50052 / pasarela 50003) contra el master privado HBlink4 de micro1,
TG 5020 (sin uso). Un script hace de ASL en los dos extremos por USRP.
Analog_Bridge 1.6.4 + MMDVM_Bridge 1.6.8.

## Analógico → DMR: el ID de origen se puede cambiar en cada llamada ✅

| Variante | ID de origen visto en el master |
|---|---|
| Sin metadato | 50002 (pasarela) |
| `SET_INFO` dentro de un paquete USRP de texto (tipo 2) hacia el puerto USRP | 50002 — **ignorado** |
| `SET_INFO` (TLV `0x08`) al **puerto TLV** de Analog_Bridge (`[AMBE_AUDIO] rxPort`) | **2130035** ✅ |

TLV `SET_INFO` (lo mismo que `dvswitch.sh setCallAndId`):

```
08 <len> <dmrid 3B BE> <repeaterid 4B BE> <tg 3B BE> <ts 1B> <cc 1B> <indicativo> 00
```

⚠️ **El ID se queda pegado**: la llamada siguiente sin metadato sigue saliendo con 2130035.
Hay que **restaurar** el ID de la pasarela al acabar cada transmisión (`SET_INFO` con 50002):
comprobado, vuelve a 50002.

Talker Alias: HBlink4 no lo registra; queda por comprobar con un servidor ADN si
MMDVM_Bridge manda TA. En ADN, con el ID real, el indicativo ya sale de la base de IDs.

## DMR → analógico: los metadatos ya llegan al lado USRP ✅

Con `exportMetadata = true`, al empezar cada llamada DMR Analog_Bridge envía al cliente USRP
un paquete de **tipo 2 (texto)** con el TLV `SET_INFO`:

```
08 12  20 80 73  00 00 C3 84  00 13 9C  02  00  "C31AG" 00 …
       2130035   rep 50052    TG 5020   TS2 CC0 indicativo (de la base de IDs)
```

app_rpt (chan_usrp) lo **ignora**: hoy se pierde. Un intermediario lo puede convertir en la
ráfaga Ham CallerID e insertarla en el audio hacia ASL.

## Diseño del intermediario (siguiente paso)

```
ASL (chan_usrp) ⇄ [hcid-usrp-proxy] ⇄ Analog_Bridge ⇄ MMDVM_Bridge ⇄ ADN
```

- **ASL → AB**: retrasa el audio ~200 ms, descodifica la ráfaga, la silencia, busca el ID del
  indicativo (RadioID) y manda `SET_INFO` al puerto TLV antes de la voz; al soltar, restaura
  el ID de la pasarela. Sin ráfaga (o indicativo sin ID): ID de la pasarela, como hoy.
- **AB → ASL**: al ver el `SET_INFO` de una llamada DMR, inserta la ráfaga con el indicativo
  al principio y al final del audio hacia ASL.
- Solo cambia puertos (ASL apunta al proxy y el proxy a AB); se quita volviendo a los puertos
  originales.
