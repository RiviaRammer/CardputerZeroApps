#include "st25r3916.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static volatile sig_atomic_t stopping;

static void stop_on_signal(int signal_number)
{
    (void)signal_number;
    stopping = 1;
}

static void usage(FILE *out)
{
    fputs("Usage:\n"
          "  amiibo-zero check DUMP.bin\n"
          "  amiibo-zero probe [--spi DEVICE]\n"
          "  amiibo-zero emulate DUMP.bin [--uid-pool FILE] [--signature FILE]\n"
          "                               [--spi DEVICE] [--gpiochip DEVICE] [--trace]\n",
          out);
}

static int read_exact(const char *path, uint8_t *buffer, size_t size)
{
    FILE *file = fopen(path, "rb");
    if (!file) {
        perror(path);
        return -1;
    }
    size_t count = fread(buffer, 1, size, file);
    int extra = fgetc(file);
    int failed = ferror(file);
    fclose(file);
    if (failed) {
        fprintf(stderr, "Could not read %s.\n", path);
    } else if (count != size || extra != EOF) {
        fprintf(stderr, "%s must contain exactly %zu bytes.\n", path, size);
        failed = 1;
    }
    return failed ? -1 : 0;
}

static bool load_pool(const char *path, struct ntag215 *variants, size_t *count,
                      const struct ntag215 *original)
{
    FILE *file = fopen(path, "rb");
    if (!file) {
        perror(path);
        return false;
    }

    uint8_t dump[NTAG_DUMP_SIZE];
    size_t bytes;
    while ((bytes = fread(dump, 1, sizeof(dump), file)) == sizeof(dump)) {
        if (*count == AMIIBO_POOL_LIMIT || !ntag215_load(&variants[*count], dump)) {
            break;
        }
        const uint8_t *uid = variants[*count].uid;
        bool duplicate = memcmp(uid, original->uid, sizeof(original->uid)) == 0;
        for (size_t i = 0; i < *count && !duplicate; ++i) {
            duplicate = memcmp(uid, variants[i].uid, sizeof(variants[i].uid)) == 0;
        }
        if (duplicate) {
            break;
        }
        ++*count;
    }
    bool valid = !ferror(file) && bytes == 0 && *count != 0;
    fclose(file);
    if (!valid) {
        fprintf(stderr, "%s must contain 1-256 complete 540-byte variants with distinct valid UIDs.\n", path);
    }
    return valid;
}

int main(int argc, char **argv)
{
    if (argc == 2 && (strcmp(argv[1], "--help") == 0 || strcmp(argv[1], "-h") == 0)) {
        usage(stdout);
        return 0;
    }
    if (argc < 2) {
        usage(stderr);
        return 2;
    }

    setvbuf(stdout, NULL, _IOLBF, 0);
    bool probe = strcmp(argv[1], "probe") == 0;
    bool emulate = strcmp(argv[1], "emulate") == 0;
    bool check = strcmp(argv[1], "check") == 0;
    if ((!probe && !emulate && !check) || (!probe && argc < 3) || (check && argc != 3)) {
        usage(stderr);
        return 2;
    }

    const char *spi = "/dev/spidev0.2";
    const char *gpio_chip = "/dev/gpiochip0";
    const char *signature = NULL;
    const char *pool = NULL;
    bool trace = false;
    for (int i = probe ? 2 : 3; i < argc;) {
        if (emulate && strcmp(argv[i], "--trace") == 0) {
            trace = true;
            ++i;
            continue;
        }
        if (i + 1 == argc) {
            usage(stderr);
            return 2;
        }
        if (strcmp(argv[i], "--spi") == 0) {
            spi = argv[i + 1];
        } else if (emulate && strcmp(argv[i], "--gpiochip") == 0) {
            gpio_chip = argv[i + 1];
        } else if (emulate && strcmp(argv[i], "--signature") == 0) {
            signature = argv[i + 1];
        } else if (emulate && strcmp(argv[i], "--uid-pool") == 0) {
            pool = argv[i + 1];
        } else {
            usage(stderr);
            return 2;
        }
        i += 2;
    }
    if (probe) {
        return cap_probe(spi);
    }

    uint8_t dump[NTAG_DUMP_SIZE];
    struct ntag215 original;
    if (read_exact(argv[2], dump, sizeof(dump)) < 0) {
        return 1;
    }
    if (!ntag215_load(&original, dump)) {
        fputs("Invalid NTAG215 UID or BCC. Use a raw amiibo dump in page order.\n", stderr);
        return 1;
    }
    if (signature && read_exact(signature, original.signature, sizeof(original.signature)) < 0) {
        return 1;
    }
    puts("Loaded a 540-byte NTAG215 image with valid UID check bytes.");
    if (check) {
        return 0;
    }
    struct sigaction handler = {.sa_handler = stop_on_signal};
    if (sigemptyset(&handler.sa_mask) < 0 ||
        sigaction(SIGINT, &handler, NULL) < 0 ||
        sigaction(SIGTERM, &handler, NULL) < 0) {
        perror("sigaction");
        return 1;
    }
    struct ntag215 *variants = NULL;
    size_t count = 0;
    if (pool) {
        variants = calloc(AMIIBO_POOL_LIMIT, sizeof(*variants));
        if (!variants) {
            perror("UID pool");
            return 1;
        }
        if (!load_pool(pool, variants, &count, &original)) {
            free(variants);
            return 1;
        }
    }
    const struct amiibo_images images = {&original, variants, count};
    int result = cap_emulate(spi, gpio_chip, &images, trace, &stopping);
    free(variants);
    return result;
}
