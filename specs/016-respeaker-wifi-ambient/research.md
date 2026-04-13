# Research: reSpeaker XVF3800 WiFi 无线环境语音接入

**Date**: 2026-04-01
**Feature**: 016-respeaker-wifi-ambient

## R1: reSpeaker XVF3800 UDP 音频流格式

**Decision**: ESP32-S3 UDP 固件输出 16kHz/32-bit/2 声道立体声 PCM，桥接服务提取 Channel 1（ASR beam）并转为 16-bit 单声道。

**Rationale**: Seeed 官方 UDP 流固件文档确认此格式。Channel 0 为会议模式（AEC 处理后），Channel 1 为 ASR 优化波束，更适合语音识别场景。32-bit 转 16-bit 通过右移 16 位实现（保留高位有效数据）。

**Alternatives considered**:
- 6 声道固件：可获取原始麦克风数据，但增加带宽和处理复杂度，不必要
- 修改固件输出 16-bit：需要定制固件，维护成本高

## R2: WebSocket 客户端库选择

**Decision**: 使用 `websockets` 库（已是项目依赖）。

**Rationale**: LinChat 后端 voice 模块已使用 websockets 12.0+（ASR/TTS Gateway 客户端的 BaseWSClient 基类依赖此库），无需引入新依赖。API 简洁，原生支持 asyncio，支持自动 ping/pong 保活。

**Alternatives considered**:
- `aiohttp`：功能更全但更重，项目未使用
- `websocket-client`：同步库，不适合 asyncio 架构

## R3: LLM 意图分类超时策略

**Decision**: 超时设为 5 秒，超时默认返回 RECORD_ONLY（不回复），不穿透到后续规则链。

**Rationale**: kimi-k2.5 基准测试显示首 token ~1s、总生成 ~5s（430 tokens）。意图分类输入短（1-2 句话）、输出短（JSON ~50 tokens），实际耗时应在 2-4 秒。5 秒留有余量。用户明确要求"宁可不回复也不误回复"，超时不穿透避免规则链误触发。

**Alternatives considered**:
- 3 秒超时 + 穿透：更快响应但可能误回复
- 本地小模型分类：需要 GPU，增加复杂度

## R4: 意图分类 prompt 上下文

**Decision**: 传入最近 5 条消息（时间倒排，含 AI 回复和不同人的消息）+ 用户记忆，与主 Agent prompt 结构保持一致。

**Rationale**: 上下文有助于 LLM 理解对话脉络。例如用户先说"好饿"（RECORD_ONLY），再说"附近有什么好吃的"——有上下文更容易判断第二句是对 AI 说的。5 条消息平衡了上下文丰富度和 token 开销（控制在 ~500 tokens 以内，不显著增加 5 秒超时内的处理时间）。

**Alternatives considered**:
- 无上下文（每次独立判断）：最快但准确率低
- 3 条消息：上下文较少，可能不够
- 10 条消息：token 开销大，可能导致超时

## R5: 桥接服务部署架构

**Decision**: 独立 Python 脚本，放在 `scripts/respeaker_bridge/`，由 systemd 管理。

**Rationale**: 桥接服务是基础设施级进程（类似 frpc），需要开机自启和崩溃自动重启。systemd 是 dev machine 上已有的进程管理方案（frpc、wstunnel 均用 systemd）。放在 scripts/ 目录而非 backend/apps/ 是因为它不是 Django app，不依赖 Django ORM，是独立的网络桥接进程。

**Alternatives considered**:
- services.sh 集成：不支持开机自启和崩溃重启
- supervisord：额外引入依赖，项目未使用
- Docker 容器：过度设计，单个 Python 脚本无需容器化

## R6: 并发 ambient 会话处理

**Decision**: 单设备独占。reSpeaker 在线时，浏览器 ambient 连接被拒绝或降级为仅接收 TTS。

**Rationale**: 当前 VoiceConsumer 架构每个连接独立运行 ASR + 聚合器。如果两个连接同时采集同一用户的音频，同一句话会被两个 ASR 流处理两次，产生重复的 transcription 和 decision，可能触发 Agent 重复执行。单设备独占避免此问题，且家庭场景下不需要浏览器和设备同时监听。

**Alternatives considered**:
- 共存互斥：实现复杂，需要全局协调器
- 完全独立：接受重复处理，但 Agent 可能重复执行工具（如重复开灯）

## R7: I2S 引脚映射与固件选型（硬件确认）

**Decision**: 使用 XVF3800 I2S Slave 固件 v1.0.4（16kHz），ESP32-S3 作为 I2S Master。引脚映射从 PCB 丝印和原理图确认。

**确认来源**：
- PCB 丝印图（`pinout.jpg`）：XIAO ESP32-S3 插座两侧标注了所有 I2S 信号
- 原理图（`gpio_sk.png`）：XIAO 与 XVF3800 之间的 I2S 连接
- GitHub 仓库 `host_control/README.md` Output Selection 章节：确认双通道含义

**I2S 引脚映射**：

| I2S 信号 | reSpeaker 丝印 | XIAO 引脚 | ESP32-S3 GPIO | 方向 |
|----------|---------------|-----------|---------------|------|
| MCLK | MCLK | D10 | GPIO9 | ESP32→XVF3800 |
| BCLK | I2S_BCLK | D9 | GPIO8 | ESP32→XVF3800 |
| WS/LRCK | I2S_LRCK | D8 | GPIO7 | ESP32→XVF3800 |
| DATA_OUT | I2S_DATAO | D7 | GPIO44 | XVF3800→ESP32（音频输出）|
| DATA_IN | I2S_DATAI | D6 | GPIO43 | ESP32→XVF3800（AEC 参考）|

**双通道含义（官方文档确认）**：
- Channel 0（左声道）：AEC + 波束成形 + 后处理（适合会议/通话）
- Channel 1（右声道）：ASR 自动选择波束（适合语音识别）→ **桥接服务提取此通道**

**固件选型**：

| 固件文件 | 采样率 | XVF3800 角色 | ESP32 角色 | 状态 |
|---------|--------|-------------|-----------|------|
| `i2s_dfu_firmware_v1.0.4.bin` | 16kHz | I2S Slave | I2S Master | ⭐ 选用 |
| `i2s_master_*_v1.0.5_48k.bin` | 48kHz | I2S Master | I2S Slave | 备选（需下采样） |
| `i2s_master_*_v1.0.7_48k_test5.bin` | 48kHz | I2S Master | I2S Slave | 备选（测试版） |

**Rationale**: v1.0.4 直接输出 16kHz，匹配 LinChat ASR 要求，无需下采样，ESP32 端和桥接服务端代码更简单。固件文件已在本地 `~/github/reSpeaker_XVF3800_USB_4MIC_ARRAY/xmos_firmwares/i2s/`。

**Alternatives considered**:
- 48kHz Master 固件（v1.0.5/v1.0.7）：XVF3800 提供时钟，ESP32 代码略简单，但需在桥接服务中从 48kHz 下采样到 16kHz，增加复杂度和延迟
- USB 模式固件：需 USB 物理连接，无法无线部署

## R8: ESP32-S3 Arduino 固件设计

**Decision**: 自行编写 Arduino 固件（`scripts/respeaker_bridge/firmware/`），Seeed 官方仓库不含 ESP32-S3 代码。

**固件架构**：
- `config.h`：所有可配置参数（WiFi/UDP/I2S 引脚/缓冲区）
- `respeaker_udp_stream.ino`：WiFi 自动连接 + I2S Master RX + UDP 发送 + 帧统计

**关键设计决策**：
- ESP32 发送原始 32-bit/2ch PCM，格式转换交给 dev machine 桥接服务（CPU 更强）
- UDP 包大小 1024 bytes（128 样本 × 8ms），低于 MTU 1500 避免分片
- WiFi 断线时继续 I2S 读取（防 DMA 溢出），但不发送 UDP
- APLL 精确时钟生成 MCLK=4.096MHz（16kHz × 256）
- 每 10 秒输出帧统计日志（帧数/字节数/丢帧数/WiFi RSSI）

**Rationale**: Seeed 的 GitHub 仓库（`respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY`）仅包含 XMOS 固件和 Python 控制工具，不含 ESP32-S3 I2S/WiFi/UDP 代码。Arduino 固件需从零编写，但逻辑简单（读 I2S → 发 UDP），核心代码约 150 行。
