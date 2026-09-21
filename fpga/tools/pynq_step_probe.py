#!/usr/bin/env python3
import argparse
import mmap
import os
import struct
import time


DVP = 0x40000000
DMA = 0x40400000
S2MM_DMACR = 0x30
S2MM_DMASR = 0x34
S2MM_DA = 0x48
S2MM_LENGTH = 0x58


def log(msg):
    print(msg, flush=True)


class Mmio:
    def __init__(self, base):
        log(f"mmio_open_enter 0x{base:08x}")
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        log(f"mmio_open_exit 0x{base:08x} fd={self.fd}")
        log(f"mmio_mmap_enter 0x{base:08x}")
        self.mem = mmap.mmap(
            self.fd,
            0x1000,
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
            offset=base,
        )
        log(f"mmio_mmap_exit 0x{base:08x}")
        self.base = base
        log(f"mmio_ready 0x{base:08x}")

    def r32(self, off):
        log(f"r32_enter 0x{self.base + off:08x}")
        val = struct.unpack_from("<I", self.mem, off)[0]
        log(f"r32_exit 0x{self.base + off:08x}=0x{val:08x}")
        return val

    def w32(self, off, val):
        log(f"w32_enter 0x{self.base + off:08x}=0x{val:08x}")
        struct.pack_into("<I", self.mem, off, val & 0xFFFFFFFF)
        log(f"w32_exit 0x{self.base + off:08x}")

    def close(self):
        log(f"mmio_close 0x{self.base:08x}")
        self.mem.close()
        os.close(self.fd)


class CmaBuffer:
    def __init__(self, length):
        from cffi import FFI

        log(f"cma_import length={length}")
        self.ffi = FFI()
        self.ffi.cdef(
            """
            void *cma_alloc(uint32_t len, uint32_t cacheable);
            unsigned long cma_get_phy_addr(void *buf);
            void cma_free(void *buf);
            uint32_t cma_pages_available();
            """
        )
        self.lib = self.ffi.dlopen("/usr/lib/libcma.so")
        log(f"cma_pages_before={self.lib.cma_pages_available()}")
        self.ptr = self.lib.cma_alloc(length, 0)
        if self.ptr == self.ffi.NULL:
            raise RuntimeError("cma_alloc failed")
        self.length = length
        self.phys_addr = int(self.lib.cma_get_phy_addr(self.ptr))
        self.view = self.ffi.buffer(self.ptr, length)
        log(f"cma_allocated phys=0x{self.phys_addr:08x}")

    def clear_head(self):
        log("cma_clear_head_enter")
        self.view[:4096] = b"\x00" * 4096
        log("cma_clear_head_exit")

    def close(self):
        if self.ptr != self.ffi.NULL:
            log("cma_free_enter")
            self.lib.cma_free(self.ptr)
            self.ptr = self.ffi.NULL
            log("cma_free_exit")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "op",
        choices=[
            "read",
            "write-dvp-zero",
            "cma-only",
            "cma-read",
            "cma-write-dvp-zero",
            "dma-reset",
            "dma-arm-no-stream",
        ],
    )
    parser.add_argument("--length", type=int, default=640 * 480 * 2)
    args = parser.parse_args()

    log(f"op_start {args.op}")
    cma = None
    dvp = None
    dma = None
    try:
        if args.op.startswith("cma") or args.op == "dma-arm-no-stream":
            cma = CmaBuffer(args.length)
            cma.clear_head()
            if args.op == "cma-only":
                return

        if args.op in ("read", "write-dvp-zero", "cma-read", "cma-write-dvp-zero"):
            dvp = Mmio(DVP)
            if args.op in ("read", "cma-read"):
                dvp.r32(0x00)
                dvp.r32(0x04)
                dvp.r32(0x08)
            else:
                dvp.w32(0x00, 0x00000000)
                dvp.r32(0x00)
                dvp.r32(0x04)
            return

        if args.op == "dma-reset":
            dma = Mmio(DMA)
            dma.w32(S2MM_DMACR, 0x00000004)
            time.sleep(0.01)
            dma.r32(S2MM_DMACR)
            dma.r32(S2MM_DMASR)
            return

        if args.op == "dma-arm-no-stream":
            dma = Mmio(DMA)
            dma.w32(S2MM_DMACR, 0x00000004)
            time.sleep(0.01)
            dma.r32(S2MM_DMACR)
            dma.w32(S2MM_DMACR, 0x00000001)
            dma.w32(S2MM_DA, cma.phys_addr)
            dma.w32(S2MM_LENGTH, args.length)
            time.sleep(0.1)
            dma.r32(S2MM_DMASR)
            dma.w32(S2MM_DMACR, 0x00000004)
            time.sleep(0.01)
            dma.r32(S2MM_DMASR)
            return
    finally:
        if dma is not None:
            dma.close()
        if dvp is not None:
            dvp.close()
        if cma is not None:
            cma.close()
        log(f"op_done {args.op}")


if __name__ == "__main__":
    main()
