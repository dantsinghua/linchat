# reSpeaker XVF3800 WiFi Bridge Service

reSpeaker XVF3800（带 XIAO ESP32-S3）WiFi 麦克风阵列到 LinChat 的音频桥接服务。

## 架构

```
reSpeaker XVF3800 (WiFi)
  → ESP32-S3 (I2S Master RX → UDP 发送)
  → [宿主机 iptables DNAT] → dev machine
  → bridge.py (UDP 接收 → 音频转换 → WebSocket 转发)
  → LinChat 后端 (ASR → 聚合 → 决策 → Agent)
```

## 音频格式

| 阶段 | 格式 |
|------|------|
| ESP32 UDP 输出 | 16kHz / 32-bit / 2 声道 (立体声 PCM) |
| 桥接服务转换后 | 16kHz / 16-bit / 1 声道 (单声道 PCM) |
| LinChat 要求 | 16kHz / 16-bit / 单声道 PCM |

通道说明：Channel 0（左声道）= AEC + 后处理，Channel 1（右声道）= ASR 自动选择波束。桥接服务提取 Channel 1。

## 安装

桥接服务复用 LinChat 虚拟环境，无需额外安装依赖。

```bash
# 验证 websockets 已安装
source /home/dantsinghua/work/linchat/linchat/bin/activate
pip show websockets  # 应显示 16.0+
```

## 配置

创建 `scripts/respeaker_bridge/.env` 文件：

```ini
# 必填：设备 API Token（通过 POST /api/v1/voice/devices/ 注册获取）
DEVICE_TOKEN=your_device_token_here

# 可选配置（以下为默认值）
UDP_PORT=12345
WS_URL=ws://localhost:8002/ws/voice/
LOG_LEVEL=INFO
```

## 启动方式

### 手动启动（调试）

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/scripts/respeaker_bridge
python bridge.py
```

### systemd 服务（推荐）

```bash
# 安装服务
sudo cp respeaker-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload

# 启动并设置开机自启
sudo systemctl enable --now respeaker-bridge

# 管理命令
sudo systemctl status respeaker-bridge   # 查看状态
sudo systemctl restart respeaker-bridge  # 重启
sudo systemctl stop respeaker-bridge     # 停止
journalctl -u respeaker-bridge -f        # 实时日志
```

## 健壮性特性

| 特性 | 行为 |
|------|------|
| WS 断线重连 | 线性递增 3/6/9/12/15s，5 次失败后等 60s 重置，无限循环 |
| UDP 流中断检测 | 30 秒无数据记录 WARNING，恢复时记录 INFO |
| 启动时后端不可达 | 复用重连策略，持续重试不退出 |
| 崩溃自动重启 | systemd `Restart=always`，5 秒后重启 |
| 优雅关闭 | SIGTERM/SIGINT → 关闭 WS → 停止 UDP → 退出 |

## 固件刷写

### XVF3800 I2S Slave 固件

```bash
# USB-C 连接靠近 3.5mm 口的 USB 口
sudo dfu-util -R -e -a 1 -D ~/github/reSpeaker_XVF3800_USB_4MIC_ARRAY/xmos_firmwares/i2s/respeaker_xvf3800_i2s_dfu_firmware_v1.0.4.bin
```

### ESP32-S3 Arduino 固件

1. Arduino IDE 选择 Board: "XIAO_ESP32S3"
2. 修改 `firmware/config.h` 中的 WiFi SSID/密码和 UDP 目标地址
3. 编译并烧录 `firmware/respeaker_udp_stream.ino`

I2S 引脚映射：MCLK=GPIO9, BCLK=GPIO8, WS=GPIO7, DATA=GPIO44

## 网络配置

reSpeaker 设备在 WiFi 网段（192.168.3.x），dev machine VM 在 192.100.2.100。
宿主机（192.168.3.119）配置 iptables DNAT：

```bash
# 已持久化到 /etc/iptables/rules.v4
iptables -t nat -A PREROUTING -p udp --dport 12345 -j DNAT --to-destination 192.100.2.100:12345
```

## 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/scripts/respeaker_bridge
python -m pytest tests/test_bridge.py -v
```

## 故障排查

| 问题 | 检查 |
|------|------|
| UDP 无数据 | `ss -ulnp \| grep 12345`，检查宿主机 iptables DNAT |
| WS 连接失败 | 检查后端是否运行 (`ss -tlnp \| grep 8002`)，检查 DEVICE_TOKEN |
| ASR 转录质量差 | 检查桥接服务帧统计日志中的丢帧率，检查 WiFi 信号 |
| 30s UDP 中断警告 | 检查设备电源和 WiFi 连接 |
