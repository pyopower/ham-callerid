"""Prueba del proxy en el laboratorio DVSwitch de micro2 (lab1/lab2, TG 5020 del master privado)."""
import math, socket, struct, sys, threading, time
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else ".")
from hamcid import burst
from hamcid.usrp_proxy import usrp_build, usrp_parse, pcm_to_floats, floats_to_pcm, set_info

A = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); A.bind(("127.0.0.1", 55210))   # hace de ASL (lado proxy)
B = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); B.bind(("127.0.0.1", 55099))   # hace de ASL en lab2
rx = {"A": [], "B": [], "Ameta": [], "Bmeta": []}

def listen(s, name):
    s.settimeout(0.3)
    while True:
        try: d, _ = s.recvfrom(4096)
        except socket.timeout: continue
        u = usrp_parse(d)
        if not u: continue
        seq, key, typ, p = u
        if typ == 0 and key: rx[name] += pcm_to_floats(p)
        elif typ == 2: rx[name + "meta"].append(p[:20])
for s, n in ((A, "A"), (B, "B")): threading.Thread(target=listen, args=(s, n), daemon=True).start()

def send(sock, dst, samples):
    samples += [0.0] * (-len(samples) % 160)
    t0 = time.time(); seq = 1
    for i in range(0, len(samples), 160):
        sock.sendto(usrp_build(seq, 1, 0, floats_to_pcm(samples[i:i+160])), dst); seq += 1
        time.sleep(max(0, t0 + (i // 160 + 1) * 0.02 - time.time()))
    sock.sendto(usrp_build(seq, 0, 0, b"\0" * 320), dst)

tone = lambda ms, f=700: [0.3 * math.sin(2 * math.pi * f * i / 8000) for i in range(8 * ms)]
B.sendto(usrp_build(0, 0, 3, b""), ("127.0.0.1", 55032)); time.sleep(0.5)

print("== 1) ASL→DMR con ráfaga C31AG")
send(A, ("127.0.0.1", 55200), burst.modulate("C31AG", amp=0.3) + tone(1500) + burst.modulate("C31AG", amp=0.3))
time.sleep(3)
print("   metadatos en el otro extremo:", [p.hex()[:30] for p in rx["Bmeta"]])
print("   ráfagas que quedan en el audio recibido:", [t for _, t, _ in burst.demodulate(rx["B"])], "(debe estar vacío: silenciadas)")
print("   audio recibido: %.1f s" % (len(rx["B"]) / 8000))
rx["B"].clear(); rx["Bmeta"].clear(); time.sleep(2)

print("== 1b) ASL→DMR sin ráfaga (debe volver al ID de pasarela)")
send(A, ("127.0.0.1", 55200), tone(1000)); time.sleep(3)
print("   metadatos:", [p.hex()[:30] for p in rx["Bmeta"]])
rx["B"].clear(); rx["Bmeta"].clear(); time.sleep(2)

print("== 2) DMR→ASL: llamada de VA3BOC (1023007) desde lab2")
B.sendto(set_info(1023007, "VA3BOC", 5020, 2), ("127.0.0.1", 31150)); time.sleep(0.3)
send(B, ("127.0.0.1", 55032), tone(1500, 500)); time.sleep(3)
B.sendto(set_info(50003, "", 5020, 2), ("127.0.0.1", 31150))
print("   metadatos que llegan al proxy (A los recibe reenviados):", [p.hex()[:30] for p in rx["Ameta"]])
print("   ráfagas descodificadas en «ASL»:", [t for _, t, _ in burst.demodulate(rx["A"])])
print("   audio: %.1f s" % (len(rx["A"]) / 8000))
