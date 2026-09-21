"""PYNQ 2.x 兼容的 overlay 加载器 (绕过 Overlay 类的 TCL 依赖).

用法:
    from load_overlay_pynq2 import load_audio_dma_overlay
    ol = load_audio_dma_overlay('audio_dma.bit')
    
    # 访问 DMA (已自动创建)
    from audio_dma_driver import AudioDMA, MicCapture
    audio = AudioDMA(ol.dma_tx)
    mic = MicCapture(ol.dma_rx)
"""
from pynq import Bitstream, MMIO, Xlnk
import time

# 硬编码地址 (与 create_audio_dma.tcl 中的 assign_bd_address 一致)
DMA_TX_ADDR = 0x40400000  # axi_dma_0 (MM2S)
DMA_RX_ADDR = 0x40410000  # axi_dma_1 (S2MM)
AUDIO_DIRECT_ADDR = 0x43C00000  # audio_direct_0 (旧 IP, 备用)

DMA_RANGE = 0x10000  # 64 KB

# AXI DMA 寄存器偏移 (参考 PG021)
MM2S_DMACR = 0x00    # Control
MM2S_DMASR = 0x04    # Status
MM2S_SA = 0x18       # Source Address
MM2S_LENGTH = 0x28   # Transfer Length

S2MM_DMACR = 0x30    # Control
S2MM_DMASR = 0x34    # Status
S2MM_DA = 0x48       # Destination Address
S2MM_LENGTH = 0x58   # Transfer Length


class SimpleDMA:
    """简化的 DMA 封装 (兼容 audio_dma_driver 的接口)."""
    
    def __init__(self, mmio, direction):
        """
        Args:
            mmio: MMIO 对象
            direction: 1=MM2S (send), 2=S2MM (recv)
        """
        self.mmio = mmio
        self.direction = direction
        self._xlnk = Xlnk()
        
        # 复位 DMA
        if direction == 1:  # MM2S
            self.mmio.write(MM2S_DMACR, 0x04)  # Reset
            time.sleep(0.01)
            self.mmio.write(MM2S_DMACR, 0x1001)  # Run + IOC_IrqEn (让 IOC 位可靠置位)
        else:  # S2MM
            self.mmio.write(S2MM_DMACR, 0x04)  # Reset
            time.sleep(0.01)
            self.mmio.write(S2MM_DMACR, 0x01)  # Run
    
    @property
    def sendchannel(self):
        """MM2S channel (兼容 pynq.lib.dma.DMA 接口)."""
        if self.direction != 1:
            raise RuntimeError("This DMA is not configured for MM2S")
        return self
    
    @property
    def recvchannel(self):
        """S2MM channel (兼容 pynq.lib.dma.DMA 接口)."""
        if self.direction != 2:
            raise RuntimeError("This DMA is not configured for S2MM")
        return self
    
    def transfer(self, buf):
        """启动 DMA transfer.

        Args:
            buf: CMA buffer (Xlnk.cma_array 返回的 numpy 数组)
        """
        if self.direction == 1:  # MM2S (播放)
            # 每次传输前 reset+run 重新 arm 本通道。原因: 分块播放长音频时, 前一块
            # 带 tlast 结束后 MM2S 进入"已完成"态, 直接再写 SA/LENGTH 不会可靠地启动
            # 下一块 → 第二块及以后不播(实测: 长句只放出第一块就停)。reset 一次确保
            # 每块都干净启动。单块短音频也只多一次 reset, 无影响。
            self.mmio.write(MM2S_DMACR, 0x04)          # reset 本通道
            t = time.time()
            while (self.mmio.read(MM2S_DMACR) & 0x04) and (time.time() - t) < 0.1:
                pass                                    # 等 reset 自清零
            self.mmio.write(MM2S_DMACR, 0x1001)        # run + IOC_IrqEn (让 IOC 位可靠置位)
            self.mmio.write(MM2S_DMASR, 0x1000)        # 清 IOC (W1C), 避免读到上次的陈旧完成位
            self.mmio.write(MM2S_SA, buf.physical_address)
            self.mmio.write(MM2S_LENGTH, buf.nbytes)   # 写长度 → 触发
        else:  # S2MM (录音)
            # ────────────────────────────────────────────────────────────
            # 关键: pdm_capture 是 tlast 永久为 0 的"无尽流". simple-mode
            # S2MM 抓完第一帧 (LENGTH 字节) 后会触发 DMA Internal Error
            # (DMASR bit4) 并 halt, 之后所有 transfer 被忽略 → buffer 永远
            # 不再刷新 → 解调出"冻结/有规律脉冲". (硬件实测确认: 第 1 帧
            # 写满 2048 字后状态变 Halt|IntErr, 第 2 帧起 written=0.)
            #
            # 软件解法: 每次传输前 reset 一次 S2MM, 从 halt 恢复. 代价是两帧
            # 之间丢掉极少量流数据 (AXIS FIFO 里), 对人声监听无影响.
            # ────────────────────────────────────────────────────────────
            self.mmio.write(S2MM_DMACR, 0x04)          # reset 本通道
            t = time.time()
            while (self.mmio.read(S2MM_DMACR) & 0x04) and (time.time() - t) < 0.1:
                pass                                    # 等 reset 自清零
            self.mmio.write(S2MM_DMACR, 0x01)          # run
            self.mmio.write(S2MM_DMASR, 0x1000)        # 清 IOC (写 1 清零)
            self.mmio.write(S2MM_DA, buf.physical_address)
            self.mmio.write(S2MM_LENGTH, buf.nbytes)   # 写长度 → 触发

    def wait(self, timeout=1.0):
        """等待 DMA transfer 完成."""
        if self.direction == 1:  # MM2S: 只等本次传输的 IOComplete (不靠 Idle, 否则陈旧Idle会提前返回)
            t0 = time.time()
            while time.time() - t0 < 60:
                sr = self.mmio.read(MM2S_DMASR)
                if sr & 0x1000:        # IOComplete
                    return
                if sr & 0x70:          # 错误位
                    raise RuntimeError("MM2S DMA error, DMASR=0x{:08X}".format(sr))
                time.sleep(0.0005)
            raise RuntimeError("MM2S DMA timeout, DMASR=0x{:08X}".format(
                self.mmio.read(MM2S_DMASR)))
        else:  # S2MM
            # tlast 恒 0 的无尽流喂给 simple-mode S2MM, 偶尔会在填满 buffer 后
            # 于尾部报 DMA Internal Error (DMASR bit4). 此时 buffer 已被这次
            # (reset 后全新启动的) 传输填满, 且下一次 transfer 的 reset 会清掉
            # 该错误 —— 所以容错处理: IOComplete 是干净完成; 见到错误位也当本帧
            # 完成正常返回 (数据有效, 靠下轮 reset 恢复引擎); 只有真正超时才报错.
            t0 = time.time()
            while (time.time() - t0) < timeout:
                sr = self.mmio.read(S2MM_DMASR)
                if sr & 0x1000:        # IOComplete (干净)
                    return
                if sr & 0x70:          # IntErr/SlvErr/DecErr: 尾部错误, 数据已到, 容错返回
                    return
                time.sleep(0.0002)
            raise RuntimeError("S2MM DMA timeout, DMASR=0x{:08X}".format(
                self.mmio.read(S2MM_DMASR)))


class SimplifiedOverlay:
    """简化的 overlay 对象, 包含 DMA 实例.

    download=False: 不重新下载 bitstream, 只挂到"已加载的 overlay"上(附着模式).
        用于与摄像头等其它子系统共用同一个 ov5640_audio_mecanum.bit 时——别人已
        加载好 overlay, 音频只需访问自己的 DMA 寄存器, 重下会复位整块 PL 冲掉摄像头.
    """

    def __init__(self, bitfile, download=True):
        self.bitstream = Bitstream(bitfile)
        if download:
            self.bitstream.download()
        
        # 创建 MMIO 对象
        self.mmio_dict = {
            'axi_dma_0': MMIO(DMA_TX_ADDR, DMA_RANGE),
            'axi_dma_1': MMIO(DMA_RX_ADDR, DMA_RANGE),
            'audio_direct_0': MMIO(AUDIO_DIRECT_ADDR, DMA_RANGE),
        }
        
        # 创建简化的 DMA 对象
        self.dma_tx = SimpleDMA(self.mmio_dict['axi_dma_0'], 1)  # MM2S
        self.dma_rx = SimpleDMA(self.mmio_dict['axi_dma_1'], 2)  # S2MM
        
        print("✓ Bitstream loaded: {}".format(bitfile))
        print("  axi_dma_0 (TX): 0x{:08X}".format(DMA_TX_ADDR))
        print("  axi_dma_1 (RX): 0x{:08X}".format(DMA_RX_ADDR))
        print("  audio_direct_0: 0x{:08X}".format(AUDIO_DIRECT_ADDR))


def load_audio_dma_overlay(bitfile='audio_dma.bit', download=True):
    """加载 audio_dma overlay (PYNQ 2.x 兼容).

    Args:
        bitfile: .bit 文件路径 (相对或绝对)
        download: True=下载bitstream(独占); False=附着到已加载的overlay(与摄像头共用时)

    Returns:
        SimplifiedOverlay 对象, 包含 dma_tx 和 dma_rx
    """
    return SimplifiedOverlay(bitfile, download=download)
