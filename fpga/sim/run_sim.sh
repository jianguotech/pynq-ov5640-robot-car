#!/bin/bash
# Run core_debug simulation
set -e
cd "$(dirname "$0")"

IVERILOG=/opt/homebrew/bin/iverilog
VVP=/opt/homebrew/bin/vvp

echo "=== Compiling ==="
$IVERILOG -g2012 -o tb_core.vvp \
    tb_core.v \
    xpm_fifo_async_sim.v \
    ov5640_dvp_capture_sim.v \
    /tmp/core_debug.v

echo "=== Running simulation ==="
$VVP tb_core.vvp
echo "=== Done ==="
