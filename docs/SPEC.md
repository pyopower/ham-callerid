# Ham CallerID — especificación (borrador v0.1)

> Estado: **borrador de trabajo**. Todo es discutible; las decisiones abiertas están
> marcadas con 🔶 y resumidas al final.

## 1. Objetivo

En modos analógicos (FM por RF, AllStarLink, EchoLink y los puentes analógico↔digital)
no viaja el indicativo de quien transmite. Ham CallerID define:

1. **Cómo identificarse**: una ráfaga corta de señalización al inicio y/o al final de
   la transmisión (PTT), que emiten la radio o la aplicación.
2. **Dónde se descodifica**: en el **primer punto** en el que el audio analógico entra en
   un sistema (nodo ASL, nodo EchoLink, puente hacia DMR...).
3. **Cómo viaja después**: como **metadato** del sistema correspondiente, **no** como audio.
   La ráfaga se silencia en el audio reenviado.

## 2. Principios

- **Decodificar una vez, en el borde.** Los vocoders digitales (AMBE en DMR/YSF/D-STAR,
  Codec2 en M17) destruyen cualquier señal de datos y GSM 06.10 (EchoLink) la degrada.
  Por eso la ráfaga no debe atravesar varios saltos: se convierte en dato en el primero.
- **No molestar.** La ráfaga se silencia en todo lo que se reenvía. El oyente directo por
  RF del repetidor sí la oirá (igual que hoy MDC-1200), por eso debe ser **corta**.
- **Funcionar con lo que la gente ya tiene.** La fase 1 usa el *PTT-ID* DTMF que traen
  muchas radios analógicas baratas, sin cambiar firmware.
- **Abierto.** Formato documentado y código libre, para que cualquier software (app_rpt,
  SvxLink, DVSwitch, servidores ADN, apps) pueda implementarlo.

## 3. Arquitectura

```
 radio FM ──RF──▶ repetidor / nodo ASL ──▶ [DECODIFICADOR] ──▶ metadato ──▶ red
 (ráfaga PTT-ID)                            │  silencia la ráfaga
                                            └▶ audio limpio ──────────────▶ red

 app (Mobile ASL Client, ADN PoC...) ──▶ ya conoce el indicativo: lo manda
                                         directamente como metadato (sin ráfaga)
```

Metadato según el destino (ver §6): texto IAX en AllStarLink, información de estación
en EchoLink, **ID de origen + Talker Alias** en DMR/ADN.

## 4. Fase 1 — DTMF PTT-ID

Muchas radios (Baofeng, Quansheng, Retevis, TYT, Anytone...) pueden enviar una secuencia
DTMF configurable al pulsar el PTT (*BOT*), al soltarlo (*EOT*) o en ambos.

### 4.1 Formato

```
  <PREFIJO> <ID> [<FIN>]
```

| Campo   | Valor propuesto | Notas |
|---------|-----------------|-------|
| PREFIJO | `D` 🔶           | Las letras A–D casi nunca se usan en comandos de app_rpt/SvxLink, que empiezan por `*` o `#`. |
| ID      | ID DMR (7 dígitos) o ID corto (ver 4.2) | Solo dígitos 0–9. |
| FIN     | `#` (opcional)   | Ayuda a delimitar si la radio no deja pausa. |

Ejemplo: ID DMR 2130035 → `D2130035` (8 tonos; a ~50 ms + 50 ms por tono ≈ 0,8 s).

### 4.2 Resolución del ID → indicativo

1. **ID DMR de 7 dígitos**: base pública de RadioID (`user.csv`), la misma que ya usan
   los servidores ADN y la mayoría de software DMR.
2. 🔶 **Radios que solo admiten 5 caracteres de PTT-ID** (a comprobar: ¿UV-5R?): haría
   falta un ID corto o un registro propio. Pendiente de verificar qué límites tienen los
   modelos más comunes antes de decidir.
3. 🔶 **Quien no tiene ID DMR**: codificación numérica del indicativo (p. ej. base-36 →
   decimal, ~10–12 dígitos) o alta en un registro propio.

### 4.3 Detección

- Goertzel por bloques de 10 ms sobre audio de 8 kHz (el mismo formato de ASL y IAX).
- Un dígito válido: par de tonos estable ≥ 40 ms, *twist* y relación señal/ruido dentro de
  límites (mismos criterios que un decodificador DTMF clásico).
- Una secuencia `D…` termina con `#`, con un silencio > 300 ms o al soltar el PTT (COS).
- Una secuencia se acepta solo si el ID existe en la base; si no, se descarta (evita falsos
  positivos con voz o con DTMF de otros usos).

### 4.4 Silenciado

Para quitar la secuencia del audio reenviado, el decodificador retrasa el audio ~100–150 ms
y sustituye por silencio los bloques que contienen tonos DTMF. app_rpt ya silencia DTMF en
algunos casos; hay que medir qué hace hoy con letras A–D. 🔶

## 5. Fase 2 — ráfaga de datos con texto

Para llevar el **indicativo en texto** (sin depender de un ID numérico) y otros datos cortos.

### 5.1 Propuesta inicial 🔶

| Aspecto     | Propuesta |
|-------------|-----------|
| Modulación  | AFSK 1200 bd, Bell 202 (1200/2200 Hz): pasa bien por FM, µ-law y la mayoría de repetidores, y hay decodificadores libres (Direwolf, multimon-ng). |
| Trama       | preámbulo (~40 ms) · sincronismo · versión · tipo · longitud · carga · CRC-16 |
| Carga tipo 1 | indicativo (≤ 10 caracteres ASCII, mayúsculas, dígitos y `/`) + SSID/sufijo opcional |
| Carga tipo 2 🔶 | texto corto (≤ 32 caracteres): QTH, nombre, aviso... |
| Duración    | ~150–250 ms con indicativo |
| Cuándo      | **al final** (EOT) por defecto: al principio la cortan la apertura del silenciador y el retardo de los repetidores. Opcionalmente también al inicio. |
| Robustez    | repetir la carga dos veces o FEC (Golay / Hamming) 🔶 |

Alternativas a estudiar: reutilizar una trama AX.25 UI (cuyo origen ya *es* un indicativo
y que entienden Direwolf y los TNC) frente a una trama propia más corta.

### 5.2 Quién la emite

- Apps propias: Mobile ASL Client, ADN PoC, PTT LoRa (cuando transmiten audio analógico).
- Firmware libre: Quansheng UV-K5/K6 (firmwares abiertos), OpenGD77 en analógico, MMDVM.
- Interfaces de radio (p. ej. adaptadores BT/USB) que inyecten la ráfaga al soltar PTT.

## 6. Transporte como metadato

| Sistema | Cómo se entrega el indicativo |
|---------|-------------------------------|
| AllStarLink (app_rpt) | trama TEXT de IAX a los nodos enlazados y a los clientes; 🔶 formato a acordar (p. ej. `CID <nodo> <indicativo> <ID>`). Visible en Allmon3 y en Mobile ASL Client. |
| EchoLink | campo de información/chat de la estación o del nodo 🔶 |
| DMR / ADN SYSTEMS | el puente pone el **ID DMR real como origen** (si se conoce) y **Talker Alias** con el indicativo, en lugar del ID del puente. Monitor, lastheard y apps lo muestran. |
| YSF / M17 | campo de origen (callsign) de la cabecera 🔶 |

## 7. Decisiones abiertas 🔶

1. Prefijo DTMF definitivo y comportamiento de app_rpt/SvxLink con A–D.
2. Límite de caracteres del PTT-ID en las radios más comunes.
3. Codificación para quien no tiene ID DMR.
4. Fase 2: trama propia o AX.25 UI; FEC; duración máxima aceptable.
5. Formato del texto IAX y del metadato en EchoLink.
6. Política de privacidad: solo indicativos públicos (sin datos personales extra).
