"""AudioDMA Python 驱动 (PYNQ-Z1).

────────────────────────────────────────────────────────────────────────────
DMA 路径专用. 用 PYNQ 自带的 pynq.lib.dma.DMA 把 packed PDM 数据从 DDR
直接 DMA 到 stream_to_pdm 模块, 完全绕开 PIO.

API:
    audio = AudioDMA(overlay.axi_dma_0)

    # 直接喂 packed PDM 字数组 (低层 API):
    audio.play_packed(packed_array)

    # 喂任意采样率的 PCM16 (推荐, 自动按硬件实测速率重采样):
    audio.play_pcm16(pcm_bytes, sample_rate=16000)

    # 显式校准硬件实际帧率 (跑一次小 burst 测耗时):
    audio.calibrate(verbose=True)

────────────────────────────────────────────────────────────────────────────
为什么需要 calibrate:
    stream_to_pdm 理论上每 4 μs 消化 1 个 16-bit 字 (250 kHz 帧率), 但实测
    可能因 AXIS 流水线 / AXI 总线 bubble / 综合后时序差异变成 ~156 kHz.
    上一轮发现 7.56 s 数据要 12.14 s 才放完 (1.6× 慢放), 听起来"听不懂".

    解决: 第一次播放前跑 calibrate(), 测 (n × 字/秒) = 实际帧率;
    之后 play_pcm16() 按测到的帧率做"密度词"上采样, 输出时长就准确.
────────────────────────────────────────────────────────────────────────────
"""
import time

import numpy as np
try:
    # PYNQ 3.x
    from pynq import allocate
    PYNQ_VERSION = 3
except ImportError:
    # PYNQ 2.x
    from pynq import Xlnk
    PYNQ_VERSION = 2
    _xlnk = Xlnk()


class AudioDMA:
    """AudioDMA: DMA 路径播放器."""

    # 默认假设 250 kHz, 第一次 calibrate() 后会被覆盖
    DEFAULT_HW_FRAME_RATE = 250000

    def __init__(self, dma_ip):
        """
        Args:
            dma_ip: pynq.Overlay 上的 axi_dma_0 (会自动绑定 pynq.lib.dma.DMA)
        """
        self.dma = dma_ip
        self._buffer = None
        self.hw_frame_rate_hz = self.DEFAULT_HW_FRAME_RATE
        self._calibrated = False

    # ────────────────────────────────────────────────────────────────────
    # 缓冲管理
    # ────────────────────────────────────────────────────────────────────
    def _ensure_buffer(self, size):
        """分配恰好 size 字的物理连续缓冲 (CMA), 返回整块 (绝不切片).

        关键: PYNQ 2.x 的 Xlnk cma_array 一旦 [:size] 切片就变成普通 ContiguousArray,
        丢失 .physical_address → DMA transfer 报错。所以这里按精确大小分配整块返回,
        同 size 复用; size 变了就重分配 (旧块由 GC 回收, Xlnk 自动管理, 不手动释放)。
        """
        if self._buffer is not None and self._buffer.shape[0] == size:
            return self._buffer
        if PYNQ_VERSION == 3:
            self._buffer = allocate(shape=(size,), dtype=np.uint32)
        else:
            self._buffer = _xlnk.cma_array(shape=(size,), dtype=np.uint32)
        return self._buffer

    def close(self):
        if self._buffer is not None:
            if PYNQ_VERSION == 3:
                self._buffer.freebuffer()
            # PYNQ 2.x: 不手动释放，让 Xlnk 自动管理
            self._buffer = None

    # ────────────────────────────────────────────────────────────────────
    # 低层: 喂 packed PDM 字
    # ────────────────────────────────────────────────────────────────────
    def play_packed(self, packed_samples):
        """阻塞播放 16-bit packed PDM 字数组.

        Args:
            packed_samples: 可迭代或 np.ndarray, 每个元素 16-bit packed PDM 字
        """
        if isinstance(packed_samples, np.ndarray):
            arr16 = packed_samples.astype(np.uint16, copy=False)
        else:
            arr16 = np.asarray(list(packed_samples), dtype=np.uint16)
        n = len(arr16)
        if n == 0:
            return

        # AXI DMA 单次传输受 MM2S 长度寄存器位宽限制 (C_SG_LENGTH_WIDTH=23 → 8 MB).
        # 每字 4 字节, 8 MB = 2097152 字; 超过就会把 23 位长度寄存器写回绕, 只播开头
        # 一小段(实测 ~9.8s 的句子只放出 ~1s)。所以超长就分块顺序传输, 每块都带 tlast
        # 干净结束, 下一块再启动。块间有极短停顿(放大器 toggle), 用 6 MB 大块尽量少切。
        MAX_WORDS = 1_500_000           # ~6 MB/块 (<8 MB 限制), ~6 秒/块
        for i in range(0, n, MAX_WORDS):
            chunk = arr16[i:i + MAX_WORDS]
            m = len(chunk)
            buf = self._ensure_buffer(m)        # 整块, 恰好 m 字, 带 physical_address
            buf[:] = chunk.astype(np.uint32)
            # MM2S transfer: DMA 顺序读 buf → AXIS → stream_to_pdm
            # DMA 自动在最后一个字置 tlast, stream_to_pdm 见 tlast 后等当前帧播完即停
            self.dma.sendchannel.transfer(buf)
            self.dma.sendchannel.wait()

    # ────────────────────────────────────────────────────────────────────
    # 校准: 测硬件实际帧率
    # ────────────────────────────────────────────────────────────────────
    def calibrate(self, n_words=200000, verbose=False):
        """跑一段已知大小的 packed PDM, 用 wall time 反推硬件帧率.

        n_words 默认 20 万字 ≈ 0.8 秒 (按 250 kHz). 太短测量误差大,
        太长又浪费时间. 中等密度词 (8) 让喇叭只发轻微嗡声, 不刺耳.

        Returns:
            float: 测得的硬件帧率 (Hz)
        """
        # 用 4 比特模式 0x0F0F (中等密度) — 占空 50%, 板上 RC 平均成中间电压
        cal = np.full(n_words, 0x0F0F, dtype=np.uint16)
        # 缓存原 hw_frame_rate, 测量期间用默认值算 deadline 兜底无关紧要
        t0 = time.monotonic()
        self.play_packed(cal)
        wall = time.monotonic() - t0
        rate = n_words / wall
        self.hw_frame_rate_hz = rate
        self._calibrated = True
        if verbose:
            print('[calibrate] {} words in {:.3f}s → {:.1f} kHz hw frame rate'.format(
                n_words, wall, rate/1000))
        return rate

    # ────────────────────────────────────────────────────────────────────
    # 高层: 喂 PCM16 (推荐入口)
    # ────────────────────────────────────────────────────────────────────
    def play_pcm16(self, pcm_bytes, sample_rate, auto_calibrate=True):
        """阻塞播放 PCM16 (signed little-endian) 数据.

        自动按硬件实测帧率做"密度字"重采样, 保证播放时长跟原 WAV 一致.

        Args:
            pcm_bytes: bytes 或 np.ndarray (int16) 的 PCM 数据
            sample_rate: PCM 采样率 (Hz), e.g. 16000
            auto_calibrate: 第一次调用时自动跑 calibrate() (默认 True)
        """
        if auto_calibrate and not self._calibrated:
            self.calibrate(verbose=True)

        if isinstance(pcm_bytes, np.ndarray):
            pcm = pcm_bytes.astype(np.int16, copy=False)
        else:
            pcm = np.frombuffer(pcm_bytes, dtype='<i2')
        n_pcm = len(pcm)
        if n_pcm == 0:
            return

        # 目标 packed PDM 字数 = pcm 时长 × 硬件帧率
        # 时长 = n_pcm / sample_rate
        n_packed = int(round(n_pcm * self.hw_frame_rate_hz / sample_rate))
        if n_packed <= 0:
            return

        # 把每个 pcm 样本映射成 0..16 的"目标 1 个数", 再查表生成密度字
        # 每个 PCM 样本对应 (n_packed / n_pcm) 个 packed 字 — 用最近邻索引
        # idx[i] = floor(i * n_pcm / n_packed)
        idx = (np.arange(n_packed, dtype=np.int64) * n_pcm // n_packed)
        idx = np.clip(idx, 0, n_pcm - 1)

        # PCM ∈ [-32768, 32767] → ones_count ∈ [0..16]
        ones = ((pcm.astype(np.int32) + 32768) * 16) // 65536
        ones = np.clip(ones, 0, 16).astype(np.int32)

        # 查表生成密度字
        table = np.array([_build_density_word(k) for k in range(17)],
                         dtype=np.uint16)
        packed = table[ones[idx]]

        self.play_packed(packed)


# ────────────────────────────────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────────────────────────────────
def _build_density_word(ones_count):
    """生成 16-bit 字, 1 的个数 = ones_count, 1 均匀分布 (低抖动)."""
    if ones_count <= 0:
        return 0x0000
    if ones_count >= 16:
        return 0xFFFF
    word = 0
    step = 16.0 / ones_count
    pos = 0.0
    for _ in range(ones_count):
        word |= 1 << int(pos)
        pos += step
    return word & 0xFFFF


def pcm16_to_packed_pdm(pcm_bytes, upsample_factor=1):
    """16-bit PCM → packed PDM, 整数倍上采样 (兼容旧 API).

    新代码请用 AudioDMA.play_pcm16() — 它会按实测硬件帧率精确重采样,
    不需要猜 upsample_factor.
    """
    if isinstance(pcm_bytes, np.ndarray):
        pcm = pcm_bytes.astype(np.int16, copy=False)
    else:
        pcm = np.frombuffer(pcm_bytes, dtype='<i2')
    ones = ((pcm.astype(np.int32) + 32768) * 16) // 65536
    ones = np.clip(ones, 0, 16).astype(np.int32)
    table = np.array([_build_density_word(k) for k in range(17)],
                     dtype=np.uint16)
    out = table[ones]
    if upsample_factor > 1:
        out = np.repeat(out, upsample_factor)
    return out



# ============================================================================
# Mic 录音 (S2MM DMA, simple-mode ping-pong)
# ============================================================================
class MicCapture:
    """通过 axi_dma_1 (S2MM) 抓 PDM mic 数据 (simple-mode 串行单缓冲).

    PDM 时钟 = pdm_capture_0 看到的 audio_direct_0/pdm_clk.
    每 16 个 PDM bit 包成 1 个 packed 字.

    ──────────────────────────────────────────────────────────────────────
    为什么是"串行单缓冲", 不是 ping-pong:
        pdm_capture 是 tlast 永久为 0 的无尽流. PYNQ 2.x 的 axi_dma_1 工作在
        simple (direct register) 模式, 一次只能挂一个 transfer, 且抓完一帧就
        会报 DMA Internal Error 并 halt (硬件实测确认). 之前的 4-buffer
        ping-pong 既无法在 simple-mode 下排多个 transfer, 读取下标又与硬件实
        际写入的 buffer 错位 → 读到从未被写过的旧 buffer → 解调出"冻结帧 /
        有规律脉冲".
        正确做法: 每次 transfer 前 reset S2MM 从 halt 恢复 (见
        load_overlay_pynq2.SimpleDMA.transfer), 然后 transfer→wait→读同一个
        buffer. 简单、与硬件语义一致.
    ──────────────────────────────────────────────────────────────────────

    API:
        mic = MicCapture(overlay.dma_rx)
        mic.start(buf_words=2048)        # 每帧 2048 字, 低延迟
        while True:
            chunk = mic.read_chunk()     # blocking, 一帧 (uint16 ndarray)
            ... process ...
        mic.stop()

    数据格式: chunk 每个 element 是 16-bit packed PDM 字, 用
              packed_pdm_to_pcm16_array() 解调成 PCM16.
    """

    def __init__(self, dma_ip):
        """
        Args:
            dma_ip: SimplifiedOverlay 上的 dma_rx (SimpleDMA, direction=2)
        """
        self.dma = dma_ip
        self._buf = None
        self._running = False

    def start(self, buf_words=2048, n_buffers=None):
        """分配缓冲并标记运行.

        Args:
            buf_words: 每帧抓多少 16-bit packed PDM 字 (DMA 实际 32-bit, 每字
                       只用低 16 位). 2048 字延迟约 3 ms, 平衡延迟与开销.
            n_buffers: 已废弃 (simple-mode 串行不需要多 buffer), 仅为兼容旧调用.
        """
        if self._running:
            raise RuntimeError("already running")
        if PYNQ_VERSION == 3:
            self._buf = allocate(shape=(buf_words,), dtype=np.uint32)
        else:
            self._buf = _xlnk.cma_array(shape=(buf_words,), dtype=np.uint32)
        self._buf_words = buf_words
        self._running = True

    def read_chunk(self):
        """Blocking 抓一帧 (uint16 ndarray, len=buf_words) 的拷贝.

        SimpleDMA.transfer 内置了 reset-per-transfer, 所以每次都拿到新鲜数据.
        """
        if not self._running:
            raise RuntimeError("not running")
        self.dma.recvchannel.transfer(self._buf)   # reset → run → arm
        self.dma.recvchannel.wait()                # 等 IOComplete
        # 复制低 16 位 (高 16 位由 BD 里的 subset converter 填 0)
        return self._buf[:].astype(np.uint16, copy=True)

    def stop(self):
        if not self._running:
            return
        self._running = False
        if PYNQ_VERSION == 3 and self._buf is not None:
            try:
                self._buf.freebuffer()
            except Exception:
                pass
        self._buf = None


# ============================================================================
# PDM → PCM 解调 (向量化, 配 mic 数据流用)
# ============================================================================
def packed_pdm_to_pcm16_array(packed):
    """把 16-bit packed PDM 字数组解调成 16-bit PCM (signed LE).

    简单算法: 数每个字里 1 的个数 (0..16) → 中心化到 [-8..+8] → 放大到 PCM.
    严格说不是真正的 PDM→PCM 解调 (没做 sinc 滤波), 但够用于人声中频段.

    Args:
        packed: np.ndarray uint16 / list

    Returns:
        np.ndarray int16 (与 packed 等长)
    """
    if not isinstance(packed, np.ndarray):
        packed = np.asarray(packed, dtype=np.uint16)
    else:
        packed = packed.astype(np.uint16, copy=False)

    # 数 1 的个数 (numpy 没有 popcount, 用查表)
    if not hasattr(packed_pdm_to_pcm16_array, '_popcnt'):
        tbl = np.zeros(65536, dtype=np.int16)
        for v in range(65536):
            tbl[v] = bin(v).count('1')
        packed_pdm_to_pcm16_array._popcnt = tbl
    ones = packed_pdm_to_pcm16_array._popcnt[packed]    # 0..16

    # 中心化 + 放大: (ones-8) * 3500 → [-28000, +28000]
    pcm = (ones - 8).astype(np.int32) * 3500
    return np.clip(pcm, -32768, 32767).astype(np.int16)


def downsample_pcm16(pcm, factor):
    """简单平均降采样 (抗混叠效果不强, 但够人声).

    Args:
        pcm: np.ndarray int16
        factor: int, 降采样因子. 输出长度 = len(pcm) // factor.
    """
    if factor <= 1:
        return pcm
    n = (len(pcm) // factor) * factor
    pcm = pcm[:n].astype(np.int32).reshape(-1, factor).mean(axis=1)
    return np.clip(pcm, -32768, 32767).astype(np.int16)
