#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云 实时语音识别(一句话/流式) 客户端 —— 把音频转成中文文字, 支持热词.

凭证 (3 样):
  1) Appkey      : 项目页就有 (已内置默认值, 可用 --appkey 覆盖)
  2) AccessKeyId / AccessKeySecret : 阿里云账号级密钥(头像→AccessKey管理), 用来换 Token
     export ALI_AK_ID=LTAI...    ALI_AK_SECRET=....

音源:
  --file 录音.wav            # 文件(wav/m4a), 自动转 16k 单声道, 测试用
  --board                    # 连板子 board_server 的 MIC 口, 实时识别麦克风
热词:
  控制台在“热词”里给本项目建好热词表后, 通常**项目级自动生效**, 这里不用传;
  若你用 POP API 建了词表, 拿到 vocabulary_id 后用 --vocab-id 传进来.

用法:
  export ALI_AK_ID=...  ALI_AK_SECRET=...
  python3 asr_aliyun.py --file ../asr_test.wav
  python3 asr_aliyun.py --board
"""
import argparse
import json
import os
import ssl
import sys
import threading
import time
import uuid

import numpy as np
import websocket          # websocket-client
import tts_aliyun         # 复用其 get_token(AK/SK → Token)

URL = "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"
APPKEY = "o3KGrwxc2QkSp2IK"          # ← 你的项目 Appkey (小车语音识别)
BOARD_HOST, BOARD_PORT = "192.168.2.99", 8800


def _id():
    return uuid.uuid4().hex


class AliyunASR:
    def __init__(self, appkey, token, vocab_id=None, on_final=None, on_partial=None):
        self.appkey, self.token, self.vocab = appkey, token, vocab_id
        self.task_id = _id()
        self.ws = None
        self.started = threading.Event()
        self.completed = threading.Event()
        self.finals = []
        self.on_final = on_final          # 可选回调: 每句定稿时调 on_final(txt) (语音助手用)
        self.on_partial = on_partial      # 可选回调: 实时草稿时调 on_partial(txt) (网页显示用)

    def _hdr(self, name):
        return {"message_id": _id(), "task_id": self.task_id,
                "namespace": "SpeechTranscriber", "name": name, "appkey": self.appkey}

    def _on_msg(self, ws, msg):
        d = json.loads(msg)
        name = d["header"]["name"]
        if name == "TranscriptionStarted":
            self.started.set()
        elif name == "TranscriptionResultChanged":      # 中间结果(实时草稿)
            txt = d["payload"]["result"]
            sys.stdout.write("\r  …%s" % txt); sys.stdout.flush()
            if self.on_partial:
                try:
                    self.on_partial(txt)
                except Exception:
                    pass
        elif name == "SentenceEnd":                      # 一句定稿
            txt = d["payload"]["result"]
            self.finals.append(txt)
            sys.stdout.write("\r  ▶ %s%s\n" % (txt, " " * 12)); sys.stdout.flush()
            if self.on_final and txt.strip():
                try:
                    self.on_final(txt.strip())
                except Exception as e:
                    sys.stdout.write("[on_final 回调出错] %s\n" % e)
        elif name == "TranscriptionCompleted":
            self.completed.set()
        elif name == "TaskFailed":
            sys.stdout.write("\n[识别失败] %s\n" % d["header"].get("status_text"))
            self.started.set(); self.completed.set()

    def start(self):
        self.ws = websocket.WebSocketApp(
            URL, header=["X-NLS-Token: " + self.token],
            on_message=self._on_msg,
            on_error=lambda w, e: sys.stdout.write("\n[WS错误] %s\n" % e),
            on_close=lambda w, *a: self.completed.set())
        threading.Thread(target=self.ws.run_forever,
                         kwargs={"sslopt": {"cert_reqs": ssl.CERT_NONE}}, daemon=True).start()
        time.sleep(1.0)
        payload = {"format": "pcm", "sample_rate": 16000,
                   "enable_intermediate_result": True,
                   "enable_punctuation_prediction": True,
                   "enable_inverse_text_normalization": True}
        if self.vocab:
            payload["vocabulary_id"] = self.vocab        # API 建的词表才需要; 控制台建的项目级自动生效
        self.ws.send(json.dumps({"header": self._hdr("StartTranscription"), "payload": payload}))
        if not self.started.wait(10):
            raise RuntimeError("StartTranscription 超时 (检查 Token/Appkey/网络)")

    def send_audio(self, pcm_bytes):
        self.ws.send(pcm_bytes, opcode=websocket.ABNF.OPCODE_BINARY)

    def stop(self):
        try:
            self.ws.send(json.dumps({"header": self._hdr("StopTranscription")}))
            self.completed.wait(8)
        finally:
            try: self.ws.close()
            except Exception: pass
        return "".join(self.finals)


def pcm16_from_file(path):
    import av
    from scipy import signal
    cont = av.open(path); st = cont.streams.audio[0]
    res = av.AudioResampler(format="s16", layout="mono", rate=16000)
    parts = [rf.to_ndarray().reshape(-1) for fr in cont.decode(st) for rf in res.resample(fr)]
    return np.concatenate(parts).astype("<i2").tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--appkey", default=APPKEY)
    ap.add_argument("--vocab-id", default=None, help="POP API 建的热词表 id(控制台建的不用填)")
    args = ap.parse_args()

    ak_id = os.environ.get("ALI_AK_ID"); ak_sec = os.environ.get("ALI_AK_SECRET")
    if not (ak_id and ak_sec):
        print("缺少 AccessKey: 设 ALI_AK_ID / ALI_AK_SECRET 环境变量"); sys.exit(2)
    print("取 Token ...")
    token = tts_aliyun.get_token(ak_id, ak_sec)
    print("连接阿里云实时识别 ...")
    asr = AliyunASR(args.appkey, token, args.vocab_id)
    asr.start()
    print("✓ 已开始. (… = 实时草稿, ▶ = 定稿句)\n")

    try:
        if args.board:
            import socket
            s = socket.create_connection((BOARD_HOST, BOARD_PORT)); s.sendall(b"MIC\n")
            rl = b""
            while not rl.endswith(b"\n"):
                rl += s.recv(1)
            src_rate = int(rl.split()[1])
            from scipy import signal
            print("板子 MIC %dHz → 16k 实时识别, Ctrl+C 停止" % src_rate)
            carry = np.empty(0, np.int16)
            while True:
                d = s.recv(8192)
                if not d:
                    break
                x = np.frombuffer(d, "<i2")
                carry = np.concatenate([carry, x])
                if len(carry) >= src_rate // 5:           # ~200ms 一发
                    g = np.gcd(int(src_rate), 16000)
                    y = signal.resample_poly(carry.astype(np.float32), 16000 // g, src_rate // g)
                    asr.send_audio(np.clip(y, -32768, 32767).astype("<i2").tobytes())
                    carry = np.empty(0, np.int16)
        else:
            pcm = pcm16_from_file(args.file)
            print("音频 %.1fs, 推流中 ..." % (len(pcm) / 2 / 16000))
            for i in range(0, len(pcm), 3200):            # 3200B=100ms@16k
                asr.send_audio(pcm[i:i + 3200]); time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n用户停止")
    text = asr.stop()
    print("\n" + "=" * 48 + "\n整段结果: " + (text or "(空)"))


if __name__ == "__main__":
    main()
