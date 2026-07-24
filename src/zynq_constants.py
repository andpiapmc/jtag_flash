import struct

# System Default File Paths
DEFAULT_FSBL_PATH = "ext/fsbl.bin"
DEFAULT_BOOTBLOCK_PATH = "ext/bootblock.bin"


class MpsseOpcodes:
    DISABLE_CLK_DIV5      = b'\x8A'
    TURN_OFF_ADAPTIVE_CLK = b'\x97'
    DISABLE_3_PHASE_CLK   = b'\x8D'
    SET_DATA_BITS_LOW     = b'\x80'
    SET_DATA_BITS_HIGH    = b'\x82'
    SET_TCK_DIVISOR       = b'\x86'
    READ_DATA_BYTES_LSB   = b'\x28'
    SHIFT_BYTES_LSB_RW    = b'\x39'
    SHIFT_BITS_LSB_RW     = b'\x3B'
    SHIFT_TMS_NO_READ     = b'\x4B'
    SHIFT_TMS_READ        = b'\x6B'
    SEND_IMMEDIATE        = b'\x87'

class TmsCommands:
    RESET            = (MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 7, 0xFF)) * 4
    TO_SHIFT_DR      = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 3, 0x02)
    TO_IDLE          = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 2, 0x03)
    EXIT_TO_IDLE     = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 1, 0x01)
    TLR_TO_IDLE      = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 0, 0x00)
    IDLE_TO_SHIFT_IR = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 3, 0x03)
    IDLE_TO_SHIFT_DR = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 2, 0x01)

class JtagInstr:
    FPGA_USERCODE = 0x08
    DAP_DPACC     = 0x0A
    DAP_APACC     = 0x0B
    DAP_IDCODE    = 0x0E

class CoreSightRegs:
    DP_ABORT      = 0x0
    DP_CTRL_STAT  = 0x1
    DP_SELECT     = 0x2
    DP_RDBUFF     = 0x3
    AP_CSW        = 0x0
    AP_TAR        = 0x1
    AP_DRW        = 0x3

class DapReq:
    READ      = 1
    WRITE     = 0
    SHIFT_LEN = 35 
    ACK_MASK  = 0x07
    CLEAR_ERR = 0x0000001E
    PWRUP_REQ = 0x50000000

class AhbApRegs:
    CSW_DEFAULT_32BIT = 0x23000012

class QspiConfig:
    """
    QSPI_CONFIG / QSPI_STATUS register bitfield masks (Zynq TRM UG585).
    MASTER_MODE, MANUAL_CS_EN, and MANUAL_START_EN sit at different bit
    positions than early Zynq documentation suggests; the values below are
    the ones verified against working hardware.
    """
    MASTER_MODE          = (1 << 0)
    MANUAL_CS_EN         = (1 << 1)
    BAUD_DIV_16          = (0x3 << 3)   # SCLK = qspi_ref_clk / 16
    MANUAL_START_EN      = (1 << 14)
    MANUAL_START         = (1 << 16)    # Trigger bit: set to start a transfer, clears on completion
    LEG_FLSH             = (1 << 31)    # Enables the legacy/manual (non-linear) SPI interface
    PCS_ALL_HIGH         = (0xF << 10)  # All 4 chip-selects de-asserted (avoids MISO bus contention)

    # Base manual-mode config with CS not yet asserted; OR with PCS_ALL_HIGH
    # for idle, AND-NOT with it to assert CS0, OR with MANUAL_START to trigger.
    MANUAL_BASE_CFG = LEG_FLSH | MANUAL_START_EN | BAUD_DIV_16 | MANUAL_CS_EN | MASTER_MODE

    STATUS_TX_NOT_FULL   = (1 << 2)
    STATUS_RX_NOT_EMPTY  = (1 << 4)

class ZynqRegs:
    SLCR_BASE        = 0xF8000000
    SLCR_UNLOCK_ADDR = SLCR_BASE + 0x08
    SLCR_LOCK_ADDR   = SLCR_BASE + 0x04
    SLCR_MIO_CTRL_0  = SLCR_BASE + 0x700
    SLCR_UNLOCK_KEY  = 0x0000DF0D
    SLCR_LOCK_KEY    = 0x0000767B
    A9_CPU_RST_CTRL  = SLCR_BASE + 0x244
    APER_CLK_CTRL    = SLCR_BASE + 0x12C
    LQSPI_CLK_CTRL   = SLCR_BASE + 0x14C

    LQSPI_CLK_CTRL_SAFE_VAL = 0x00002821
    LQSPI_CLK_ACT    = 1 << 23

    MIO_PULLUP_BIT   = 1 << 12   # Enables the MIO pin's internal weak pull-up
    MIO_PIN_MUX_QSPI = 0x02      # L1_SEL: routes the pin to the QSPI peripheral function

    QSPI_BASE        = 0xE000D000
    QSPI_CONFIG      = QSPI_BASE + 0x00
    QSPI_STATUS      = QSPI_BASE + 0x04
    QSPI_ENABLE      = QSPI_BASE + 0x14
    QSPI_TXD_FIFO    = QSPI_BASE + 0x1C
    QSPI_RXD_FIFO    = QSPI_BASE + 0x20
    QSPI_TAIL_1BYTE  = QSPI_BASE + 0x80
    QSPI_TAIL_2BYTE  = QSPI_BASE + 0x84
    QSPI_TAIL_3BYTE  = QSPI_BASE + 0x88
    QSPI_LQSPI_CFG   = QSPI_BASE + 0xA0
    
    OCM_BASE_ADDR    = 0x00000000

class FlashCmd:
    READ               = 0x03
    WRSR               = 0x01
    WRSR2              = 0x31
    WREN               = 0x06
    RDSR               = 0x05
    RDSR2              = 0x35
    SE                 = 0xD8
    CE                 = 0xC7
    PP                 = 0x02
    FAST_READ          = 0x0B
    RDID               = 0x9F
    RELEASE_POWER_DOWN = 0xAB
    QE_BIT             = 0x40

    SR1_WIP            = 0x01
    SR1_WEL            = 0x02
    SR2_QE             = 0x02

KNOWN_TAPS = {
    0x0BA00477: "ARM Cortex-A9 CoreSight DAP (Zynq 7000)",
    0x0BA02477: "ARM Cortex-A53 CoreSight DAP (Zynq UltraScale+)"
}

FLASH_MANUFACTURERS = {
    0x01: "Spansion / Cypress (Infineon)",
    0x1C: "EON Silicon Devices",
    0x1F: "Adesto / Dialog Semiconductor",
    0x20: "Micron / ST (Numonyx)",
    0x85: "Puya Semiconductor",
    0x9D: "ISSI (Integrated Silicon Solution Inc.)",
    0xBF: "SST / Microchip",
    0xC2: "Macronix (MXIC)",
    0xC8: "GigaDevice",
    0xEF: "Winbond"
}

FLASH_MEMORY_TYPES = {
    0x9D: { 0x40: "IS25LQ (3.0V Quad)", 0x60: "IS25LP (3.0V Quad)", 0x70: "IS25WP (1.8V Quad)" },
    0xEF: { 0x30: "W25X", 0x40: "W25Q (SPI)", 0x60: "W25Q (QPI)" },
    0xC2: { 0x20: "MX25L (3.0V)", 0x25: "MX25U (1.8V)", 0x28: "MX25R (Ultra Low Power)" },
    0x20: { 0x20: "M25P", 0xBA: "N25Q / MT25QL (3.0V)", 0xBB: "MT25QU (1.8V)" },
    0x01: { 0x02: "S25FL-A/K (3.0V)", 0x20: "S25FL-S (3.0V)" },
    0xC8: { 0x40: "GD25Q (3.0V)", 0x60: "GD25LQ (1.8V)" }
}