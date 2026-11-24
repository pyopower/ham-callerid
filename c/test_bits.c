/* hcid_frame_bits() contra vectors/bits.txt, compilado SOLO con el codificador (sin libm). */
#define HCID_ENCODER_ONLY
#define HCID_IMPLEMENTATION
#include "hcid.h"
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    char path[1100], line[2048];
    snprintf(path, sizeof path, "%s/bits.txt", argc > 1 ? argv[1] : "vectors");
    FILE *f = fopen(path, "r");
    if (!f) { perror(path); return 2; }
    int fails = 0, total = 0;
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#') continue;
        line[strcspn(line, "\r\n")] = 0;
        char *sp = strchr(line, ' ');
        if (!sp) continue;
        *sp = 0;
        const char *want = sp + 1;
        uint8_t bits[HCID_MAX_BITS];
        int m = hcid_frame_bits(line, HCID_TYPE_CALLSIGN, bits, HCID_MAX_BITS);
        int ok = m == (int)strlen(want);
        for (int i = 0; ok && i < m; i++) ok = bits[i] == (uint8_t)(want[i] - '0');
        printf("%s %-12s %d bits\n", ok ? "OK  " : "FAIL", line, m);
        fails += !ok; total++;
    }
    uint8_t small[10];
    if (hcid_frame_bits("C31AG", HCID_TYPE_CALLSIGN, small, 10) != 0) { puts("FAIL cap"); fails++; }
    if (hcid_frame_bits("c31ag", HCID_TYPE_CALLSIGN, small, 10) != 0) { puts("FAIL minúsculas"); fails++; }
    printf("%d/%d\n", total - fails, total);
    return fails != 0;
}
