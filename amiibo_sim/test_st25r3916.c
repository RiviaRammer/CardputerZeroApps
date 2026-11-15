#include <assert.h>
#include <stdarg.h>
#include <sys/ioctl.h>

static int mock_ioctl(int fd, unsigned long request, ...);
#define ioctl mock_ioctl
#include "st25r3916.c"
#undef ioctl

static uint8_t interrupts[4];
static unsigned irq_reads;
static unsigned transmissions;
static unsigned fifo_clears;

static int mock_ioctl(int fd, unsigned long request, ...)
{
    (void)fd;
    assert(request == SPI_IOC_MESSAGE(1));
    va_list args;
    va_start(args, request);
    struct spi_ioc_transfer *message = va_arg(args, struct spi_ioc_transfer *);
    va_end(args);
    const uint8_t *tx = (const uint8_t *)(uintptr_t)message->tx_buf;
    uint8_t *rx = (uint8_t *)(uintptr_t)message->rx_buf;
    if (rx) memset(rx, 0, message->len);
    switch (tx[0]) {
    case SPI_READ | REG_IRQ_MAIN:
        assert(message->len == 5);
        memcpy(rx + 1, interrupts, 4);
        memset(interrupts, 0, 4);
        ++irq_reads;
        break;
    case SPI_READ | REG_FIFO_STATUS:
        rx[1] = 4; // READ command, page number and two CRC bytes.
        break;
    case SPI_FIFO_READ:
        rx[1] = 0x30;
        rx[2] = 4;
        break;
    case CMD_CLEAR_FIFO: ++fifo_clears; break;
    case CMD_TX_CRC: ++transmissions; break;
    default: break;
    }
    return (int)message->len;
}

int main(void)
{
    struct listener dev = {.active = true, .field = true};
    struct ntag215 tag = {0};

    // A CRC error can be collected before RX_END. It must survive that read.
    interrupts[0] = IRQ_RX_START;
    interrupts[2] = 0x80;
    assert(handle_irq(&dev, &tag) == 0);
    assert(dev.pending_rx_errors == 0x80);
    interrupts[0] = IRQ_RX_END;
    assert(handle_irq(&dev, &tag) == 0);
    assert(dev.rx_errors == 1 && transmissions == 0 && fifo_clears == 1);
    assert(dev.pending_rx_errors == 0);

    // The next clean frame must get a response, with success recorded at TX_END.
    interrupts[0] = IRQ_RX_START | IRQ_RX_END;
    assert(handle_irq(&dev, &tag) == 0);
    assert(transmissions == 1 && dev.tx_pending && !dev.read_reply_sent);
    interrupts[0] = IRQ_TX_END;
    assert(handle_irq(&dev, &tag) == 0);
    assert(!dev.tx_pending && dev.read_reply_sent && dev.replies == 1);

    dev.pending_rx_errors = 0x80;
    assert(arm_target(&dev) == 0);
    assert(dev.pending_rx_errors == 0);
    assert(irq_reads == 4);
    puts("ST25R3916 IRQ regression tests passed.");
    return 0;
}
