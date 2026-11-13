#include "ntag215.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#define FRAME(...) (const uint8_t[]){__VA_ARGS__}, sizeof((const uint8_t[]){__VA_ARGS__})

int main(void)
{
    uint8_t dump[NTAG_DUMP_SIZE] = {4, 1, 2, 0x8F, 3, 4, 5, 6, 4};
    const uint8_t password[] = {0xA8, 0x53, 0xAC, 0x57};
    const uint8_t version[] = {0, 4, 4, 2, 1, 0, 0x11, 3};
    const uint8_t blank_signature[32] = {0};
    struct ntag215 tag;
    struct ntag_reply reply;

    dump[0x83 * 4 + 3] = 4;
    dump[0x84 * 4] = 0x80;
    dump[0x82 * 4] = 1;
    memset(dump + 0x85 * 4, 0xFF, 8);
    dump[16] = 42;
    assert(ntag215_load(&tag, dump));
    assert(memcmp(tag.password, password, sizeof(password)) == 0);

    ntag215_command(&tag, FRAME(0x60), &reply);
    assert(reply.kind == NTAG_BYTES && reply.length == sizeof(version));
    assert(memcmp(reply.data, version, sizeof(version)) == 0);
    ntag215_command(&tag, FRAME(0x30, 4), &reply);
    assert(reply.kind == NTAG_BYTES && reply.length == 16 && reply.data[0] == 42);
    ntag215_command(&tag, FRAME(0x1B, 0xFF, 0xFF, 0xFF, 0xFF), &reply);
    assert(reply.kind == NTAG_NIBBLE && reply.data[0] == 0);
    ntag215_command(&tag, FRAME(0x1B, 0xA8, 0x53, 0xAC, 0x57), &reply);
    assert(reply.kind == NTAG_BYTES && reply.length == 2);
    assert(reply.data[0] == 0x80 && reply.data[1] == 0x80);

    ntag215_command(&tag, FRAME(0x3A, 0, 134), &reply);
    assert(reply.kind == NTAG_BYTES && reply.length == 540 && reply.data[16] == 42);
    for (size_t i = 532; i < reply.length; ++i) {
        assert(reply.data[i] == 0);
    }
    ntag215_command(&tag, FRAME(0x30, 134), &reply);
    assert(reply.kind == NTAG_BYTES && reply.length == 16 && reply.data[4] == 4);
    ntag215_command(&tag, FRAME(0x3A, 4, 3), &reply);
    assert(reply.kind == NTAG_NIBBLE);
    ntag215_command(&tag, FRAME(0x3A, 0, 135), &reply);
    assert(reply.kind == NTAG_NIBBLE);

    ntag215_command(&tag, FRAME(0xA2, 4, 1, 2, 3, 4), &reply);
    assert(reply.kind == NTAG_NIBBLE && reply.data[0] == 0x0A && tag.pages[16] == 1);
    ntag215_command(&tag, FRAME(0xA2, 129, 1, 2, 3, 4), &reply);
    assert(reply.data[0] == 0x0A && tag.pages[129 * 4] == 1);
    ntag215_command(&tag, FRAME(0xA2, 3, 1, 2, 3, 4), &reply);
    assert(reply.data[0] == 0 && tag.pages[3 * 4] == 0);
    ntag215_command(&tag, FRAME(0xA2, 130, 4, 3, 2, 1), &reply);
    assert(reply.data[0] == 0 && tag.pages[130 * 4] == 1);
    ntag215_command(&tag, FRAME(0x50, 0), &reply);
    assert(reply.kind == NTAG_HALT);

    for (uint8_t address = 0; address <= 2; ++address) {
        ntag215_command(&tag, FRAME(0x39, address), &reply);
        assert(reply.kind == NTAG_BYTES && reply.length == 3 && reply.data[2] == 0);
        ntag215_command(&tag, FRAME(0x3E, address), &reply);
        assert(reply.kind == NTAG_BYTES && reply.length == 1 && reply.data[0] == 0xBD);
    }
    ntag215_command(&tag, FRAME(0x39, 3), &reply);
    assert(reply.kind == NTAG_NIBBLE);
    ntag215_command(&tag, FRAME(0x3C, 0), &reply);
    assert(reply.kind == NTAG_BYTES && reply.length == sizeof(blank_signature));
    assert(memcmp(reply.data, blank_signature, sizeof(blank_signature)) == 0);
    tag.signature[31] = 42;
    ntag215_command(&tag, FRAME(0x3C, 0), &reply);
    assert(reply.data[31] == 42);

    dump[8] ^= 1;
    assert(!ntag215_load(&tag, dump));
    puts("NTAG215 protocol tests passed.");
    return 0;
}
