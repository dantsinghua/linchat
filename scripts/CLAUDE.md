# scripts/ 目录开发指南

> 本目录包含 LinChat 平台的运维脚本和硬件桥接服务。

---

## 目录结构

```
scripts/
├── services.sh                # LinChat 应用服务管理脚本（唯一入口）
├── playwright_login.py        # Playwright 自动登录脚本（E2E 测试用）
└── respeaker_bridge/          # reSpeaker WiFi/Serial 音频桥接服务 (016)
    ├── bridge.py              # UDP 桥接主服务（ESP32 UDP → WebSocket）
    ├── serial_bridge.py       # 串口桥接服务（ESP32 Serial → WebSocket）
    ├── config.py              # 配置加载（.env + 环境变量）
    ├── audio_converter.py     # 音频格式转换（32-bit/2ch → 16-bit/1ch）
    ├── diagnose.py            # 串口音频诊断工具
    ├── quick_record.py        # 快速录音测试工具（3 秒录制 WAV）
    ├── respeaker-bridge.service  # systemd 服务单元文件
    ├── __init__.py
    ├── firmware/              # ESP32-S3 Arduino 固件
    │   ├── config.h           # WiFi/I2S/UDP 硬件配置
    │   ├── respeaker_udp_stream.ino       # UDP 流式固件（WiFi 方案）
    │   ├── respeaker_serial_stream.ino    # 串口流式固件 v1
    │   ├── respeaker_serial_stream_v2.ino # 串口流式固件 v2
    │   ├── respeaker_serial_stream_v3.ino # 串口流式固件 v3（I2S Slave + 降采样）
    │   ├── i2s_diagnose.ino               # I2S 信号诊断固件
    │   ├── i2s_record_test.ino            # I2S 录音测试固件
    │   ├── audiotools_record_test.ino     # AudioTools 录音测试
    │   └── audiotools_slave_test.ino      # AudioTools Slave 模式测试
    └── tests/
        ├── __init__.py
        └── test_bridge.py     # 全模块单元测试（57 个测试函数）
```

---

## services.sh -- 应用服务管理

LinChat 应用层服务的唯一管理入口，通过 PID 文件追踪进程，避免孤儿进程积累。

### 管理的服务

| 服务 | 进程 | PID 文件 | 日志文件 |
|------|------|----------|----------|
| 后端 | `uvicorn core.asgi:application --port 8002` | `.pids/backend.pid` | `/tmp/linchat-backend.log` |
| Celery Worker | `celery -A core worker` | `.pids/celery-worker.pid` | `/tmp/linchat-celery-worker.log` |
| Celery Beat | `celery -A core beat` | `.pids/celery-beat.pid` | `/tmp/linchat-celery-beat.log` |
| 前端 | `npm run start -- -p 3784` | `.pids/frontend.pid` | `/tmp/linchat-frontend.log` |

### 用法

```bash
./scripts/services.sh start     # 启动所有服务
./scripts/services.sh stop      # 停止所有服务（含孤儿进程清理）
./scripts/services.sh restart   # 先停后启
./scripts/services.sh status    # 查看运行状态 + Docker 服务
```

### 注意事项

- **禁止**手动 `nohup uvicorn/celery/npm &` 启动服务，会导致孤儿进程积累
- `stop` 操作会杀掉 PID 文件指向的进程组，并兜底清理 uvicorn/celery/next-server 孤儿进程
- Docker 服务（PostgreSQL/Redis/Langfuse 等）和 systemd 服务（Nginx/frpc/wstunnel）不受此脚本管理

---

## respeaker_bridge -- reSpeaker 音频桥接服务

### 模块概述

016-respeaker-wifi-ambient 特性的核心组件。将 reSpeaker XVF3800（4 麦克风阵列 + XIAO ESP32-S3）的音频流桥接到 LinChat 语音 WebSocket 端点，实现环境音频监听（ambient mode）。

提供两种桥接方案：

| 方案 | 入口文件 | 传输方式 | 适用场景 |
|------|----------|----------|----------|
| WiFi UDP | `bridge.py` | ESP32 UDP 广播 → 桥接服务 | 正式部署（设备与服务器同网段） |
| USB Serial | `serial_bridge.py` | ESP32 USB CDC 串口 → 桥接服务 | 调试开发（USB 直连电脑） |

### 数据流

```
reSpeaker XVF3800 麦克风阵列
  → XVF3800 DSP (波束成形 + AEC + 降噪)
  → I2S 输出 (16kHz / 32-bit / 2ch)
  → ESP32-S3 (I2S 接收 → UDP/Serial 发送)
  → bridge.py / serial_bridge.py
  → AudioConverter (32-bit/2ch → 16-bit/1ch, 提取 ASR 波束通道)
  → WebSocket (session.configure mode=ambient)
  → LinChat 后端 (ASR → 话语聚合 → LLM 决策 → Agent 响应)
```

### 音频格式

| 阶段 | 采样率 | 位深 | 通道 | 说明 |
|------|--------|------|------|------|
| XVF3800 I2S 输出 | 16kHz | 32-bit signed | 2ch | Ch0=AEC 后处理, Ch1=ASR 波束 |
| ESP32 UDP 包 | 16kHz | 32-bit signed | 2ch | 每包 1024 字节 (128 样本) |
| 桥接服务转换后 | 16kHz | 16-bit signed | 1ch | 提取右声道 (Ch1) 并右移 16 位 |
| LinChat 语音端点要求 | 16kHz | 16-bit signed | 1ch | 标准 PCM |

### 核心组件

| 文件 | 职责 |
|------|------|
| `bridge.py` | UDP 桥接主服务：UDP 接收 → 音频转换 → WebSocket 转发，含重连、帧统计、优雅关闭 |
| `serial_bridge.py` | 串口桥接服务：COM 串口读取 → 帧同步 → WebSocket 转发，支持 Windows/Linux |
| `config.py` | 配置管理：`.env` 文件解析 + 环境变量覆盖，优先级: 环境变量 > .env > 默认值 |
| `audio_converter.py` | PCM 格式转换：32-bit/2ch 交错立体声 → 16-bit/1ch 单声道，含首包校验 |
| `diagnose.py` | 串口诊断：读取原始数据、检测同步头、分析样本值、判断信号状态 |
| `quick_record.py` | 快速录音：通过串口录制 3 秒音频保存为 WAV 文件，用于验证固件输出 |

### 配置项

配置文件路径: `scripts/respeaker_bridge/.env`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DEVICE_TOKEN` | (必填) | 设备认证 Token，通过 `POST /api/v1/voice/devices/` 注册获取 |
| `UDP_PORT` | `12345` | UDP 监听端口 |
| `WS_URL` | `ws://localhost:8002/ws/voice/` | LinChat WebSocket 语音端点 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

### 健壮性机制

| 机制 | 行为 |
|------|------|
| WebSocket 断线重连 | 线性递增 3/6/9/12/15s，5 次失败后等 60s 重置，无限循环 |
| UDP 流中断检测 | 30 秒无数据记录 WARNING，恢复时记录 INFO |
| 队列溢出处理 | 队列满时丢弃最旧帧，避免延迟累积（队列容量 500） |
| 首包格式校验 | 校验首个 UDP 包是否为 1024 字节，通过后不再检查 |
| 优雅关闭 | SIGTERM/SIGINT → 关闭 WebSocket → 停止 UDP → 取消任务 |
| 崩溃自动重启 | systemd `Restart=always`，RestartSec=5 |

### 依赖

桥接服务复用 LinChat 虚拟环境（`/home/dantsinghua/work/linchat/linchat/`），核心依赖：

| 依赖 | 用途 | 使用方 |
|------|------|--------|
| `websockets` | WebSocket 客户端 | bridge.py, serial_bridge.py |
| `asyncio` | 异步事件循环 | bridge.py, serial_bridge.py |
| `struct` | PCM 音频字节解包/打包 | audio_converter.py, diagnose.py |
| `pyserial` | COM 串口通信 | serial_bridge.py, diagnose.py, quick_record.py |

### 运行方式

#### UDP 桥接（正式部署）

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/scripts/respeaker_bridge
python bridge.py                          # 使用 .env 配置
DEVICE_TOKEN=xxx python bridge.py         # 环境变量配置
```

systemd 服务（推荐）:

```bash
sudo cp respeaker-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now respeaker-bridge
```

#### 串口桥接（调试开发）

```bash
python serial_bridge.py --list                              # 列出 COM 口
python serial_bridge.py --port COM3 --token TOKEN           # 启动桥接
python serial_bridge.py --port COM3 --token TOKEN --debug   # 调试模式
```

#### 诊断工具

```bash
python diagnose.py --port COM5          # 串口音频信号诊断
python quick_record.py --port COM5      # 录制 3 秒 WAV 试听
```

### 固件

`firmware/` 目录包含 ESP32-S3 Arduino 固件源码，需通过 Arduino IDE 编译烧录。

| 固件 | 说明 |
|------|------|
| `config.h` | 共享配置：WiFi SSID/密码、UDP 目标地址、I2S 引脚映射、DMA 参数 |
| `respeaker_udp_stream.ino` | WiFi UDP 流式方案：I2S Master 接收 → UDP 发送 |
| `respeaker_serial_stream_v3.ino` | 串口流式方案 v3：I2S Slave + 3:1 降采样 → 16kHz 16-bit 串口输出 |
| `i2s_diagnose.ino` | I2S 信号诊断：检测 BCLK/WS/DATA 信号、采样率、位深 |

I2S 引脚映射（PCB 原理图确认）:

| 信号 | XIAO 引脚 | ESP32-S3 GPIO | 方向 |
|------|-----------|---------------|------|
| MCLK | D10 | GPIO9 | ESP32 → XVF3800 |
| BCLK | D9 | GPIO8 | ESP32 → XVF3800 |
| WS (LRCK) | D8 | GPIO7 | ESP32 → XVF3800 |
| DATA | D6 | GPIO43 | XVF3800 → ESP32 (实测确认) |

### 网络拓扑

```
reSpeaker (WiFi 192.168.3.x)
  → UDP :12345
  → 宿主机 (192.168.3.119) iptables DNAT
  → dev machine VM (192.100.2.100:12345)
  → bridge.py → ws://localhost:8002/ws/voice/
```

### 测试

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/scripts/respeaker_bridge
python -m pytest tests/test_bridge.py -v
```

测试覆盖 57 个测试函数，涵盖:

| 模块 | 覆盖范围 |
|------|----------|
| `audio_converter` | 格式转换、通道提取、首包校验、边界值 |
| `config` | 默认值、.env 解析、环境变量覆盖、缺失 Token 校验 |
| `bridge` | UDP 接收转发、WebSocket 事件处理、session.configure、断连丢帧 |

---

## playwright_login.py -- 自动登录脚本

Playwright 浏览器自动化脚本，用于 E2E 测试时自动登录 LinChat 平台。流程: 导航到登录页 → 拦截验证码 API → 从 Redis 查询验证码 → 填写表单提交。

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
python scripts/playwright_login.py
```
