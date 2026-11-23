/* Conformidad de hcid.h con los vectores de vectors/ (generados por la referencia en Python). */
#define HCID_IMPLEMENTATION
#include "hcid.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int16_t *read_wav(const char *path, int *n) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *buf = malloc((size_t)sz);
    if (fread(buf, 1, (size_t)sz, f) != (size_t)sz) { fclose(f); free(buf); return NULL; }
    fclose(f);
    long p = 12;                                   /* buscar el bloque «data» */
    while (p + 8 <= sz && memcmp(buf + p, "data", 4) != 0) p += 8 + (buf[p + 4] | buf[p + 5] << 8 | buf[p + 6] << 16 | buf[p + 7] << 24);
    int len = buf[p + 4] | buf[p + 5] << 8 | buf[p + 6] << 16 | buf[p + 7] << 24;
    *n = len / 2;
    int16_t *x = malloc((size_t)len);
    memcpy(x, buf + p + 8, (size_t)len);
    free(buf);
    return x;
}

static char got[512];
static void on_hit(int type, const char *text, int start, void *u) {
    (void)type; (void)start; (void)u;
    if (got[0]) strcat(got, ",");
    strcat(got, text);
}

int main(int argc, char **argv) {
    const char *dir = argc > 1 ? argv[1] : "vectors";
    char path[1100], line[512];
    snprintf(path, sizeof path, "%s/expected.txt", dir);
    FILE *f = fopen(path, "r");
    if (!f) { perror(path); return 2; }
    int fails = 0, total = 0;
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        char *tab = strchr(line, '\t');
        if (!tab) continue;
        *tab = 0;
        const char *exp = tab + 1;
        snprintf(path, sizeof path, "%s/%s", dir, line);
        int n; int16_t *x = read_wav(path, &n);
        if (!x) { printf("FALTA %s\n", path); fails++; continue; }
        float *scratch = malloc(sizeof(float) * (size_t)n);
        got[0] = 0;
        hcid_demodulate(x, n, scratch, on_hit, NULL);
        int ok = strcmp(got, exp) == 0;
        /* los mod_* además deben coincidir muestra a muestra con el modulador en C */
        if (ok && strncmp(line, "mod_", 4) == 0) {
            int16_t *m = malloc(sizeof(int16_t) * HCID_MAX_SAMPLES);
            int k = hcid_modulate(exp, HCID_TYPE_CALLSIGN, 0.3f, m, HCID_MAX_SAMPLES);
            int maxd = 0;
            for (int i = 0; i < k && i < n; i++) { int dd = abs(m[i] - x[i]); if (dd > maxd) maxd = dd; }
            ok = k == n && maxd <= 1;
            if (!ok) printf("  modulador: %d vs %d muestras, dif. máx %d\n", k, n, maxd);
            free(m);
        }
        printf("%-4s %-36s esperado [%s] obtenido [%s]\n", ok ? "OK" : "FALLA", line, exp, got);
        fails += !ok; total++;
        free(x); free(scratch);
    }
    fclose(f);
    printf("%d/%d vectores correctos\n", total - fails, total);
    return fails ? 1 : 0;
}
