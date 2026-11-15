CC ?= cc
ARM64_CC ?= aarch64-linux-gnu-gcc
CPPFLAGS += -D_POSIX_C_SOURCE=200809L
CFLAGS ?= -O2
CFLAGS += -std=c11 -Wall -Wextra -Wpedantic

cli_sources := src/main.c src/ntag215.c src/st25r3916.c
cli_headers := src/ntag215.h src/st25r3916.h

.PHONY: all arm64 clean

all: build/amiibo-zero

build/amiibo-zero: $(cli_sources) $(cli_headers)
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) $(cli_sources) -o $@ $(LDLIBS)

arm64: build/amiibo-zero-arm64

build/amiibo-zero-arm64: $(cli_sources) $(cli_headers)
	mkdir -p build
	$(ARM64_CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) $(cli_sources) -o $@ $(LDLIBS)

clean:
	rm -f build/amiibo-zero build/amiibo-zero-arm64
