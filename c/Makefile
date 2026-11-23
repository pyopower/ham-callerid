CFLAGS ?= -O2 -std=c99 -Wall -Wextra
test: test_hcid
	./test_hcid ../vectors
test_hcid: test_hcid.c hcid.h
	$(CC) $(CFLAGS) -o $@ test_hcid.c -lm
clean:
	rm -f test_hcid
.PHONY: test clean
