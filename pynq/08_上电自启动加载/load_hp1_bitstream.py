#!/usr/bin/env python3
import argparse
import mmap
import os
import struct
import sys
import time


DEFAULT_BITFILE = "/home/xilinx/jupyter_notebooks/ov5640_audio_mecanum_hp1.bit"
DEFAULT_LOG = "/home/xilinx/load_hp1_bitstream.log"
MOTOR_BASE = 0x40000000
MAP_SIZE = 0x1000
REG_VERSION = 0x20
REG_HEARTBEAT = 0x24
EXPECTED_VERSION = 0x59590200


def log_line(log_path, text):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), text)
    print(line)
    try:
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_reg32(base, offset):
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    try:
        mem = mmap.mmap(
            fd,
            MAP_SIZE,
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
            offset=base,
        )
        try:
            return struct.unpack_from("<I", mem, offset)[0]
        finally:
            mem.close()
    finally:
        os.close(fd)


def verify_motor_ip(log_path):
    version = read_reg32(MOTOR_BASE, REG_VERSION)
    hb1 = read_reg32(MOTOR_BASE, REG_HEARTBEAT)
    time.sleep(0.2)
    hb2 = read_reg32(MOTOR_BASE, REG_HEARTBEAT)
    delta = (hb2 - hb1) & 0xFFFFFFFF
    ok = version == EXPECTED_VERSION and delta > 1000000
    log_line(log_path, "VERSION=0x%08X HEARTBEAT_DELTA_200MS=%d VERSION_OK=%d" %
             (version, delta, 1 if version == EXPECTED_VERSION else 0))
    return ok


def load_with_pynq(bitfile, log_path):
    from pynq import Bitstream
    Bitstream(bitfile).download()
    log_line(log_path, "LOAD_OK method=pynq bitfile=%s" % bitfile)


def load_with_xdevcfg(bitfile, log_path):
    with open(bitfile, "rb") as src:
        with open("/dev/xdevcfg", "wb") as dst:
            dst.write(src.read())
    log_line(log_path, "LOAD_OK method=xdevcfg bitfile=%s" % bitfile)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Load hp1 merged bitstream at boot.")
    parser.add_argument("--bitfile", default=DEFAULT_BITFILE)
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--delay", type=float, default=8.0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if os.geteuid() != 0:
        log_line(args.log, "ERROR root permission required")
        return 2

    log_line(args.log, "BOOT_LOAD_BEGIN bitfile=%s delay=%.1f" % (args.bitfile, args.delay))
    if args.delay > 0:
        time.sleep(args.delay)

    if not os.path.exists(args.bitfile):
        log_line(args.log, "ERROR bitfile missing: %s" % args.bitfile)
        return 3

    try:
        load_with_pynq(args.bitfile, args.log)
    except Exception as exc:
        log_line(args.log, "PYNQ_LOAD_FAILED error=%s" % exc)
        try:
            load_with_xdevcfg(args.bitfile, args.log)
        except Exception as fallback_exc:
            log_line(args.log, "XDEVCFG_LOAD_FAILED error=%s" % fallback_exc)
            return 4

    time.sleep(0.5)
    if verify_motor_ip(args.log):
        log_line(args.log, "BOOT_LOAD_PASS")
        return 0

    log_line(args.log, "BOOT_LOAD_FAIL motor IP verification failed")
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
