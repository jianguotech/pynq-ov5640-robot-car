#!/usr/bin/env python3
import mmap
import os
import struct


DVP = 0x40000000
DMA = 0x40400000


def read32(fd, addr):
    page = addr & ~0xFFF
    off = addr - page
    mem = mmap.mmap(
        fd,
        0x1000,
        mmap.MAP_SHARED,
        mmap.PROT_READ | mmap.PROT_WRITE,
        offset=page,
    )
    try:
        return struct.unpack_from("<I", mem, off)[0]
    finally:
        mem.close()


def main():
    regs = [
        ("dvp_ctrl", DVP + 0x00),
        ("dvp_status", DVP + 0x04),
        ("dvp_frame", DVP + 0x08),
        ("dvp_line", DVP + 0x0C),
        ("dvp_pixel", DVP + 0x10),
        ("dvp_last_pixel", DVP + 0x14),
        ("dvp_last_xy", DVP + 0x18),
        ("dma_s2mm_dmacr", DMA + 0x30),
        ("dma_s2mm_dmasr", DMA + 0x34),
        ("dma_s2mm_da", DMA + 0x48),
        ("dma_s2mm_length", DMA + 0x58),
    ]

    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        for name, addr in regs:
            print(f"{name}@0x{addr:08x}=0x{read32(fd, addr):08x}")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
