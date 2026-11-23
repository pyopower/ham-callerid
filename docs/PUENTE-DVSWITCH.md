# Puente ASL ↔ DMR con DVSwitch: lo que permite hoy (medido 23-nov-2026)

Laboratorio: dos pares DVSwitch (`lab1` → peer 50051 / pasarela 50002,
`lab2` → peer 50052 / pasarela 50003) contra un master HBlink4 de pruebas,
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

## Intermediario implementado: `hamcid/usrp_proxy.py` (23-nov-2026)

```
python3 -m hamcid.usrp_proxy --listen-asl IP:P --asl IP:P --listen-ab IP:P --ab IP:P \
        --ab-tlv IP:P --gateway-id N --tg N [--ts 2] [--delay 300] [--level 0.25]
```

Solo biblioteca estándar (pensado también para máquinas pequeñas sin numpy). ~25 MB de RAM, CPU despreciable.

- Detector en flujo: busca los **20 ms de 1200 Hz** del reposo inicial (ventanas de 80 muestras
  cada 40; 0 falsos disparos en 20 s de voz simulada) y solo entonces demodula (≈3 ms).
- Índice de IDs compacto en memoria (4,4 MB para 310 524 indicativos, búsqueda binaria). Leer
  el CSV en cada llamada costaba ~350 ms y hacía que la llamada saliera con el ID de la pasarela.

Laboratorio (lab1 ⇄ proxy, lab2 al otro lado, TG 5020):

| Prueba | Resultado |
|---|---|
| ASL→DMR con ráfaga C31AG | master: `src=2130035` ✅; ráfaga silenciada en el otro extremo ✅ |
| ASL→DMR siguiente sin ráfaga | `src=50002` (pasarela restaurada) ✅ |
| DMR→ASL llamada de VA3BOC (1023007) | «ASL» descodifica `VA3BOC` al principio y al final ✅ |

Pendiente para producción (60596 ⇄ TG 213): servicio systemd, cambio de puertos
(AB `[USRP] address=127.0.0.1 txPort/rxPort` → proxy; ASL sigue igual), prueba con la app.

## ✅ Producción de punta a punta (23-nov-2026, 19:17 UTC)

Mobile ASL Client (OnePlus 8T, ráfaga activada) → nodo 60596 → 1932 → hcid-usrp-proxy →
Analog_Bridge/MMDVM_Bridge → servidor ADN 2131 (v2.7.0, `TALKER_ALIAS_MODE: both`) → TG 213.

- El intermediario reconoce la ráfaga ~0,4 s después del inicio (app_rpt manda ~200 ms antes)
  y mete el alias en la voz a mitad de llamada.
- Capturado lo que el servidor **reenvía** a otro sistema del TG 213 (BALIZA-MASTER):
  supertramas `FLCO 0,0,0,4,0,5,0,4,…` → alias **`C31AG/ASL`** con ID de pasarela 2130035.
  El servidor recoge el alias de la fuente y lo reenvía sin tocar su código.
- DMR → ASL: ráfaga al principio y al final de cada llamada (visto con 1000001, EA5IAJ, SP8X).
