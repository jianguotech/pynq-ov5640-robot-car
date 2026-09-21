#!/bin/bash
# 启动板端音频语音助手 = board_server(MIC/PLAY) + car_audio_assistant(ASR/DeepSeek/TTS+连中继)
#
# 用法:
#   ./run_audio_assistant.sh            # 独立音频测试: board_server 下载 hp1 bit (无摄像头时)
#   ./run_audio_assistant.sh attach     # 与摄像头共存: 摄像头已加载好 bit, board_server 附着不重下
#
# 凭证放同目录 audio_keys.env (不进 git):
#   export ALI_AK_ID=...
#   export ALI_AK_SECRET=...
#   export ALI_APPKEY=o3KGrwxc2QkSp2IK      # 可选
#   export DEEPSEEK_API_KEY=sk-...
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
mkdir -p logs

if [ -f audio_keys.env ]; then set -a; . ./audio_keys.env; set +a; fi
: "${ALI_AK_ID:?缺 ALI_AK_ID (放 audio_keys.env)}"
: "${ALI_AK_SECRET:?缺 ALI_AK_SECRET}"
: "${DEEPSEEK_API_KEY:?缺 DEEPSEEK_API_KEY}"

BIT=/home/xilinx/jupyter_notebooks/ov5640_audio_mecanum_hp1.bit
BS_DIR=/home/xilinx/jupyter_notebooks/pynq_app
PY=/opt/python3.6/bin/python3.6
DL=""
[ "$1" = "attach" ] && DL="--no-download"

# 1) board_server (需 root 访问 MMIO/DMA)
sudo pkill -9 -f '[b]oard_server' 2>/dev/null || true
sleep 1
sudo sh -c "cd $BS_DIR && setsid nohup $PY -u board_server.py --bit $BIT $DL --port 8800 > $HERE/logs/board_server.log 2>&1 < /dev/null &"
echo "board_server 启动中 (DL='$DL')..."
for i in $(seq 1 30); do
  if (exec 3<>/dev/tcp/127.0.0.1/8800) 2>/dev/null; then echo "✓ board_server 已监听 8800 (@${i}s)"; break; fi
  sleep 1
done

# 2) car_audio_assistant (普通用户, 连 board_server + 中继 + 云端; setsid 脱离终端常驻)
pkill -f '[c]ar_audio_assistant.py' 2>/dev/null || true
sleep 1
setsid nohup $PY -u car_audio_assistant.py > logs/audio_assistant.log 2>&1 < /dev/null &
echo "✓ car_audio_assistant 已启动"
echo ""
echo "看日志:  tail -f $HERE/logs/board_server.log $HERE/logs/audio_assistant.log"
echo "停止:    pkill -f car_audio_assistant.py ; sudo pkill -f '[b]oard_server'"
