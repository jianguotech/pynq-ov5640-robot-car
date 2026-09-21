# 中转服务器部署与省流量配置

目标：服务器可以长期保持待命，但平时不测试小车时不持续产生视频流量。

## 1. 流量来源

`quick_relay_server.py` 本身不会主动生成视频或状态数据。流量主要来自：

| 场景 | 流量情况 |
|---|---|
| 没有车端、没有网页端连接 | 几乎 0 |
| 只有服务器进程常驻 | 几乎 0 |
| 车端连接但无人打开网页视频 | 只有 WebSocket 保活和少量状态流量；视频会按需暂停 |
| 网页端打开视频、小车端在线 | 产生 JPEG 视频流量 |
| 小车端没有启用 `--video-on-demand` | 会持续上传视频，容易产生空跑流量 |

省流量的关键不是关服务器，而是让小车端按需上传视频：

```bash
python3 car_net_client.py ... --video-on-demand
```

这个选项默认已经开启。服务器会在 `/web/video` 有网页观看者时，通过控制通道给小车端发送：

```json
{"type":"server_event","event":"video_demand","enabled":true}
```

无人观看时发送 `enabled=false`，小车端暂停发送 JPEG 帧。

## 2. VPS 安装

以 Ubuntu/Debian 为例：

```bash
sudo apt update
sudo apt install -y python3 python3-venv
mkdir -p ~/car_relay
cd ~/car_relay
python3 -m venv .venv
. .venv/bin/activate
pip install websockets
```

把下面文件上传到 `~/car_relay/`：

```text
quick_relay_server.py
```

手动测试启动：

```bash
export CAR_RELAY_TOKEN='换成一个长一点的随机token'
python quick_relay_server.py --host 0.0.0.0 --port 8000
```

服务器安全组/防火墙需要放行：

```text
TCP 8000
```

## 3. systemd 常驻服务

创建配置：

```bash
sudo nano /etc/systemd/system/car-relay.service
```

写入：

```ini
[Unit]
Description=Campus delivery car WebSocket relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/car_relay
Environment=CAR_RELAY_TOKEN=换成一个长一点的随机token
Environment=CAR_RELAY_HOST=0.0.0.0
Environment=CAR_RELAY_PORT=8000
ExecStart=/home/ubuntu/car_relay/.venv/bin/python /home/ubuntu/car_relay/quick_relay_server.py --ping-interval 30 --ping-timeout 15
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

如果你的 VPS 用户不是 `ubuntu`，把 `/home/ubuntu/car_relay` 改成实际路径。

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now car-relay
sudo systemctl status car-relay
```

查看日志：

```bash
journalctl -u car-relay -f
```

停止服务器：

```bash
sudo systemctl stop car-relay
```

重新启动：

```bash
sudo systemctl restart car-relay
```

## 4. 小车端推荐启动参数

真实 PYNQ 上：

```bash
python3 car_net_client.py \
  --server ws://你的服务器IP:8000 \
  --token 换成同一个token \
  --runtime /dev/shm/car_runtime \
  --motion-udp-host 127.0.0.1 \
  --motion-udp-port 50009 \
  --video-on-demand \
  --status-hz 0.5
```

`--status-hz 0.5` 表示 2 秒发一次状态，平时更省流量。调试时想更顺滑可以改回：

```bash
--status-hz 2
```

## 5. 网页端连接

打开 `test_web_client.html`，服务器地址填：

```text
ws://你的服务器IP:8000
```

Token 填和服务器一致的 token。

网页视频连接存在时，小车才会上传 JPEG 视频帧；关掉网页或断开视频连接后，服务器会通知小车暂停视频上传。

## 6. HTTPS / WSS 说明

课程演示阶段可以先用：

```text
ws://服务器IP:8000
```

如果后面要在 HTTPS 页面里嵌入这个网页，浏览器通常会要求 WebSocket 也使用：

```text
wss://域名
```

这时可以在服务器前面加 Caddy 或 Nginx 做 HTTPS 反向代理。课程联调不强制。

## 7. 最省钱的使用方式

平时：

```text
服务器 systemd 常驻
小车端程序不启动，或启动但使用 --video-on-demand
网页端不打开视频
```

测试时：

```text
启动小车端 car_net_client.py
打开网页端并连接
需要看画面时保持网页视频连接
测试结束后关闭网页或停止小车端
```

这样服务器处于待命状态时基本不产生可观流量；只有真实看视频时才会跑大流量。
