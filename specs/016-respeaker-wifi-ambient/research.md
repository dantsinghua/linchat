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
