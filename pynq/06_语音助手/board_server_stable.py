#!/opt/python3.6/bin/python3.6
# -*- coding: utf-8 -*-
"""稳定启动入口：导入 board_server.BoardServer 后再进入服务。

直接把 board_server.py 当脚本跑时，SSH/后台启动偶发卡在入口阶段。
这个入口只做参数解析和显式导入，实际服务逻辑仍然在 board_server.py。
"""
import argparse
import sys
import traceback

sys.path.insert(0, "/home/xilinx/jupyter_notebooks/pynq_app")

from board_server import BoardServer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--bit", default="/home/xilinx/jupyter_notebooks/ov5640_audio_mecanum_hp1.bit")
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    try:
        BoardServer(bitfile=args.bit, download=not args.no_download).serve(args.port)
    except BaseException as exc:
        print("board_server_stable exception: %r" % (exc,), flush=True)
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
