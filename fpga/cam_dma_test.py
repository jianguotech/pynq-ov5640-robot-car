#!/usr/bin/env python3
"""
OV5640 DVP + AXI DMA 正确测试脚本
关键点：必须先用 pynq.allocate() 分配缓冲区，再启动 DMA，最后才能开 stream
否则 DMA 会写到物理地址 0x0，覆盖 Linux 内核，导致板子死机需断电重启
"""
from pynq import Overlay, allocate
import numpy as np

BITFILE = '/home/xilinx/design_1_wrapper_v3.bit'
IMG_W, IMG_H = 640, 480
FRAME_PIXELS = IMG_W * IMG_H  # 307200

print("Loading overlay...")
ol = Overlay(BITFILE)
print(f"IPs: {list(ol.ip_dict.keys())}")

# 验证 HP0 数据宽度（应为 32）
ps = ol.ip_dict.get('processing_system7_0')
if ps:
    w = ps['parameters'].get('C_S_AXI_HP0_DATA_WIDTH', '?')
    print(f"HP0 data width: {w} (should be 32)")

dma = ol.axi_dma_0
dvp = ol.myOV5640_DVP_0

# 读取初始状态
status = dvp.read(0x04)
fc = dvp.read(0x08)
print(f"DVP status=0x{status:08X}  frame_count={fc}")
print(f"  xclk_locked={status&1}  sccb_done={(status>>7)&1}  sccb_err={(status>>8)&1}")

# 步骤1：分配物理连续缓冲区（pynq.allocate 保证在安全的 CMA 区域，不会覆盖内核）
buf = allocate(shape=(FRAME_PIXELS,), dtype=np.uint16)
print(f"Buffer: {FRAME_PIXELS} pixels @ phys=0x{buf.physical_address:08X}")

# 步骤2：启动 DMA S2MM 传输（这一步会把 buf 的物理地址写入 S2MM_DA）
#         必须在 stream_enable 之前做，否则 DMA 以 DA=0 接收数据会覆盖内核
dma.recvchannel.transfer(buf)
print("DMA transfer started (S2MM_DA set to buffer address)")

# 步骤3：使能 DVP stream（bit3=1）
dvp.write(0x00, 0x08)
print("DVP stream enabled")

# 步骤4：等待一帧传输完成（TLAST 触发 DMA 完成）
print("Waiting for frame (timeout 3s)...")
try:
    dma.recvchannel.wait(timeout=3)
    print("Frame received!")
except Exception as e:
    print(f"Timeout/error: {e}")
    sr = dma.register_map.S2MM_DMASR
    print(f"S2MM_DMASR=0x{int(sr):08X}  Halted={int(sr)&1}  Idle={(int(sr)>>1)&1}  DMAIntErr={(int(sr)>>4)&1}")

# 关闭 stream
dvp.write(0x00, 0x00)

# 检查数据
nonzero = np.count_nonzero(buf)
print(f"Non-zero pixels: {nonzero}/{FRAME_PIXELS} ({100*nonzero/FRAME_PIXELS:.1f}%)")
print(f"First 8 pixels: {[f'0x{v:04X}' for v in buf[:8]]}")

# 检查奇偶索引（HP0 宽度不匹配时奇数索引全为 0）
odd_zero = int(np.sum(buf[1::2] == 0))
even_zero = int(np.sum(buf[0::2] == 0))
print(f"Odd-index zeros: {odd_zero}  Even-index zeros: {even_zero}")
if odd_zero > FRAME_PIXELS // 4:
    print("WARNING: HP0 data width mismatch! Rebuild bitstream with PCW_S_AXI_HP0_DATA_WIDTH=32")

buf.freebuffer()
print("Done.")
