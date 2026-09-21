"""
OV5640 VDMA S2MM 驱动
=====================
从 DDR 中获取 OV5640 摄像头经 VDMA 写入的原始图像帧。

驱动接口：
  open()       — 初始化 VDMA 寄存器，分配 CMA 帧缓冲
  start()      — 启动环形 DMA 采集
  get_frame()  — 获取最新原始帧（numpy BGR 数组），无新帧返回 None
  stop()       — 停止 DMA
  close()      — 释放 CMA 缓冲
"""

import time
import numpy as np
from pynq import MMIO, Xlnk


class OV5640VDMADriver:

    # VDMA S2MM 寄存器偏移 (PG020)
    REG_DMACR  = 0x30
    REG_DMASR  = 0x34
    REG_PARK   = 0x28
    REG_VSIZE  = 0xA0
    REG_HSIZE  = 0xA4
    REG_STRIDE = 0xA8
    REG_FB1    = 0xAC
    REG_FB2    = 0xB0
    REG_FB3    = 0xB4
    REG_VER    = 0x2C

    def __init__(self, vdma_base=0x43000000, width=640, height=480):
        self._base = vdma_base
        self._width = width
        self._height = height
        self._vdma = None
        self._buffers = []
        self._seq = 0
        self._running = False

    def open(self):
        """初始化 VDMA 寄存器，分配 CMA 帧缓冲。"""
        self._vdma = MMIO(self._base, 0x10000)
        self._reset_channel()
        self._alloc_buffers()
        self._config_registers()

    def start(self):
        """启动环形 DMA 采集。"""
        self._running = True

    def get_frame(self, last_seq=0, timeout=0.05):
        """获取最新原始帧（零拷贝 numpy view）。

        Returns:
            (frame, seq): BGR (height,width,3) uint8 数组，
            无新帧返回 (None, last_seq)。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._seq > last_seq:
                park = self._vdma.read(self.REG_PARK) & 0x3
                idx = (park - 1) % 3
                if (self._vdma.read(self.REG_PARK) & 0x3) != idx:
                    return self._buffers[idx], self._seq
            time.sleep(0.001)
        return None, last_seq

    def stop(self):
        """停止 DMA。"""
        self._running = False
        if self._vdma:
            self._vdma.write(self.REG_DMACR, 0x00000000)

    def close(self):
        """释放 CMA 缓冲。"""
        self.stop()
        self._buffers = []
        self._vdma = None

    # ── 内部 ────────────────────────────────────────────────────────────

    def _reset_channel(self):
        self._vdma.write(self.REG_DMACR, 0x00000004)
        time.sleep(0.1)
        for _ in range(100):
            if (self._vdma.read(self.REG_DMACR) & 0x04) == 0:
                break
            time.sleep(0.01)

    def _alloc_buffers(self):
        xlnk = Xlnk()
        for _ in range(3):
            buf = xlnk.cma_array(
                shape=(self._height, self._width, 3), dtype=np.uint8)
            self._buffers.append(buf)
        self._buffers[0][:] = 50
        self._buffers[1][:] = 100
        self._buffers[2][:] = 150

    def _config_registers(self):
        """VDMA S2MM 环形模式配置。"""
        hsize = self._width * 3
        self._vdma.write(self.REG_FB1,    self._buffers[0].physical_address)
        self._vdma.write(self.REG_FB2,    self._buffers[1].physical_address)
        self._vdma.write(self.REG_FB3,    self._buffers[2].physical_address)
        self._vdma.write(self.REG_STRIDE, hsize)
        self._vdma.write(self.REG_HSIZE,  hsize)
        self._vdma.write(self.REG_DMACR,  0x00010003)   # Run + Circular
        self._vdma.write(self.REG_VSIZE,  self._height)
        time.sleep(0.5)
