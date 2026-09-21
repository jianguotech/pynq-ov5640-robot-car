#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek 对话客户端 (OpenAI 兼容接口) —— 给"代跑小车"语音助手生成口语化回答.

凭证:
    export DEEPSEEK_API_KEY=sk-....        # https://platform.deepseek.com 申请

说明:
  * 跑在 **PC** 上 (板子无外网, 不能直接调云端 API). 板子只负责 MIC/PLAY.
  * 带简短对话记忆 (保留最近几轮), system 提示约束"答案短、口语化、适合朗读".
  * 接口与 OpenAI /chat/completions 一致, 返回 choices[0].message.content.

self-test:
    export DEEPSEEK_API_KEY=sk-...
    python3 deepseek_chat.py "三号楼怎么走"
"""
import os
import sys

import requests

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_SYSTEM = (
    "你是『代跑小车』的车载语音助手。用中文口语回答，"
    "尽量简短(40字以内)、直接、适合语音朗读，不要用 Markdown、表情或列表符号。"
)


class DeepSeek:
    def __init__(self, api_key=None, model=DEFAULT_MODEL, system=None,
                 max_tokens=200, temperature=0.7, keep_turns=6, timeout=30):
        self.key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not self.key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY (export DEEPSEEK_API_KEY=sk-...)")
        self.model = model
        self.system = system or DEFAULT_SYSTEM
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.keep_turns = keep_turns          # 保留最近 N 轮 (user+assistant 各算半轮)
        self.timeout = timeout
        self.history = []                     # [{"role":..,"content":..}, ...]

    def ask(self, user_text):
        """发一句用户文字, 返回助手回答 (str). 自动维护上下文记忆."""
        messages = ([{"role": "system", "content": self.system}]
                    + self.history
                    + [{"role": "user", "content": user_text}])
        r = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json"},
            json={"model": self.model, "messages": messages,
                  "max_tokens": self.max_tokens, "temperature": self.temperature,
                  "stream": False},
            timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError("DeepSeek %d: %s" % (r.status_code, r.text[:200]))
        reply = r.json()["choices"][0]["message"]["content"].strip()
        # 更新记忆 (截断到最近 keep_turns 轮)
        self.history += [{"role": "user", "content": user_text},
                         {"role": "assistant", "content": reply}]
        self.history = self.history[-2 * self.keep_turns:]
        return reply

    def reset(self):
        self.history = []


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "你好，你是谁？"
    ds = DeepSeek()
    print("Q:", q)
    print("A:", ds.ask(q))
