"""
CameraEngine: zero-copy video capture + JPEG encode engine.

Architecture (single-producer, multi-consumer):
  _capture_loop (1 thread):
    park_reg poll (1ms) → zero-copy frame → cv2.imencode(Q=40)
    → self.latest.update(jpeg_bytes, seq)

  get_frame() (called by N WebSocket handler threads):
    return self.latest.get(last_seq)  — lock-protected, no race

Key optimizations:
  1. Zero-copy: direct CMA buffer reference, no np.array() memcpy
  2. No color_correct: removed float32 per-pixel white balance (was ~40ms)
  3. No cv2.cvtColor: camera BGR byte order matches OpenCV imencode input
  4. Single producer thread: one encode serves all consumers
  5. park_reg polling at 1ms: detects new frame immediately
  6. libjpeg-turbo 4:2:0 Q=40: ~8ms encode on Cortex-A9 @650MHz

Usage:
    from camera_engine import CameraEngine

    cam = CameraEngine()
    cam.init()
    last_seq = 0
    while True:
        data, seq = cam.get_frame(last_seq, timeout=0.05)
        if data:
            send_to_all_clients(data, seq)
            last_seq = seq
"""

import threading
import time
import numpy as np
import cv2

from pynq import MMIO, Xlnk


# ── Thread-safe latest-item holder (multi-consumer pub/sub) ────────────────
class LatestItem:
    """Holds the most recent (data, seq) pair.

    Multiple consumer threads call get() with their own last_seq.
    Single producer thread calls update().
    All operations are lock-protected.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.data = None
        self.seq = 0

    def update(self, data, seq):
        with self._lock:
            self.data = data
            self.seq = seq

    def get(self, last_seq=0):
        """Return (data, seq) if new item available, else (None, last_seq)."""
        with self._lock:
            if self.seq > last_seq:
                return self.data, self.seq
            return None, last_seq


# ── Camera Engine ──────────────────────────────────────────────────────────
class CameraEngine:
    """Zero-copy video capture from VDMA + threaded JPEG encoding.

    VDMA is configured in circular mode (3 buffers, DMACR=0x10003).
    park_reg (offset 0x28) tracks which buffer VDMA is currently writing.
    The most recently completed frame is at index (park_reg - 1) % 3.

    A single background thread captures and encodes. Consumers (WebSocket
    handlers) call get_frame() to poll for the latest JPEG.
    """

    # VDMA S2MM register offsets (PG020)
    REG_DMACR  = 0x30   # Control
    REG_DMASR  = 0x34   # Status
    REG_PARK   = 0x28   # Park pointer (read-only)
    REG_VSIZE  = 0xA0   # Vertical size (lines)
    REG_HSIZE  = 0xA4   # Horizontal size (bytes)
    REG_STRIDE = 0xA8   # Stride (bytes)
    REG_FB1    = 0xAC   # Frame buffer start address 1
    REG_FB2    = 0xB0   # Frame buffer start address 2
    REG_FB3    = 0xB4   # Frame buffer start address 3
    REG_VER    = 0x2C   # Version

    def __init__(self, vdma_base=0x43000000,
                 width=640, height=480,
                 jpeg_quality=40):
        self.vdma_base = vdma_base
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.scale = 1.0           # 1.0=640x480, 0.5=320x240, 0.25=160x120

        # Thread-safe latest JPEG holder (multi-consumer)
        self.latest = LatestItem()

        # Internal state
        self.vdma = None
        self.frames = []            # 3 CMA buffers (numpy views)
        self._running = False
        self._thread = None

        # Stats (EMA-smoothed)
        self._stats_lock = threading.Lock()
        self.stats = {
            'frames': 0, 'drops': 0,
            'encode_ms': 0.0, 'fps': 0.0,
        }
        self._stat_frames = 0
        self._stat_t0 = time.time()

    # ── Initialization ─────────────────────────────────────────────────
    def init(self):
        """Initialize VDMA, allocate CMA buffers, start capture thread."""
        self.vdma = MMIO(self.vdma_base, 0x10000)

        # Verify VDMA presence
        ver = self.vdma.read(self.REG_VER)
        major = (ver >> 20) & 0xFFF
        minor = (ver >> 8) & 0xFFF
        patch = ver & 0xFF
        print("[cam] VDMA v%d.%d.%d" % (major, minor, patch))

        # Reset S2MM channel
        self.vdma.write(self.REG_DMACR, 0x00000004)  # Reset
        time.sleep(0.1)
        for _ in range(100):
            if (self.vdma.read(self.REG_DMACR) & 0x04) == 0:
                break
            time.sleep(0.01)

        # Allocate 3 frame buffers via CMA (DDR physically contiguous)
        xlnk = Xlnk()
        self.frames = []
        for i in range(3):
            buf = xlnk.cma_array(shape=(self.height, self.width, 3),
                                 dtype=np.uint8)
            self.frames.append(buf)
        # Fill with distinct greys → visible if VDMA not writing yet
        self.frames[0][:] = 50
        self.frames[1][:] = 100
        self.frames[2][:] = 150
        print("[cam] CMA: %d x %dx%d (%.1f MB)" % (
            3, self.width, self.height,
            3 * self.width * self.height * 3 / (1024 * 1024)))

        # Configure VDMA for circular mode (3 buffers)
        hsize = self.width * 3   # 640 × 3 = 1920 bytes/line
        self.vdma.write(self.REG_FB1,    self.frames[0].physical_address)
        self.vdma.write(self.REG_FB2,    self.frames[1].physical_address)
        self.vdma.write(self.REG_FB3,    self.frames[2].physical_address)
        self.vdma.write(self.REG_STRIDE, hsize)
        self.vdma.write(self.REG_HSIZE,  hsize)
        self.vdma.write(self.REG_DMACR,  0x00010003)    # Run + Circular
        self.vdma.write(self.REG_VSIZE,  self.height)    # 480 lines
        time.sleep(0.5)

        sr = self.vdma.read(self.REG_DMASR)
        halted = sr & 1
        idle   = (sr >> 1) & 1
        running = not halted and not idle
        print("[cam] DMASR=0x%08X  halt=%d  idle=%d  running=%s" % (
            sr, halted, idle, "YES" if running else "WAITING"))

        if not running:
            print("[cam] WARNING: VDMA not receiving video stream.")
            print("[cam]          Check: sensor connected? SCCB config OK?")

        # Start capture+encode thread
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop,
                                        name="cam-engine", daemon=True)
        self._thread.start()
        print("[cam] engine started (Q=%d, scale=%.2f)" % (
            self.jpeg_quality, self.scale))

    # ── Capture + Encode Loop (single producer thread) ────────────────
    def _capture_loop(self):
        """Background thread: poll park_reg → encode JPEG → publish.

        This is the ONLY place that reads VDMA registers and encodes.
        Consumers call get_frame() to read the published result.
        """
        last_park = None
        frame_seq = 0

        while self._running:
            # ── Poll park_reg at 1ms intervals ──
            park = self.vdma.read(self.REG_PARK) & 0x3
            if park == last_park:
                time.sleep(0.001)
                continue
            last_park = park

            safe_idx = (park - 1) % 3   # most recently completed frame

            # Paranoid re-check: skip if VDMA is now writing this buffer
            if (self.vdma.read(self.REG_PARK) & 0x3) == safe_idx:
                with self._stats_lock:
                    self.stats['drops'] += 1
                continue

            t_start = time.time()

            # ── Zero-copy: numpy view of CMA buffer (no memcpy) ──
            frame = self.frames[safe_idx]

            # ── Optional resize (dynamic resolution) ──
            if self.scale < 0.99:
                h = max(2, int(self.height * self.scale))
                w = max(2, int(self.width * self.scale))
                frame = cv2.resize(frame, (w, h),
                                   interpolation=cv2.INTER_NEAREST)

            # ── JPEG encode ──
            # Camera output is BGR byte order (ov5640_dvp_capture.v:155).
            # OpenCV imencode expects BGR input → zero conversion needed.
            ok, jpg = cv2.imencode('.jpg', frame, [
                int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality,
                int(cv2.IMWRITE_JPEG_OPTIMIZE), 1,
            ])

            if not ok:
                with self._stats_lock:
                    self.stats['drops'] += 1
                continue

            # ── Publish to all consumers ──
            frame_seq += 1
            self.latest.update(jpg.tobytes(), frame_seq)

            elapsed_ms = (time.time() - t_start) * 1000

            # ── Update EMA-smoothed stats ──
            with self._stats_lock:
                s = self.stats
                s['frames'] += 1
                s['encode_ms'] = 0.9 * s['encode_ms'] + 0.1 * elapsed_ms
                self._stat_frames += 1
                now = time.time()
                dt = now - self._stat_t0
                if dt >= 2.0:
                    s['fps'] = round(self._stat_frames / dt, 1)
                    self._stat_frames = 0
                    self._stat_t0 = now

    # ── Public API ─────────────────────────────────────────────────────
    def get_frame(self, last_seq=0, timeout=0.05):
        """Block up to timeout for next JPEG frame.

        Multiple threads can call this concurrently — each tracks its
        own last_seq. The underlying LatestItem.get() is lock-protected.

        Args:
            last_seq: Last sequence number this consumer has seen.
            timeout: Maximum seconds to block waiting.

        Returns:
            (jpeg_bytes, seq) or (None, last_seq) if no new frame.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            data, seq = self.latest.get(last_seq)
            if data is not None:
                return data, seq
            time.sleep(0.002)     # 2ms poll — fine for consumer side
        return None, last_seq

    def set_quality(self, q):
        """Dynamic JPEG quality (10-95). Takes effect next frame."""
        self.jpeg_quality = max(10, min(95, int(q)))

    def set_scale(self, s):
        """Dynamic resolution scale. 1.0=640x480, 0.5=320x240, 0.25=160x120."""
        self.scale = max(0.25, min(1.0, float(s)))

    def get_stats(self):
        """Return snapshot of current stats."""
        with self._stats_lock:
            return dict(self.stats)

    def stop(self):
        """Stop capture thread and halt VDMA."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self.vdma is not None:
            self.vdma.write(self.REG_DMACR, 0x00000000)  # Halt VDMA
        print("[cam] stopped (frames=%d, drops=%d)" % (
            self.stats['frames'], self.stats['drops']))


# ── Standalone test ────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("CameraEngine test — Ctrl+C to stop")
    print("(Run this on PYNQ after loading ov5640_audio_mecanum.bit)")
    print()
    cam = CameraEngine()
    try:
        cam.init()
        last_seq = 0
        print("\nStreaming... (press Ctrl+C to stop)\n")
        while True:
            data, seq = cam.get_frame(last_seq, timeout=0.5)
            if data is not None:
                last_seq = seq
                s = cam.get_stats()
                print("\r  seq=%-6d  size=%-6d B  fps=%-5s  "
                      "enc=%.1fms  Q=%d  scale=%.2f  drops=%d  " % (
                          seq, len(data), s['fps'], s['encode_ms'],
                          cam.jpeg_quality, cam.scale, s['drops']),
                      end='', flush=True)
    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        cam.stop()
