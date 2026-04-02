# Implementation Plan: reSpeaker XVF3800 WiFi 无线环境语音接入

**Branch**: `016-respeaker-wifi-ambient` | **Date**: 2026-04-01 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/016-respeaker-wifi-ambient/spec.md`

## Summary

通过 reSpeaker XVF3800（带 XIAO ESP32-S3）WiFi 麦克风阵列接入 LinChat 的 ambient 环境语音模式。核心实现包括三部分：(1) Python UDP→WebSocket 桥接服务，运行在 dev machine 上，接收设备 UDP 音频流并转发给 LinChat；(2) 开启并调优 LLM 意图分类，替代唤醒词作为主决策路径；(3) systemd 服务化管理，确保 24 小时稳定运行。

## Technical Context

**Language/Version**: Python 3.12（桥接服务复用 LinChat 虚拟环境依赖）
**Primary Dependencies**: websockets（WebSocket 客户端）、asyncio（异步事件循环）、struct/numpy（音频格式转换）
**Storage**: 无新增存储，复用现有 PostgreSQL（RegisteredDevice）+ Redis（会话状态）
**Testing**: pytest（桥接服务单元测试）+ 手动 E2E 验证（需硬件设备）
**Target Platform**: Linux（dev machine, Ubuntu 22.04）
**Project Type**: Web 应用扩展 + 独立桥接服务
**Performance Goals**: UDP→WebSocket 转发延迟 ≤ 200ms
**Constraints**: 设备与 dev machine 同一局域网（192.168.3.x）
**Scale/Scope**: 单设备单用户，家庭场景

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 合规 | 说明 |
|------|------|------|
| 1.1 关注点分离 | ✅ | 桥接服务为独立进程，不修改现有后端分层架构 |
| 1.2 接口设计标准 | ✅ | 复用现有 WebSocket 协议（ws/voice/），无新增 API |
| 1.3 数据一致性 | ✅ | 无新增数据模型，复用 RegisteredDevice |
| 1.4 简单设计 | ✅ | 桥接服务职责单一（UDP→WS 转发），LLM 意图分类复用现有框架 |
| 2.1 Python 规范 | ✅ | 桥接服务遵循 PEP 8 + Black + 类型注解 |
| 3.1 测试覆盖 | ✅ | 桥接服务核心逻辑（格式转换、重连）单元测试覆盖 |
| 4.1 认证 | ✅ | 设备 Token 认证，SM4 加密，符合豁免条款 |
| 4.3 LLM 异常处理 | ✅ | 意图分类超时默认 RECORD_ONLY，不阻塞流程 |
| 9.2 使用场景 | ✅ | 单设备独占，不引入并发控制 |

**GATE 结果**: 全部通过，无违规项。

## Project Structure

### Documentation (this feature)

```text
specs/016-respeaker-wifi-ambient/
├── spec.md              # 功能规范
├── plan.md              # 本文件
├── research.md          # Phase 0 研究输出
├── checklists/          # 质量检查清单
└── tasks.md             # Phase 2 任务清单（/speckit.tasks 生成）
```

### Source Code (repository root)

```text
scripts/
└── respeaker_bridge/           # 桥接服务（独立 Python 包）
    ├── bridge.py               # 主入口：UDP 接收 + WS 转发 + 事件循环
    ├── audio_converter.py      # 音频格式转换：32bit/2ch → 16bit/1ch
    ├── config.py               # 配置管理（.env 或命令行参数）
    └── README.md               # 桥接服务使用说明

backend/
├── apps/voice/
│   └── services/
│       └── response_decision_service.py  # 修改：LLM 超时默认 RECORD_ONLY
├── apps/context/
│   └── templates/
│       └── voice_intent_classify.j2      # 修改：增强 prompt（上下文+记忆）
└── core/
    └── settings.py                       # 修改：VOICE_DECISION_USE_LLM=True, timeout=5

/etc/systemd/system/
└── respeaker-bridge.service              # systemd 服务配置
```

**Structure Decision**: 桥接服务放在 `scripts/respeaker_bridge/`，与现有 `scripts/services.sh` 同级。它是独立运行的辅助进程，不是 Django app，不适合放在 `backend/apps/` 下。后端改动仅限配置和 prompt 调优，不新增模块。

## Implementation Phases

### Phase 1: 硬件准备与固件刷写（依赖设备到货）

- 刷入 XVF3800 I2S 固件（USB-DFU，一次性）
- 刷入 ESP32-S3 UDP 音频流固件（Seeed 官方 Arduino 示例）
- 配置 WiFi SSID/密码，验证 UDP 包可达 dev machine
- 用 `nc -lu <port>` 或 Python 脚本验证 UDP 数据接收

### Phase 2: 桥接服务开发（P1 核心）

**bridge.py** — 主服务，asyncio 事件循环：
- UDP 服务器：绑定指定端口，接收 reSpeaker 音频帧
- WebSocket 客户端：连接 LinChat `ws/voice/?token=xxx`，发送 `session.configure` 配置 ambient 模式
- 音频转发循环：UDP 帧 → `audio_converter` → WebSocket binary 帧
- 事件接收循环：接收 LinChat 返回的 JSON 事件（transcription/decision/error），记录日志
- 重连逻辑：WebSocket 断开自动重连（5 次，间隔递增 3/6/9/12/15s）
- 统计日志：每 60 秒输出帧数、字节数、丢帧数

**audio_converter.py** — 格式转换：
- 输入：32-bit 立体声 PCM（reSpeaker UDP 固件输出）
- 处理：提取 Channel 1（ASR beam）→ 32-bit 转 16-bit（右移 16 位或 clamp）
- 输出：16-bit 单声道 PCM（LinChat 要求）
- 纯 `struct` 模块实现，无外部依赖

**config.py** — 配置管理：
- 读取 `.env` 文件或命令行参数
- 配置项：UDP_PORT、WS_URL、DEVICE_TOKEN、LOG_LEVEL
- 默认值：UDP_PORT=12345、WS_URL=ws://localhost:8002/ws/voice/

### Phase 3: LLM 意图分类调优（P1 核心）

**settings.py 配置变更**：
```
VOICE_DECISION_USE_LLM = True
VOICE_DECISION_LLM_TIMEOUT = 5   # 从 1s 改为 5s
VOICE_DECISION_LLM_THRESHOLD = 0.6  # 适当降低阈值，偏向回复
```

**response_decision_service.py 修改**：
- LLM 超时后默认返回 `RECORD_ONLY`（当前代码超时返回 `None` 会穿透规则链，需改为直接返回 RECORD_ONLY）

**voice_intent_classify.j2 prompt 增强**：
- 传入最近 5 条消息（时间倒排，含 AI 回复和用户消息）
- 传入用户记忆摘要
- 三类判定：RESPOND（对 AI 的指令/问题）、RECORD_ONLY（人际对话/自言自语）
- JSON 输出：`{"decision": "RESPOND|RECORD_ONLY", "reason": "...", "confidence": 0.9}`

### Phase 4: systemd 服务化（P2 健壮运行）

**respeaker-bridge.service**：
```ini
[Unit]
Description=reSpeaker XVF3800 Bridge Service
After=network.target

[Service]
Type=simple
ExecStart=/home/dantsinghua/work/linchat/linchat/bin/python \
    /home/dantsinghua/work/linchat/scripts/respeaker_bridge/bridge.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Phase 5: 测试与验证

- 单元测试：audio_converter（格式转换正确性）、config（配置加载）、重连逻辑
- 集成测试：桥接服务 → LinChat WebSocket → ASR 转录 → 决策结果
- E2E 验证：对设备说话 → 看 LinChat 后端日志确认 transcription + decision
- LLM 意图分类准确率测试：20 条指令/问题 + 20 条闲聊，统计 RESPOND/RECORD_ONLY 准确率

## Key Design Decisions

| 决策 | 选择 | 理由 |
|------|------|------|
| 桥接服务位置 | `scripts/respeaker_bridge/` | 独立进程，非 Django app |
| 音频转换 | `struct` 模块 | 无外部依赖，性能足够 |
| WebSocket 库 | `websockets` | 已是项目依赖（voice 模块使用） |
| LLM 超时行为 | 默认 RECORD_ONLY | 用户明确要求"宁可不回复也不误回复" |
| 管理方式 | systemd | 与 frpc/wstunnel 一致 |
| 并发会话 | 单设备独占 | 避免重复 ASR 处理 |
| 断线音频 | 丢弃 | ASR 需要连续流，缓存无意义 |

## Risk & Mitigation

| 风险 | 影响 | 缓解 |
|------|------|------|
| ESP32 UDP 固件音频格式与文档不符 | 桥接服务无法正确转换 | bridge.py 首包验证格式，异常时日志告警 |
| LLM 意图分类 5s 仍超时 | 所有话语默认 RECORD_ONLY | 监控超时率，必要时切换更快的模型或降级到规则链 |
| WiFi 信号差导致 UDP 大量丢包 | ASR 转录质量下降 | 部署时确保 WiFi 信号覆盖，桥接服务统计丢帧率 |
| reSpeaker 硬件故障 | 无音频输入 | 桥接服务 30s 无数据告警，systemd 自动重启 |
