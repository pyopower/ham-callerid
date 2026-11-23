/*
 * hcid.h — Ham CallerID v1, single-file C library (encoder + decoder). MIT License.
 *
 * Callsign burst for analog voice links: AFSK 1200 baud (mark 1200 Hz = 1, space 2200 Hz = 0),
 * synchronous NRZ, 8 kHz mono, frame =
 *     mark lead 24 bits · preamble 0101… 16 bits · sync 0x1ACFFC1D · k ≥ 8 Golay(24,12) words,
 *     interleaved column by column.
 * Data before coding: version(3)=1 | type(3) | length(6) | payload | CRC-16/CCITT-FALSE over the
 * 12-bit header (as 2 bytes) + payload. Spec: https://github.com/pyopower/ham-callerid
 *
 * No malloc, no dependencies beyond <math.h>/<string.h>. In ONE C file:
 *     #define HCID_IMPLEMENTATION
 *     #include "hcid.h"
 *
 * Encoder:  int n = hcid_modulate("C31AG", HCID_TYPE_CALLSIGN, 0.3f, pcm, cap);   // int16 samples
 * Decoder:  hcid_demodulate(pcm, n, scratch, on_hit, user);  // scratch: n floats
 *           (feed it a window that holds a whole burst, e.g. the last 700 ms of audio)
 */
#ifndef HCID_H
#define HCID_H

#include <stdint.h>
#include <stddef.h>

#define HCID_RATE 8000
#define HCID_TYPE_CALLSIGN 1
#define HCID_TYPE_TEXT 2
#define HCID_MAX_LEN 32
/* longest frame: 24+16+32 + 24*24 + 2 bits at 6.667 samples/bit */
#define HCID_MAX_SAMPLES 4334

typedef void (*hcid_hit_fn)(int type, const char *text, int start_sample, void *user);

#ifdef __cplusplus
extern "C" {
#endif
/* Returns the number of samples written, 0 if the text is invalid or cap too small. */
int hcid_modulate(const char *text, int type, float amp, int16_t *out, int cap);
/* Number of samples hcid_modulate() would write for this text (0 if invalid). */
int hcid_frame_samples(const char *text);
/* Finds every valid burst in pcm[0..n); scratch must hold n floats. Returns the number found. */
int hcid_demodulate(const int16_t *pcm, int n, float *scratch, hcid_hit_fn cb, void *user);
uint32_t hcid_golay_encode(uint32_t data12);
int hcid_golay_decode(uint32_t word24);          /* 12 data bits or -1 */
uint16_t hcid_crc16(const uint8_t *d, int n);
#ifdef __cplusplus
}
#endif
#endif /* HCID_H */

#ifdef HCID_IMPLEMENTATION
#include <math.h>
#include <string.h>

#define HCID__SPB (8000.0 / 1200.0)
#define HCID__LEAD 24
#define HCID__MINW 8
#define HCID__MAXW 24
#define HCID__SYNC 0x1ACFFC1Du
#define HCID__PI 3.14159265358979323846

uint16_t hcid_crc16(const uint8_t *d, int n) {
    uint16_t crc = 0xFFFF;
    for (int i = 0; i < n; i++) {
        crc ^= (uint16_t)(d[i] << 8);
        for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

uint32_t hcid_golay_encode(uint32_t data12) {
    uint32_t r = (data12 & 0xFFF) << 11;
    for (int i = 22; i >= 11; i--) if (r & (1u << i)) r ^= 0xC75u << (i - 11);
    uint32_t cw23 = ((data12 & 0xFFF) << 11) | r, p = 0, v = cw23;
    while (v) { p ^= v & 1; v >>= 1; }
    return (cw23 << 1) | p;
}

static uint32_t hcid__syndrome(uint32_t w) { return (w & 0xFFF) ^ (hcid_golay_encode(w >> 12) & 0xFFF); }

static int32_t hcid__table[4096];
static int hcid__table_ready = 0;

static void hcid__put(uint32_t e) { uint32_t s = hcid__syndrome(e); if (hcid__table[s] == -1) hcid__table[s] = (int32_t)e; }

static void hcid__init_table(void) {
    if (hcid__table_ready) return;
    for (int i = 0; i < 4096; i++) hcid__table[i] = -1;
    hcid__table[0] = 0;
    for (int a = 0; a < 24; a++) hcid__put(1u << a);
    for (int a = 0; a < 24; a++) for (int b = a + 1; b < 24; b++) hcid__put((1u << a) | (1u << b));
    for (int a = 0; a < 24; a++) for (int b = a + 1; b < 24; b++) for (int c = b + 1; c < 24; c++)
        hcid__put((1u << a) | (1u << b) | (1u << c));
    hcid__table_ready = 1;
}

int hcid_golay_decode(uint32_t w) {
    hcid__init_table();
    int32_t e = hcid__table[hcid__syndrome(w & 0xFFFFFF)];
    return e < 0 ? -1 : (int)(((w & 0xFFFFFF) ^ (uint32_t)e) >> 12);
}

static int hcid__valid(const char *t, int type) {
    int n = (int)strlen(t);
    if (n < 1 || n > HCID_MAX_LEN) return 0;
    if (type == HCID_TYPE_CALLSIGN)
        for (int i = 0; i < n; i++) {
            char c = t[i];
            if (!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '/' || c == '-')) return 0;
        }
    return n;
}

static int hcid__words_for(int nbits) { int k = (nbits + 11) / 12; return k < HCID__MINW ? HCID__MINW : k; }

/* frame bits into bits[] (one per byte); returns count or 0 */
static int hcid__frame_bits(const char *t, int type, uint8_t *bits) {
    int n = hcid__valid(t, type);
    if (!n) return 0;
    uint8_t data[24 * 12];
    int nb = 0;
    uint16_t hdr = (uint16_t)((1 << 9) | ((type & 7) << 6) | n);
    uint8_t buf[2 + HCID_MAX_LEN];
    buf[0] = (uint8_t)(hdr >> 8); buf[1] = (uint8_t)hdr;
    memcpy(buf + 2, t, (size_t)n);
    uint16_t crc = hcid_crc16(buf, 2 + n);
    for (int i = 0; i < 12; i++) data[nb++] = (hdr >> (11 - i)) & 1;
    for (int j = 0; j < n + 2; j++) {
        uint8_t b = j < n ? (uint8_t)t[j] : (uint8_t)(j == n ? crc >> 8 : crc & 0xFF);
        for (int i = 0; i < 8; i++) data[nb++] = (b >> (7 - i)) & 1;
    }
    int k = hcid__words_for(nb);
    while (nb < 12 * k) data[nb++] = 0;
    uint32_t words[HCID__MAXW];
    for (int w = 0; w < k; w++) {
        uint32_t v = 0;
        for (int i = 0; i < 12; i++) v = (v << 1) | data[12 * w + i];
        words[w] = hcid_golay_encode(v);
    }
    int m = 0;
    for (int i = 0; i < HCID__LEAD; i++) bits[m++] = 1;
    for (int i = 0; i < 16; i++) bits[m++] = (uint8_t)(i & 1);
    for (int i = 0; i < 32; i++) bits[m++] = (HCID__SYNC >> (31 - i)) & 1;
    for (int c = 0; c < 24; c++) for (int w = 0; w < k; w++) bits[m++] = (words[w] >> (23 - c)) & 1;
    bits[m++] = 1; bits[m++] = 1;
    return m;
}

int hcid_frame_samples(const char *text) {
    uint8_t bits[24 + 16 + 32 + 24 * HCID__MAXW + 2];
    int m = hcid__frame_bits(text, HCID_TYPE_CALLSIGN, bits);
    return m ? (int)ceil(m * HCID__SPB) : 0;
}

int hcid_modulate(const char *text, int type, float amp, int16_t *out, int cap) {
    uint8_t bits[24 + 16 + 32 + 24 * HCID__MAXW + 2];
    int m = hcid__frame_bits(text, type, bits);
    if (!m) return 0;
    int total = (int)ceil(m * HCID__SPB);
    if (total > cap) return 0;
    double phase = 0.0;
    for (int n = 0; n < total; n++) {
        int bi = (int)(n / HCID__SPB);
        if (bi >= m) bi = m - 1;
        phase += 2 * HCID__PI * (bits[bi] ? 1200.0 : 2200.0) / HCID_RATE;
        double v = amp * 32767.0 * sin(phase);
        out[n] = (int16_t)lround(v);
    }
    return total;
}

/* ------------------------------------------------------------------ decoder */

static void hcid__discriminator(const int16_t *x, int n, float *d) {
    const int w = 7, half = 3;   /* round(6.667) */
    double a = 0, b = 0, c = 0, e = 0;
    for (int i = 0; i < n; i++) {
        double v = x[i] / 32768.0, ph = 2 * HCID__PI * (i % HCID_RATE) / HCID_RATE;
        a += v * cos(1200 * ph); b += v * sin(1200 * ph); c += v * cos(2200 * ph); e += v * sin(2200 * ph);
        if (i >= w) {
            double u = x[i - w] / 32768.0, q = 2 * HCID__PI * ((i - w) % HCID_RATE) / HCID_RATE;
            a -= u * cos(1200 * q); b -= u * sin(1200 * q); c -= u * cos(2200 * q); e -= u * sin(2200 * q);
        }
        int j = i - half;
        if (j >= 0) d[j] = (float)(hypot(a, b) - hypot(c, e));
    }
    for (int j = n - half; j < n; j++) if (j >= 0) d[j] = 0;
}

static const uint8_t *hcid__ref(void) {
    static uint8_t r[40];
    static int ready = 0;
    if (!ready) {
        for (int i = 0; i < 8; i++) r[i] = (uint8_t)(i & 1);
        for (int i = 0; i < 32; i++) r[8 + i] = (HCID__SYNC >> (31 - i)) & 1;
        ready = 1;
    }
    return r;
}

static int hcid__read_bits(const float *d, int n, double pos, uint8_t *out, int count) {
    int got = 0, prev = -1;
    for (int k = 0; k < count; k++) {
        int i = (int)(pos + 0.5 * HCID__SPB);
        if (i >= n) break;
        int v = d[i] > 0;
        if (prev >= 0 && v != prev) {
            int lo = (int)(pos - HCID__SPB / 2), hi = (int)(pos + HCID__SPB / 2);
            if (lo < 1) lo = 1;
            if (hi > n) hi = n;
            for (int j = lo; j < hi; j++)
                if ((d[j - 1] > 0) != (d[j] > 0)) { pos += 0.25 * (j - pos); break; }
        }
        out[got++] = (uint8_t)v; prev = v;
        pos += HCID__SPB;
    }
    return got;
}

static int hcid__decode_block(const uint8_t *bits, int nbits, int *type, char *text) {
    for (int k = HCID__MINW; k <= HCID__MAXW; k++) {
        if (nbits < 24 * k) break;
        uint32_t words[HCID__MAXW];
        int data[HCID__MAXW], ok = 1;
        for (int w = 0; w < k; w++) words[w] = 0;
        for (int c = 0; c < 24; c++) for (int w = 0; w < k; w++) words[w] = (words[w] << 1) | bits[c * k + w];
        for (int w = 0; w < k && ok; w++) { data[w] = hcid_golay_decode(words[w]); ok = data[w] >= 0; }
        if (!ok) continue;
        int hdr = data[0], ver = hdr >> 9, kind = (hdr >> 6) & 7, n = hdr & 0x3F;
        if (ver != 1 || n < 1 || n > HCID_MAX_LEN || hcid__words_for(12 + 8 * n + 16) != k) continue;
        uint8_t by[2 + HCID_MAX_LEN + 2];
        by[0] = (uint8_t)(hdr >> 8); by[1] = (uint8_t)hdr;
        for (int i = 0; i < n + 2; i++) {
            int v = 0;
            for (int b = 0; b < 8; b++) {
                int idx = 8 * i + b;
                v = (v << 1) | ((data[1 + idx / 12] >> (11 - idx % 12)) & 1);
            }
            by[2 + i] = (uint8_t)v;
        }
        uint16_t crc = (uint16_t)((by[2 + n] << 8) | by[3 + n]);
        if (hcid_crc16(by, 2 + n) != crc) continue;
        memcpy(text, by + 2, (size_t)n); text[n] = 0;
        *type = kind;
        return 1;
    }
    return 0;
}

int hcid_demodulate(const int16_t *pcm, int n, float *d, hcid_hit_fn cb, void *user) {
    if (n < 200) return 0;
    hcid__discriminator(pcm, n, d);
    const uint8_t *ref = hcid__ref();
    const int nref = 40, span = (int)(nref * HCID__SPB) + 2;
    int hits = 0, o = 0;
    while (o + span < n) {
        int err = 0;
        for (int k = 0; k < nref && err <= 3; k++)
            if ((d[(int)(o + (k + 0.5) * HCID__SPB)] > 0) != ref[k]) err++;
        if (err > 3) { o++; continue; }
        int best_f = o, best_e = 1 << 30;
        double best_m = -1;
        for (int f = o; f < o + 7 && f + span < n; f++) {
            int e2 = 0; double m = 0;
            for (int k = 0; k < nref; k++) {
                float v = d[(int)(f + (k + 0.5) * HCID__SPB)];
                if ((v > 0) != ref[k]) e2++;
                m += fabs(v);
            }
            if (e2 < best_e || (e2 == best_e && m > best_m)) { best_e = e2; best_m = m; best_f = f; }
        }
        uint8_t bits[24 * HCID__MAXW];
        int nb = hcid__read_bits(d, n, best_f + nref * HCID__SPB, bits, 24 * HCID__MAXW);
        int type; char text[HCID_MAX_LEN + 1];
        if (hcid__decode_block(bits, nb, &type, text)) {
            int start = (int)(best_f - (HCID__LEAD + 8) * HCID__SPB);
            if (cb) cb(type, text, start < 0 ? 0 : start, user);
            hits++;
        }
        o = best_f + span;
    }
    return hits;
}
#endif /* HCID_IMPLEMENTATION */
