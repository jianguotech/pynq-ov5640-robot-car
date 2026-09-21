#!/usr/bin/env python3
import argparse
import mmap
import os
import struct
import time


DVP = 0x40000000
DMA = 0x40400000
FIRMWARE = "/sys/class/fpga_manager/fpga0/firmware"
FLAGS = "/sys/class/fpga_manager/fpga0/flags"


def log(msg):
    print(msg, flush=True)


def write_text(path, text):
    log(f"write_enter {path} {text!r}")
    with open(path, "w") as f:
        f.write(text)
    log(f"write_exit {path}")


def read_text(path):
    with open(path) as f:
        return f.read().strip()


def load_bitstream(name):
    write_text(FLAGS, "0")
    write_text(FIRMWARE, name)
    for i in range(30):
        state = read_text("/sys/class/fpga_manager/fpga0/state")
        log(f"state[{i}]={state}")
        if state == "operating":
            return
        time.sleep(0.2)
    raise RuntimeError("fpga_manager did not reach operating")


def probe_mmap(base, writable, osync, offsets):
    flags = os.O_RDWR if writable else os.O_RDONLY
    if osync:
        flags |= os.O_SYNC
    prot = mmap.PROT_READ | (mmap.PROT_WRITE if writable else 0)

    log(f"open_enter flags=0x{flags:x}")
    fd = os.open("/dev/mem", flags)
    log(f"open_exit fd={fd}")
    try:
        log(f"mmap_enter base=0x{base:08x} prot=0x{prot:x}")
        mem = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, prot, offset=base)
        log("mmap_exit")
        try:
            for off in offsets:
                log(f"read_enter off=0x{off:02x}")
                val = struct.unpack_from("<I", mem, off)[0]
                log(f"read_exit off=0x{off:02x} val=0x{val:08x}")
        finally:
            log("munmap_enter")
            mem.close()
            log("munmap_exit")
    finally:
        os.close(fd)
        log("close_exit")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--firmware", default="design_1_wrapper.bin")
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--writable", action="store_true")
    parser.add_argument("--osync", action="store_true")
    parser.add_argument("--target", choices=["dvp", "dma"], default="dvp")
    args = parser.parse_args()

    log("load_probe_start")
    load_bitstream(args.firmware)
    log(f"sleep_enter seconds={args.delay}")
    time.sleep(args.delay)
    log("sleep_exit")
    if args.target == "dma":
        offsets = (0x30, 0x34, 0x48, 0x58)
        base = DMA
    else:
        offsets = (0x00, 0x04, 0x08)
        base = DVP
    probe_mmap(base, args.writable, args.osync, offsets)
    log("load_probe_done")


if __name__ == "__main__":
    main()
