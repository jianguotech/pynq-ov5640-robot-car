#!/opt/python3.6/bin/python3.6
# -*- coding: utf-8 -*-
"""板端统一服务: 一个进程同时支持 mic 录音流 + TTS 播报, 半双工(播报时暂停录音).

只 load overlay 一次, 同时持 AudioDMA(dma_tx 播放) 与 MicCapture(dma_rx 录音);
两套 DMA 独立, 由一个 TCP 服务对外:

  连接后第一行声明角色:
    "MIC\n"  → 订阅录音流: 服务先回 "RATE <n>\n", 之后持续推裸 PCM16(小端,单声道).
              播报进行中会暂停推送(不把喇叭声录进去).
    "PLAY\n" → 播报通道: 客户端发 [4字节长度][PCM16@16000] 帧; 服务置 playing 暂停 mic,
              用喇叭放完, 清 playing, 回 "DONE\n".

启动 (SSH, root):
    sudo /opt/python3.6/bin/python3.6 -u board_server.py [--port 8800]
"""
import argparse
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from load_overlay_pynq2 import load_audio_dma_overlay
from audio_dma_driver import AudioDMA, MicCapture

_POP = np.array([bin(v).count('1') for v in range(65536)], dtype=np.float32)
TARGET_RATE = 16000.0
MIC_BUF = 4096
GAIN = 4000.0
PLAY_RATE = 16000


def _box(x, k):
    if k <= 1:
        return x
    n = (len(x) // k) * k
    return x[:n].reshape(-1, k).mean(axis=1) if n else x[:0]


class BoardServer:
    def __init__(self, bitfile='audio_dma.bit', download=True):
        sys.stderr.write("[srv] %s overlay: %s ...\n" % (
            "loading" if download else "attaching(不重下)", bitfile)); sys.stderr.flush()
        _saved = sys.stdout; sys.stdout = sys.stderr      # 拦 overlay 的 print
        try:
            self.ol = load_audio_dma_overlay(bitfile, download=download)
        finally:
            sys.stdout = _saved
        self.audio = AudioDMA(self.ol.dma_tx)             # 播放 (TX)
        self.mic = MicCapture(self.ol.dma_rx)             # 录音 (RX)
        self.mic.start(buf_words=MIC_BUF)

        self.playing = threading.Event()                  # 置位=正在播报→暂停录音推送
        self.play_lock = threading.Lock()                 # 串行播报(队列在PC, 这里兜底)
        self.subs = set()                                 # mic 订阅者 socket
        self.subs_lock = threading.Lock()
        self.out_rate = self._measure_rate()
        # 录音线程启动前干净校准播放帧率 (MM2S wait 已修为等真正完成, 校准一次即准)
        hw = self.audio.calibrate(verbose=False)
        sys.stderr.write("[srv] ready: mic_rate=%d, play_rate=%d, hw_frame=%.0f\n" % (
            self.out_rate, PLAY_RATE, hw))
        sys.stderr.flush()

    def _measure_rate(self):
        n = 0; t0 = time.time()
        while time.time() - t0 < 0.5:
            n += len(self.mic.read_chunk())
        wr = n / (time.time() - t0)
        D = max(2, int(round(wr / TARGET_RATE)))
        self.d1 = max(1, D // 2); self.d2 = max(1, int(round(D / float(self.d1))))
        return int(round(wr / (self.d1 * self.d2)))

    # ── 录音线程: 持续抓 mic, 非播报时推给订阅者; 播报时读了丢弃(暂停) ──
    def mic_loop(self):
        dc = float(_POP[self.mic.read_chunk().astype(np.uint16)].mean())
        while True:
            if self.playing.is_set():
                time.sleep(0.005)                          # 播报中: 不读RX, 彻底让出总线(暂停录音)
                continue
            try:
                c = self.mic.read_chunk()
            except Exception:
                continue
            ones = _POP[c.astype(np.uint16)]
            dc = 0.99 * dc + 0.01 * float(ones.mean())
            sig = _box(_box(ones - dc, self.d1), self.d2)
            pcm = np.clip(sig * GAIN, -32768, 32767).astype('<i2').tobytes()
            with self.subs_lock:
                dead = []
                for s in self.subs:
                    try:
                        s.sendall(pcm)
                    except Exception:
                        dead.append(s)
                for s in dead:
                    self.subs.discard(s)

    # ── 处理一个连接 ──
    def handle(self, conn, addr):
        try:
            f = conn.makefile('rb')
            role = f.readline().strip()
            if role == b'MIC':
                conn.sendall(("RATE %d\n" % self.out_rate).encode())
                with self.subs_lock:
                    self.subs.add(conn)
                sys.stderr.write("[srv] MIC 订阅 %s\n" % (addr,)); sys.stderr.flush()
                while True:                                # 保持连接, 数据由 mic_loop 推
                    if not f.readline():
                        break
            elif role == b'PLAY':
                sys.stderr.write("[srv] PLAY 通道 %s\n" % (addr,)); sys.stderr.flush()
                while True:
                    hdr = f.read(4)
                    if len(hdr) < 4:
                        break
                    n = struct.unpack('<I', hdr)[0]
                    if n == 0:
                        continue
                    data = f.read(n)
                    if len(data) < n:
                        break
                    pcm = np.frombuffer(data, dtype='<i2')
                    with self.play_lock:
                        self.playing.set()                 # 暂停录音
                        try:
                            self.audio.play_pcm16(pcm, sample_rate=PLAY_RATE)
                        except Exception as e:
                            sys.stderr.write("[srv] play err: %s\n" % e); sys.stderr.flush()
                        self.playing.clear()               # 恢复录音
                    conn.sendall(b"DONE\n")
            else:
                conn.sendall(b"ERR unknown role\n")
        except Exception as e:
            sys.stderr.write("[srv] conn err %s: %s\n" % (addr, e)); sys.stderr.flush()
        finally:
            with self.subs_lock:
                self.subs.discard(conn)
            try: conn.close()
            except Exception: pass

    def serve(self, port):
        threading.Thread(target=self.mic_loop, daemon=True).start()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port)); srv.listen(16)
        sys.stderr.write("[srv] listening on :%d (MIC/PLAY)\n" % port); sys.stderr.flush()
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=self.handle, args=(conn, addr), daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--bit", default="audio_dma.bit", help="加载的 bitstream (合并设计用 ../ov5640_audio_mecanum.bit)")
    ap.add_argument("--no-download", action="store_true", help="附着已加载的overlay(与摄像头共用时, 不重下bitstream)")
    args = ap.parse_args()
    BoardServer(bitfile=args.bit, download=not args.no_download).serve(args.port)


if __name__ == '__main__':
    main()
