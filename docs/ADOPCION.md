# Hoja de ruta de adopción

Objetivo: que ASL, EchoLink, SvxLink, DVSwitch, MMDVM, apps y firmwares lo integren **sin tocar su
núcleo**, de lo menos a lo más invasivo.

## 1. Estándar abierto v1
- Formato congelado: versión, tipos reservados, **FEC** (decidir con las medidas del punto 5).
- **Vectores de prueba**: WAV de referencia + resultado esperado (conformidad).
- Especificación en inglés, licencia CC-BY; código de referencia MIT/BSD (cabe en GPL y en cerrados).

## 2. Bibliotecas de referencia
- Python ✅ · Kotlin ✅
- **C de un solo fichero**, sin dependencias, apto para MCU (app_rpt, SvxLink, MMDVMHost, OpenGD77, Quansheng).
- JavaScript + **página de demostración** que descodifica con el micro del navegador.

## 3. Niveles de integración
| Nivel | Cómo | Quién |
|---|---|---|
| 0 — sin tocar nada | proceso aparte en el camino del audio (intermediario USRP, tarjeta de sonido virtual) | ASL vía USRP, DVSwitch, sysops |
| 1 — módulo/parche pequeño | decodificar en la entrada de audio y publicar el indicativo como evento | ASL3 (evento AMI + TEXT a enlaces → Allmon3/Supermon), SvxLink (módulo), MMDVMHost FM |
| 2 — emisores nativos | ráfaga al pulsar/soltar PTT | apps propias, DroidStar (fork), OpenGD77, Quansheng, clientes web |

EchoLink oficial (cerrado): solo nivel 0 o clientes libres compatibles (SvxLink, TheLinkBox, qtel).

## 4. Transporte del metadato
| Sistema | Convención |
|---|---|
| USRP | TLV `SET_INFO` de DVSwitch (estándar de facto) |
| ASL | TEXT `HCID <nodo> <indicativo> [ID]` + evento AMI |
| EchoLink | línea de info/chat |
| DMR | ID de origen + Talker Alias |
| YSF / D-STAR / M17 | campo de origen de la cabecera |

## 5. Validación en caminos reales
FM por repetidor (preénfasis, filtros), GSM (EchoLink), Opus (Zello, Mumble), pérdida de paquetes.

## 6. Comunidad
Repo público, propuesta en el foro de AllStarLink y en la lista de SvxLink, demostración en ADN.

## 🔶 Confianza
La ráfaga no está autenticada (como el propio analógico): cualquiera puede emitir otro indicativo.
- El puente debe marcar el origen analógico en el Talker Alias (p. ej. `C31AG via ASL`).
- **Decisión abierta**: ¿poner el ID DMR real del indicativo (lo que hace hoy el intermediario) o
  usar siempre el ID del puente + Talker Alias? Opciones intermedias: solo IDs de una lista
  blanca del sysop, o firma opcional en la ráfaga (tipo futuro) para quien quiera autenticar.
