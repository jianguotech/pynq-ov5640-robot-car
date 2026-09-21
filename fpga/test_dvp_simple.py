#!/usr/bin/env python3
"""
Minimal DVP test - no PYNQ dependency.
Uses /dev/mem to read AXI registers at 0x43C00000.
Load bitstream first: sudo sh -c 'cat design_1_wrapper.bit > /sys/class/fpga_manager/fpga0/firmware'
"""
import mmap
import os
import struct
import sys
import time

BASE = 0x43C00000
SIZE = 0x10000

def main():
    # Open /dev/mem
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    mem = mmap.mmap(fd, SIZE, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BASE)
    os.close(fd)

    def read_reg(offset):
        mem.seek(offset)
        return struct.unpack("<I", mem.read(4))[0]

    print("=== OV5640 DVP Register Dump ===")
    print(f"Base: 0x{BASE:08X}\n")

    # Control register (slv_reg0)
    ctrl = read_reg(0x00)
    print(f"  control       @0x00: 0x{ctrl:08X}")

    # Status word
    status = read_reg(0x04)
    print(f"  status_word   @0x04: 0x{status:08X}")
    print(f"    bit0 xclk_locked = {(status>>0)&1}")
    print(f"    bit1 frame_seen   = {(status>>1)&1}")
    print(f"    bit2 line_seen    = {(status>>2)&1}")
    print(f"    bit3 pixel_seen   = {(status>>3)&1}")
    print(f"    bit4 vsync_level  = {(status>>4)&1}")
    print(f"    bit5 href_level   = {(status>>5)&1}")

    # Counters
    frame_count = read_reg(0x08)
    line_count  = read_reg(0x0C)
    pixel_count = read_reg(0x10)
    print(f"  frame_count   @0x08: {frame_count}")
    print(f"  line_count    @0x0C: {line_count}")
    print(f"  pixel_count   @0x10: {pixel_count}")

    # Last pixel
    last_pixel = read_reg(0x14)
    last_xy    = read_reg(0x18)
    x = (last_xy >> 0) & 0xFFF
    y = (last_xy >> 16) & 0xFFF
    print(f"  last_pixel    @0x14: 0x{last_pixel:04X}")
    print(f"  last_xy       @0x18: x={x}, y={y}")

    # Cleanup
    mem.close()

    # Quick check
    if frame_count > 0:
        print(f"\n*** SUCCESS: Camera is streaming! {frame_count} frames received. ***")
    elif (status & 0x0E) != 0:
        print(f"\n*** PARTIAL: Pixels/lines seen but no complete frame. Check VSYNC. ***")
    else:
        print(f"\n*** NO DATA: No pixels detected. Check camera power/wiring/SCCB init. ***")

if __name__ == "__main__":
    main()
