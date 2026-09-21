#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云智能语音交互(NLS) 短文本语音合成(TTS): 文字 → 16000Hz PCM16 单声道.

凭证 (阿里云控制台「智能语音交互」创建项目后获得):
    export ALI_AK_ID=LTAI...            # AccessKey ID
    export ALI_AK_SECRET=....           # AccessKey Secret
    export ALI_APPKEY=....              # 项目 Appkey

self-test:
    python3 tts_aliyun.py "你好，代跑小车测试" out.pcm
"""
import base64
import hashlib
import hmac
import os
import sys
import time
import uuid
import urllib.parse

import requests

REGION = "cn-shanghai"
TTS_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"
META_HOST = "http://nls-meta.cn-shanghai.aliyuncs.com/"
DEFAULT_APPKEY = "o3KGrwxc2QkSp2IK"   # 项目"小车语音识别"的 Appkey (ASR/TTS 通用)

_token_cache = {"id": None, "exp": 0}


def _percent(s):
    return urllib.parse.quote(str(s), safe="~")


def get_token(ak_id, ak_secret):
    """用 AccessKey 换取 NLS Token (带缓存, 过期前自动复用)."""
    now = int(time.time())
    if _token_cache["id"] and _token_cache["exp"] - now > 60:
        return _token_cache["id"]
    params = {
        "AccessKeyId": ak_id, "Action": "CreateToken", "Version": "2019-02-28",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "Format": "JSON", "RegionId": REGION, "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0", "SignatureNonce": str(uuid.uuid4()),
    }
    q = "&".join("%s=%s" % (_percent(k), _percent(params[k])) for k in sorted(params))
    string_to_sign = "GET&%s&%s" % (_percent("/"), _percent(q))
    sig = base64.b64encode(hmac.new((ak_secret + "&").encode(), string_to_sign.encode(),
                                    hashlib.sha1).digest()).decode()
    params["Signature"] = sig
    r = requests.get(META_HOST + "?" + urllib.parse.urlencode(params), timeout=10)
    j = r.json()
    if "Token" not in j:
        raise RuntimeError("取 token 失败: %s" % j)
    _token_cache["id"] = j["Token"]["Id"]
    _token_cache["exp"] = j["Token"]["ExpireTime"]
    return _token_cache["id"]


def synthesize(text, appkey=None, ak_id=None, ak_secret=None,
               voice="xiaoyun", rate=16000, volume=50, speech_rate=0, pitch_rate=0):
    """文字 → PCM16 字节 (单声道, little-endian, sample_rate=rate, 默认16000)."""
    appkey = appkey or os.environ.get("ALI_APPKEY") or DEFAULT_APPKEY
    ak_id = ak_id or os.environ.get("ALI_AK_ID")
    ak_secret = ak_secret or os.environ.get("ALI_AK_SECRET")
    if not (appkey and ak_id and ak_secret):
        raise RuntimeError("缺少阿里云凭证: ALI_APPKEY / ALI_AK_ID / ALI_AK_SECRET")
    token = get_token(ak_id, ak_secret)
    payload = {
        "appkey": appkey, "token": token, "text": text,
        "format": "pcm", "sample_rate": rate, "voice": voice,
        "volume": volume, "speech_rate": speech_rate, "pitch_rate": pitch_rate,
    }
    r = requests.post(TTS_URL, json=payload, timeout=20)
    ct = r.headers.get("Content-Type", "")
    if ct.startswith("audio") or ct == "application/octet-stream":
        return r.content                       # 裸 PCM16 @ rate
    raise RuntimeError("TTS 失败: %s %s" % (r.status_code, r.text[:300]))


if __name__ == "__main__":
    txt = sys.argv[1] if len(sys.argv) > 1 else "你好，代跑小车语音合成测试。"
    out = sys.argv[2] if len(sys.argv) > 2 else "tts_out.pcm"
    pcm = synthesize(txt)
    open(out, "wb").write(pcm)
    print("✓ 合成 %d 字节 PCM16@16000 → %s (%.2fs)" % (len(pcm), out, len(pcm) / 2 / 16000))
