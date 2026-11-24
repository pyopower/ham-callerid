CFLAGS ?= -O2 -std=c99 -Wall -Wextra
test: test_hcid test_bits
	./test_hcid ../vectors
	./test_bits ../vectors
test_hcid: test_hcid.c hcid.h
	$(CC) $(CFLAGS) -o $@ test_hcid.c -lm
test_bits: test_bits.c hcid.h
	$(CC) $(CFLAGS) -o $@ test_bits.c
clean:
	rm -f test_hcid test_bits
.PHONY: test clean
