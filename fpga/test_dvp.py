#!/usr/bin/env python3
"""
OV5640 DVP Capture Test Script
Run on PYNQ-Z1:  python3 test_dvp.py
Or from Mac via SSH:  ssh xilinx@192.168.2.99 "python3 /home/xilinx/test_dvp.py"
"""
from pynq import Overlay, MMIO
import time

BITFILE = "/home/xilinx/overlay/design_1_wrapper.bit"
BASE_ADDR = 0x43C00000

print("Loading overlay...")
ol = Overlay(BITFILE)
print(f"Overlay loaded. IPs: {list(ol.ip_dict.keys())}")

mmio = MMIO(BASE_ADDR, 0x10000)
print(f"MMIO opened at 0x{BASE_ADDR:08X}")

def read_reg(offset, name):
    val = mmio.read(offset)
    print(f"  {name:20s}  0x{val:08X}  ({val})")
    return val

print("\n=== DVP Capture Registers ===")
print(f"{'Register':<20s}  {'Hex':>10s}  {'Decimal':>10s}")
print("-" * 50)

# offset 0x00: slv_reg0 (control)
ctrl = read_reg(0x00, "control")

# offset 0x04: status_word
status = read_reg(0x04, "status_word")
print(f"    bit0 xclk_locked = {(status>>0)&1}")
print(f"    bit1 frame_seen   = {(status>>1)&1}")
print(f"    bit2 line_seen    = {(status>>2)&1}")
print(f"    bit3 pixel_seen   = {(status>>3)&1}")
print(f"    bit4 vsync_level  = {(status>>4)&1}")
print(f"    bit5 href_level   = {(status>>5)&1}")

frame_count = read_reg(0x08, "frame_count")
line_count  = read_reg(0x0C, "line_count")
pixel_count = read_reg(0x10, "pixel_count")
last_pixel  = read_reg(0x14, "last_pixel_word")
last_xy     = read_reg(0x18, "last_xy_word")
x = (last_xy >> 0) & 0xFFF
y = (last_xy >> 16) & 0xFFF
print(f"  last (x,y)          x={x}, y={y}")

# Continuous monitoring
print("\n=== Monitoring (Ctrl+C to stop) ===")
try:
    while True:
        fc = mmio.read(0x08)
        pc = mmio.read(0x10)
        st = mmio.read(0x04)
        print(f"\r  frames={fc:8d}  pixels={pc:10d}  status=0x{st:08X}", end="")
        time.sleep(0.5)
except KeyboardInterrupt:
    print("\n\nDone.")
    final_fc = mmio.read(0x08)
    final_pc = mmio.read(0x10)
    final_st = mmio.read(0x04)

    if final_fc > 0:
        print(f"SUCCESS: Camera is streaming! {final_fc} frames received, {final_pc} pixels.")
    elif (final_st & 0x2) or (final_st & 0x4):
        print("PARTIAL: Lines/pixels seen but no complete frame. Check VSYNC wiring.")
    else:
        print("NO DATA: No pixels detected. Check camera power, SCCB init, and wiring.")
