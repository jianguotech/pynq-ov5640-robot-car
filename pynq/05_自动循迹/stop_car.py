#!/usr/bin/env python3
import argparse
import mmap
import os
import struct
import time


REG_CTRL = 0x00
REG_STEP_HIGH_TICKS = 0x04
REG_WHEEL_A_PERIOD_TICKS = 0x08
REG_WHEEL_B_PERIOD_TICKS = 0x0C
REG_WHEEL_C_PERIOD_TICKS = 0x10
REG_WHEEL_D_PERIOD_TICKS = 0x14
REG_DIR_BITS = 0x18
REG_STATUS = 0x1C
REG_VERSION = 0x20
REG_HEARTBEAT = 0x24

CTRL_DISABLE = 0x00
CTRL_HOLD = 0x0B


def parse_args():
    parser = argparse.ArgumentParser(description="Force the AXI motor IP into stop/disable state.")
    parser.add_argument("--base", type=lambda x: int(x, 0), default=0x40000000)
    parser.add_argument("--span", type=lambda x: int(x, 0), default=0x1000)
    return parser.parse_args()


def main():
    args = parse_args()
    if os.geteuid() != 0:
        print("ERROR: run with sudo.")
        return 2

    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    mem = mmap.mmap(
        fd,
        args.span,
        mmap.MAP_SHARED,
        mmap.PROT_READ | mmap.PROT_WRITE,
        offset=args.base,
    )

    def read_reg(offset):
        return struct.unpack_from("<I", mem, offset)[0]

    def write_reg(offset, value):
        struct.pack_into("<I", mem, offset, value & 0xFFFFFFFF)

    print(
        "STOP_BEGIN VERSION=0x%08X CTRL=0x%08X STATUS=0x%08X"
        % (read_reg(REG_VERSION), read_reg(REG_CTRL), read_reg(REG_STATUS))
    )
    for _ in range(5):
        write_reg(REG_CTRL, CTRL_HOLD)
        write_reg(REG_STEP_HIGH_TICKS, 0)
        for offset in (
            REG_WHEEL_A_PERIOD_TICKS,
            REG_WHEEL_B_PERIOD_TICKS,
            REG_WHEEL_C_PERIOD_TICKS,
            REG_WHEEL_D_PERIOD_TICKS,
        ):
            write_reg(offset, 0)
        write_reg(REG_DIR_BITS, 0)
        write_reg(REG_CTRL, CTRL_DISABLE)
        time.sleep(0.05)

    print(
        "STOP_DONE CTRL=0x%08X STATUS=0x%08X HEARTBEAT=0x%08X"
        % (read_reg(REG_CTRL), read_reg(REG_STATUS), read_reg(REG_HEARTBEAT))
    )
    mem.close()
    os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
