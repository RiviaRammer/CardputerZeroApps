CC ?= cc
ARM64_CC ?= aarch64-linux-gnu-gcc
CPPFLAGS += -D_POSIX_C_SOURCE=200809L
CFLAGS ?= -O2
CFLAGS += -std=c11 -Wall -Wextra -Wpedantic

.PHONY: all arm64 test clean

all: build/amiibo-zero

build/amiibo-zero: main.c ntag215.c st25r3916.c ntag215.h st25r3916.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) main.c ntag215.c st25r3916.c -o $@ $(LDLIBS)

arm64: build/amiibo-zero-arm64

build/amiibo-zero-arm64: main.c ntag215.c st25r3916.c ntag215.h st25r3916.h
	mkdir -p build
	$(ARM64_CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) main.c ntag215.c st25r3916.c -o $@ $(LDLIBS)

test: build/test_ntag215
	./build/test_ntag215

build/test_ntag215: test_ntag215.c ntag215.c ntag215.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) test_ntag215.c ntag215.c -o $@ $(LDLIBS)

clean:
	rm -f build/amiibo-zero build/amiibo-zero-arm64 build/test_ntag215
