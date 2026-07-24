/**
 * Zynq-7000 Bare-Metal QSPI Flash Stub
 * Runs in OCM (On-Chip Memory) to provide ultra-fast SPI-NOR programming
 * bypassing JTAG latency.
 */

#include <stdint.h>
#include <stddef.h>

/* ===================================================================== */
/* ZYNQ-7000 HARDWARE REGISTER DEFINITIONS                               */
/* ===================================================================== */

#define QSPI_BASE_ADDR              0xE000D000
#define QSPI_CONFIG_REG             (QSPI_BASE_ADDR + 0x00)
#define QSPI_STATUS_REG             (QSPI_BASE_ADDR + 0x04)
#define QSPI_TXD_FIFO_REG           (QSPI_BASE_ADDR + 0x1C)
#define QSPI_RXD_FIFO_REG           (QSPI_BASE_ADDR + 0x20)
#define QSPI_TXD_1BYTE_REG          (QSPI_BASE_ADDR + 0x80)
#define QSPI_TXD_2BYTE_REG          (QSPI_BASE_ADDR + 0x84)
#define QSPI_TXD_3BYTE_REG          (QSPI_BASE_ADDR + 0x88)

/* QSPI Configuration Bitmasks */
#define QSPI_CONFIG_MANUAL_START    (1 << 16)
#define QSPI_CONFIG_PCS_ALL_HIGH    (0xF << 10)
#define QSPI_CONFIG_PCS_CS0_LOW     (0xE << 10)

/* 
 * Base QSPI Config: 
 * LEG_FLSH (Bit 31) | MANUAL_START_EN (Bit 14) | BAUD_DIV_16 (Bits 5:3 = 3) | 
 * MANUAL_CS_EN (Bit 1) | MASTER_MODE (Bit 0) = 0x8000401B 
 */
#define QSPI_CONFIG_BASE_VAL        0x8000401B

#define QSPI_STATUS_RX_NOT_EMPTY    (1 << 4)
#define QSPI_FIFO_DEPTH_BYTES       252

/* ===================================================================== */
/* SPI-NOR FLASH COMMANDS & CONSTANTS                                    */
/* ===================================================================== */

#define FLASH_CMD_WREN              0x06
#define FLASH_CMD_RDSR              0x05
#define FLASH_CMD_PP                0x02
#define FLASH_CMD_SE                0xD8

#define FLASH_SR1_WIP_BIT           0x01
#define FLASH_SR1_WEL_BIT           0x02
#define FLASH_PAGE_SIZE             256
#define FLASH_SECTOR_MASK           0xFFFF

/* ===================================================================== */
/* OCM MAILBOX ARCHITECTURE                                              */
/* ===================================================================== */

#define MBOX_BASE_ADDR              0x00010000
#define MBOX_CMD_STATUS             ((volatile uint32_t *)(MBOX_BASE_ADDR + 0x00))
#define MBOX_FLASH_OFFSET           ((volatile uint32_t *)(MBOX_BASE_ADDR + 0x04))
#define MBOX_DATA_LENGTH            ((volatile uint32_t *)(MBOX_BASE_ADDR + 0x08))
#define MBOX_DATA_BUFFER            ((const uint8_t *)(MBOX_BASE_ADDR + 0x40))

/* Mailbox State Machine Codes */
#define STATE_IDLE                  0x00
#define CMD_WRITE_CHUNK             0x01
#define CMD_ERASE_SECTOR            0x02
#define STATUS_OK_DONE              0xAA
#define STATUS_ERROR                0xEE

/* ===================================================================== */
/* UTILITY FUNCTIONS                                                     */
/* ===================================================================== */

static inline void reg_write32(uint32_t addr, uint32_t val) {
    *((volatile uint32_t *)addr) = val;
}

static inline uint32_t reg_read32(uint32_t addr) {
    return *((volatile uint32_t *)addr);
}

/* ===================================================================== */
/* CORE SPI TRANSFER ENGINE                                              */
/* ===================================================================== */

/**
 * Executes a full SPI transfer, managing the CS line and safely chunking 
 * data to prevent TX FIFO underrun/overrun on large payloads.
 */
static void spi_transfer(const uint8_t *tx_data, uint8_t *rx_data, uint32_t length) {
    uint32_t cfg_idle = QSPI_CONFIG_BASE_VAL | QSPI_CONFIG_PCS_ALL_HIGH;
    uint32_t cfg_cs0  = QSPI_CONFIG_BASE_VAL | QSPI_CONFIG_PCS_CS0_LOW;

    /* Ensure controller is in idle state before starting */
    reg_write32(QSPI_CONFIG_REG, cfg_idle);
    reg_write32(QSPI_STATUS_REG, 0x7F); /* Clear sticky flags */

    /* Drain any stale data in RX FIFO */
    while (reg_read32(QSPI_STATUS_REG) & QSPI_STATUS_RX_NOT_EMPTY) {
        reg_read32(QSPI_RXD_FIFO_REG);
    }

    /* Assert Chip Select (CS goes LOW) */
    reg_write32(QSPI_CONFIG_REG, cfg_cs0);
    
    uint32_t bytes_transferred = 0;

    /* Burst data in safe chunks up to the hardware FIFO limit */
    while (bytes_transferred < length) {
        uint32_t chunk = length - bytes_transferred;
        if (chunk > QSPI_FIFO_DEPTH_BYTES) {
            chunk = QSPI_FIFO_DEPTH_BYTES;
        }

        uint32_t words = chunk / 4;
        uint32_t remainder = chunk % 4;
        uint32_t tx_offset = bytes_transferred;

        /* Push fully-aligned 32-bit words */
        for (uint32_t i = 0; i < words; i++) {
            uint32_t w = tx_data[tx_offset] | (tx_data[tx_offset+1] << 8) |
                         (tx_data[tx_offset+2] << 16) | (tx_data[tx_offset+3] << 24);
            reg_write32(QSPI_TXD_FIFO_REG, w);
            tx_offset += 4;
        }

        /* Push unaligned trailing bytes using dedicated registers */
        if (remainder == 1) {
            reg_write32(QSPI_TXD_1BYTE_REG, tx_data[tx_offset]);
        } else if (remainder == 2) {
            uint32_t w = tx_data[tx_offset] | (tx_data[tx_offset+1] << 8);
            reg_write32(QSPI_TXD_2BYTE_REG, w);
        } else if (remainder == 3) {
            uint32_t w = tx_data[tx_offset] | (tx_data[tx_offset+1] << 8) | (tx_data[tx_offset+2] << 16);
            reg_write32(QSPI_TXD_3BYTE_REG, w);
        }

        /* Trigger SPI bus transmission */
        reg_write32(QSPI_CONFIG_REG, cfg_cs0 | QSPI_CONFIG_MANUAL_START);

        /* Wait for physical transmission to complete */
        while (reg_read32(QSPI_CONFIG_REG) & QSPI_CONFIG_MANUAL_START) {
            /* Busy wait */
        }

        /* Pop RX FIFO if caller requested incoming data */
        uint32_t rx_offset = bytes_transferred;
        uint32_t rx_words_expected = words + (remainder > 0 ? 1 : 0);
        uint32_t rx_words_read = 0;

        while (rx_words_read < rx_words_expected) {
            if (reg_read32(QSPI_STATUS_REG) & QSPI_STATUS_RX_NOT_EMPTY) {
                uint32_t w = reg_read32(QSPI_RXD_FIFO_REG);
                
                if (rx_data != NULL) {
                    uint32_t bytes_to_copy = 4;
                    if (rx_words_read == words && remainder > 0) {
                        bytes_to_copy = remainder;
                    }
                    for (uint32_t b = 0; b < bytes_to_copy; b++) {
                        rx_data[rx_offset++] = (w >> (b * 8)) & 0xFF;
                    }
                }
                rx_words_read++;
            }
        }
        bytes_transferred += chunk;
    }

    /* De-assert Chip Select (CS goes HIGH) */
    reg_write32(QSPI_CONFIG_REG, cfg_idle);
}

/* ===================================================================== */
/* FLASH PROTOCOL IMPLEMENTATION                                         */
/* ===================================================================== */

static uint8_t flash_read_status(void) {
    uint8_t tx[2] = {FLASH_CMD_RDSR, 0x00};
    uint8_t rx[2] = {0x00, 0x00};
    spi_transfer(tx, rx, 2);
    return rx[1];
}

static void flash_wait_ready(void) {
    while (flash_read_status() & FLASH_SR1_WIP_BIT) {
        /* Wait until Write-In-Progress bit clears */
    }
}

static void flash_write_enable(void) {
    uint8_t tx[1] = {FLASH_CMD_WREN};
    
    while (1) {
        spi_transfer(tx, NULL, 1);
        if (flash_read_status() & FLASH_SR1_WEL_BIT) {
            break; /* Write Enable Latch is successfully set */
        }
    }
}

static void flash_page_program(uint32_t offset, const uint8_t *data, uint32_t length) {
    /* Maximum transaction size: 1 byte cmd + 3 bytes addr + 256 bytes payload */
    uint8_t tx_buffer[260];
    
    tx_buffer[0] = FLASH_CMD_PP;
    tx_buffer[1] = (offset >> 16) & 0xFF;
    tx_buffer[2] = (offset >> 8) & 0xFF;
    tx_buffer[3] = offset & 0xFF;

    for (uint32_t i = 0; i < length; i++) {
        tx_buffer[4 + i] = data[i];
    }

    /* Hardware spi_transfer handles the burst slicing internally */
    spi_transfer(tx_buffer, NULL, 4 + length);
}

/* ===================================================================== */
/* MAIN MAILBOX LOOP                                                     */
/* ===================================================================== */

int main(void) {
    /* Acknowledge boot to Python */
    *MBOX_CMD_STATUS = STATE_IDLE;

    while (1) {
        uint32_t cmd = *MBOX_CMD_STATUS;

        if (cmd == CMD_WRITE_CHUNK) {
            uint32_t target_offset = *MBOX_FLASH_OFFSET;
            uint32_t total_length  = *MBOX_DATA_LENGTH;
            uint32_t data_idx      = 0;
            
            /* Program the entire chunk respecting 256-byte page boundaries */
            while (data_idx < total_length) {
                uint32_t current_offset = target_offset + data_idx;
                uint32_t page_boundary_space = FLASH_PAGE_SIZE - (current_offset % FLASH_PAGE_SIZE);
                
                uint32_t chunk_size = total_length - data_idx;
                if (chunk_size > page_boundary_space) {
                    chunk_size = page_boundary_space;
                }
                
                flash_write_enable();
                flash_page_program(current_offset, &MBOX_DATA_BUFFER[data_idx], chunk_size);
                flash_wait_ready();
                
                data_idx += chunk_size;
            }
            
            /* Notify Python of successful completion */
            *MBOX_CMD_STATUS = STATUS_OK_DONE;

        } else if (cmd == CMD_ERASE_SECTOR) {
            uint32_t target_offset = *MBOX_FLASH_OFFSET;
            
            /* Safely align to 64KB boundary */
            target_offset &= ~FLASH_SECTOR_MASK;
            
            uint8_t tx[4] = {
                FLASH_CMD_SE,
                (target_offset >> 16) & 0xFF,
                (target_offset >> 8) & 0xFF,
                target_offset & 0xFF
            };
            
            flash_write_enable();
            spi_transfer(tx, NULL, 4);
            flash_wait_ready();
            
            *MBOX_CMD_STATUS = STATUS_OK_DONE;
        }
    }

    return 0;
}

/* Minimal entry point for bare-metal execution */
void _start(void) {
    main();
}