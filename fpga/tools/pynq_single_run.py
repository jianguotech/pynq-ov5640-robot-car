#!/usr/bin/env python3
import argparse
import mmap
import os
import struct
import time


DVP = 0x40000000
DMA = 0x40400000
FRAME_BYTES = 640 * 480 * 2

S2MM_DMACR = 0x30
S2MM_DMASR = 0x34
S2MM_DA = 0x48
S2MM_LENGTH = 0x58

DMACR_RS = 0x00000001
DMACR_RESET = 0x00000004
DMASR_IOC_IRQ = 0x00001000
DMASR_ERR_IRQ = 0x00004000
DMASR_ERR_MASK = 0x00000770


def log(msg):
    print(msg, flush=True)


def write_text(path, text):
    log(f"write {path} {text!r}")
    with open(path, "w") as f:
        f.write(text)


def load_bitstream(firmware):
    write_text("/sys/class/fpga_manager/fpga0/flags", "0")
    write_text("/sys/class/fpga_manager/fpga0/firmware", firmware)
    for i in range(50):
        with open("/sys/class/fpga_manager/fpga0/state") as f:
            state = f.read().strip()
        log(f"fpga_state[{i}]={state}")
        if state == "operating":
            return
        time.sleep(0.1)
    raise RuntimeError("fpga_manager did not reach operating")


class Page:
    def __init__(self, fd, base, label):
        self.base = base
        self.label = label
        log(f"mmap_enter {label} base=0x{base:08x}")
        self.mem = mmap.mmap(
            fd,
            0x1000,
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
            offset=base,
        )
        log(f"mmap_exit {label}")

    def r32(self, off):
        val = struct.unpack_from("<I", self.mem, off)[0]
        log(f"r32 {self.label}+0x{off:02x}=0x{val:08x}")
        return val

    def w32(self, off, val):
        log(f"w32 {self.label}+0x{off:02x}=0x{val:08x}")
        struct.pack_into("<I", self.mem, off, val & 0xFFFFFFFF)

    def close(self):
        log(f"munmap {self.label}")
        self.mem.close()


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
        log(f"cma_pages_before={self.lib.cma_pages_available()}")
        self.ptr = self.lib.cma_alloc(length, 0)
        if self.ptr == self.ffi.NULL:
            raise RuntimeError("cma_alloc failed")
        self.length = length
        self.phys_addr = int(self.lib.cma_get_phy_addr(self.ptr))
        self.view = self.ffi.buffer(self.ptr, length)
        self.view[:] = b"\x00" * length
        log(f"cma phys=0x{self.phys_addr:08x} length={length}")

    def sample(self, size=4096):
        data = bytes(self.view[: min(size, self.length)])
        nonzero = sum(1 for b in data if b)
        checksum = sum(data) & 0xFFFFFFFF
        log(
            f"sample bytes={len(data)} nonzero={nonzero} "
            f"checksum=0x{checksum:08x} head={data[:32].hex()}"
        )

    def close(self):
        if self.ptr != self.ffi.NULL:
            log("cma_free")
            self.lib.cma_free(self.ptr)
            self.ptr = self.ffi.NULL


def status_bits(status):
    return {
        "xclk": status & 1,
        "frame": (status >> 1) & 1,
        "line": (status >> 2) & 1,
        "pixel": (status >> 3) & 1,
        "pwdn": (status >> 6) & 1,
        "sccb_done": (status >> 7) & 1,
        "sccb_error": (status >> 8) & 1,
    }


def wait_camera_ready(dvp, seconds):
    deadline = time.monotonic() + seconds
    last = 0
    while time.monotonic() < deadline:
        last = dvp.r32(0x04)
        bits = status_bits(last)
        log("status_bits " + " ".join(f"{k}={v}" for k, v in bits.items()))
        if bits["xclk"] and bits["sccb_done"] and not bits["sccb_error"]:
            return last
        time.sleep(0.2)
    return last


def reset_dma(dma):
    dma.w32(S2MM_DMACR, DMACR_RESET)
    deadline = time.monotonic() + 1.0
    while dma.r32(S2MM_DMACR) & DMACR_RESET:
        if time.monotonic() > deadline:
            raise RuntimeError("DMA reset timed out")
        time.sleep(0.002)
    dma.w32(S2MM_DMASR, DMASR_IOC_IRQ | DMASR_ERR_IRQ | DMASR_ERR_MASK)


def arm_dma(dma, addr, length):
    reset_dma(dma)
    dma.w32(S2MM_DMACR, DMACR_RS)
    dma.w32(S2MM_DA, addr)
    dma.w32(S2MM_LENGTH, length)
    dma.r32(S2MM_DMACR)
    dma.r32(S2MM_DMASR)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--firmware", default="design_1_wrapper.bin")
    parser.add_argument("--length", type=int, default=FRAME_BYTES)
    parser.add_argument("--ready-wait", type=float, default=3.0)
    parser.add_argument("--poll-ms", type=int, default=800)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--no-cma", action="store_true")
    parser.add_argument("--skip-dma", action="store_true")
    parser.add_argument("--dma-first", action="store_true")
    args = parser.parse_args()

    log("single_run_start")
    cma = None
    fd = None
    dvp = None
    dma = None
    try:
        load_bitstream(args.firmware)
        time.sleep(0.2)
        if args.no_cma:
            log("cma_skipped")
        else:
            cma = CmaBuffer(args.length)

        log("devmem_open")
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        if args.dma_first and not args.skip_dma:
            log("before_dma_page")
            dma = Page(fd, DMA, "dma")
            log("after_dma_page")
        log("before_dvp_page")
        dvp = Page(fd, DVP, "dvp")
        log("after_dvp_page")
        if dma is None and not args.skip_dma:
            log("before_dma_page")
            dma = Page(fd, DMA, "dma")
            log("after_dma_page")

        dvp.w32(0x00, 0x00000000)
        wait_camera_ready(dvp, args.ready_wait)
        dvp.r32(0x00)
        dvp.r32(0x08)
        dvp.r32(0x10)

        if args.skip_dma:
            log("skip_dma_done")
            return

        if cma is None:
            raise RuntimeError("DMA test needs CMA unless --skip-dma is used")

        arm_dma(dma, cma.phys_addr, args.length)
        if args.no_stream:
            log("no_stream_wait")
            time.sleep(args.poll_ms / 1000.0)
            dma.r32(S2MM_DMASR)
            reset_dma(dma)
            cma.sample()
            return

        log("stream_enable")
        dvp.w32(0x00, 0x00000008)
        deadline = time.monotonic() + (args.poll_ms / 1000.0)
        final_sr = 0
        while time.monotonic() < deadline:
            final_sr = dma.r32(S2MM_DMASR)
            if final_sr & (DMASR_IOC_IRQ | DMASR_ERR_IRQ | DMASR_ERR_MASK):
                break
            time.sleep(0.01)
        log("stream_disable")
        dvp.w32(0x00, 0x00000000)
        dma.r32(S2MM_DMASR)
        dvp.r32(0x08)
        dvp.r32(0x10)
        cma.sample()
        if final_sr & DMASR_ERR_MASK:
            raise RuntimeError(f"DMA error status 0x{final_sr:08x}")
    finally:
        if dvp is not None:
            try:
                dvp.w32(0x00, 0x00000000)
            except Exception:
                pass
        if dma is not None:
            try:
                dma.w32(S2MM_DMACR, DMACR_RESET)
            except Exception:
                pass
            dma.close()
        if dvp is not None:
            dvp.close()
        if fd is not None:
            os.close(fd)
        if cma is not None:
            cma.close()
        log("single_run_done")


if __name__ == "__main__":
    main()
