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

DMACR_RS = 0x00000001
DMACR_RESET = 0x00000004
DMASR_HALTED = 0x00000001
DMASR_IDLE = 0x00000002
DMASR_IOC_IRQ = 0x00001000
DMASR_ERR_IRQ = 0x00004000
DMASR_ERR_MASK = 0x00000770

FRAME_BYTES = 640 * 480 * 2


def map_page(fd, addr):
    return mmap.mmap(
        fd,
        0x1000,
        mmap.MAP_SHARED,
        mmap.PROT_READ | mmap.PROT_WRITE,
        offset=addr & ~0xFFF,
    )


class Mmio:
    def __init__(self, base):
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.base = base
        self.mem = map_page(self.fd, base)

    def close(self):
        self.mem.close()
        os.close(self.fd)

    def r32(self, off):
        return struct.unpack_from("<I", self.mem, off)[0]

    def w32(self, off, val):
        struct.pack_into("<I", self.mem, off, val & 0xFFFFFFFF)


def decode_status(status):
    return {
        "xclk_locked": (status >> 0) & 1,
        "frame_seen": (status >> 1) & 1,
        "line_seen": (status >> 2) & 1,
        "pixel_seen": (status >> 3) & 1,
        "vsync_pin": (status >> 4) & 1,
        "href_pin": (status >> 5) & 1,
        "cam_pwdn": (status >> 6) & 1,
        "sccb_init_done": (status >> 7) & 1,
        "sccb_cfg_error": (status >> 8) & 1,
        "cfg_error_code": (status >> 9) & 0xF,
    }


def print_status(prefix, dvp, dma):
    status = dvp.r32(0x04)
    decoded = decode_status(status)
    print(
        f"{prefix} dvp_ctrl=0x{dvp.r32(0x00):08x} dvp_status=0x{status:08x}",
        flush=True,
    )
    print(
        "  "
        + " ".join(f"{key}={value}" for key, value in decoded.items()),
        flush=True,
    )
    print(
        f"  frame={dvp.r32(0x08)} line={dvp.r32(0x0c)} "
        f"pixel={dvp.r32(0x10)} last_pixel=0x{dvp.r32(0x14):04x} "
        f"last_xy=0x{dvp.r32(0x18):08x}",
        flush=True,
    )
    print(
        f"  dma_dmacr=0x{dma.r32(S2MM_DMACR):08x} "
        f"dma_dmasr=0x{dma.r32(S2MM_DMASR):08x} "
        f"da=0x{dma.r32(S2MM_DA):08x} length=0x{dma.r32(S2MM_LENGTH):08x}",
        flush=True,
    )


def checksum_sample(fd, phys_addr, size):
    page = phys_addr & ~0xFFF
    page_off = phys_addr - page
    map_len = ((page_off + size + 0xFFF) // 0x1000) * 0x1000
    mem = mmap.mmap(
        fd,
        map_len,
        mmap.MAP_SHARED,
        mmap.PROT_READ | mmap.PROT_WRITE,
        offset=page,
    )
    try:
        data = mem[page_off : page_off + size]
    finally:
        mem.close()
    nonzero = sum(1 for b in data if b)
    checksum = sum(data) & 0xFFFFFFFF
    head = data[:32].hex()
    return nonzero, checksum, head


class CmaBuffer:
    def __init__(self, length):
        from cffi import FFI

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
        self.ptr = self.lib.cma_alloc(length, 0)
        if self.ptr == self.ffi.NULL:
            raise RuntimeError("cma_alloc failed")
        self.length = length
        self.phys_addr = int(self.lib.cma_get_phy_addr(self.ptr))
        self.view = self.ffi.buffer(self.ptr, length)

    def clear(self):
        self.view[:] = b"\x00" * self.length

    def sample(self, size):
        data = bytes(self.view[:size])
        nonzero = sum(1 for b in data if b)
        checksum = sum(data) & 0xFFFFFFFF
        return nonzero, checksum, data[:32].hex()

    def close(self):
        if self.ptr != self.ffi.NULL:
            self.lib.cma_free(self.ptr)
            self.ptr = self.ffi.NULL


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--addr", type=lambda s: int(s, 0))
    parser.add_argument("--length", type=int, default=FRAME_BYTES)
    parser.add_argument("--poll-ms", type=int, default=500)
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args()

    if args.length <= 0 or args.length >= (1 << 23):
        raise SystemExit("DMA length must be 1..8388607 bytes for this build")

    cma = None
    if args.addr is None:
        cma = CmaBuffer(args.length)
        cma.clear()
        args.addr = cma.phys_addr
        print(f"cma_addr=0x{args.addr:08x} cma_length={args.length}", flush=True)
    if args.addr & 0x3:
        raise SystemExit("DMA destination address must be 32-bit aligned")

    dvp = Mmio(DVP)
    dma = Mmio(DMA)
    mem_fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        # Keep camera out of power-down but prevent AXIS writes while DMA is reset.
        dvp.w32(0x00, 0x00000000)
        time.sleep(0.02)

        print_status("before", dvp, dma)
        s = decode_status(dvp.r32(0x04))
        if not s["xclk_locked"]:
            raise SystemExit("xclk is not locked")
        if s["sccb_cfg_error"]:
            raise SystemExit("SCCB configuration reports an error")

        # Reset S2MM and clear sticky status bits.
        dma.w32(S2MM_DMACR, DMACR_RESET)
        deadline = time.monotonic() + 1.0
        while dma.r32(S2MM_DMACR) & DMACR_RESET:
            if time.monotonic() > deadline:
                raise SystemExit("DMA reset did not complete")
            time.sleep(0.001)
        dma.w32(S2MM_DMASR, DMASR_IOC_IRQ | DMASR_ERR_IRQ | DMASR_ERR_MASK)

        # PG021 Direct Register mode: set RS, program destination, write LENGTH last.
        dma.w32(S2MM_DMACR, DMACR_RS)
        time.sleep(0.005)
        dma.w32(S2MM_DA, args.addr)
        dma.w32(S2MM_LENGTH, args.length)
        time.sleep(0.005)

        if args.no_stream:
            print("armed_dma_without_stream", flush=True)
            time.sleep(args.poll_ms / 1000.0)
        else:
            # Now the DMA can assert TREADY; allow the DVP FIFO to write AXI-Stream.
            dvp.w32(0x00, 0x00000008)

        deadline = time.monotonic() + (args.poll_ms / 1000.0)
        final_sr = dma.r32(S2MM_DMASR)
        if not args.no_stream:
            while time.monotonic() < deadline:
                final_sr = dma.r32(S2MM_DMASR)
                if final_sr & (DMASR_IOC_IRQ | DMASR_ERR_IRQ | DMASR_ERR_MASK):
                    break
                time.sleep(0.005)

        # Stop stream immediately after the short capture window.
        dvp.w32(0x00, 0x00000000)
        time.sleep(0.02)
        print_status("after", dvp, dma)

        sample_size = min(args.length, 4096)
        if cma is None:
            nonzero, checksum, head = checksum_sample(mem_fd, args.addr, sample_size)
        else:
            nonzero, checksum, head = cma.sample(sample_size)
        print(
            f"sample addr=0x{args.addr:08x} bytes={sample_size} "
            f"nonzero={nonzero} checksum=0x{checksum:08x} head={head}",
            flush=True,
        )

        if final_sr & DMASR_ERR_MASK:
            raise SystemExit(f"DMA error bits set: 0x{final_sr:08x}")
    finally:
        try:
            dvp.w32(0x00, 0x00000000)
        except Exception:
            pass
        os.close(mem_fd)
        dma.close()
        dvp.close()
        if cma is not None:
            cma.close()


if __name__ == "__main__":
    main()
