# Research: 014-jarvis-ambient-voice

## R-001: 话语聚合实现模式

**Decision**: 在 VoiceConsumer 内存中实现 UtteranceAggregator，使用 asyncio.Task 倒计时器检测静默超时

**Rationale**:
- 聚合缓冲区为短暂状态（秒级生命周期），无需持久化到 Redis
- asyncio.Task 在 ASGI 事件循环中原生支持，无额外依赖
- 每 user_id 一个实例，跟随 VoiceConsumer 生命周期自动清理
- CleanS2S 使用类似模式（Queue + timeout 检测），但其基于线程的设计不适合 Django Channels 的 asyncio 架构

**Alternatives considered**:
- Redis 缓冲区 + Lua 脚本超时检测 — 过度复杂，引入分布式状态同步问题
- Celery 延迟任务 — 延迟不可控（最小 1 秒粒度），无法精确到毫秒级重置

---

## R-002: LLM 意图分类调用方式

**Decision**: httpx 直连 DeepSeek API（方案 B），不经过 LangChain/LangGraph

**Rationale**:
- 分类是单次非流式请求（~100 tokens），LangChain ChatOpenAI 的初始化开销（~50ms）不划算
- httpx 直连延迟更低（~200-400ms vs ~300-500ms with LangChain）
- 复用 `model_service.get_active_model("tool")` 获取 API 配置，保持与现有模型管理一致
- JSON 模式 (`response_format={"type": "json_object"}`) 确保输出格式可靠
- 成本极低：~$0.60-15/月（取决于使用频率）

**Alternatives considered**:
- LangChain ChatOpenAI.ainvoke() — 功能过重，初始化开销大
- 本地小模型分类 — 需要额外 GPU 资源，部署复杂
- 纯规则引擎（无 LLM） — 准确率不足以区分"需要帮助"和"日常闲聊"

---

## R-003: 跨设备 TTS 路由机制

**Decision**: 使用 Django Channels 分组机制（group_send）广播 TTS 音频帧到同一 user_id 的非 ESP 连接

**Rationale**:
- Django Channels Redis 分组后端已在 settings.py 配置完成（Redis DB 3, 容量 1500）
- VoiceConsumer 已有 `_is_device_connection` 标记区分 ESP 和浏览器连接
- group_send 是 Channels 原生 API，无需引入新依赖
- ESP 连接加入分组但在 handler 中跳过 TTS 消息（仅接收管理消息）
- 浏览器连接接收并播放 TTS — 复用现有前端 TTS 播放逻辑

**Alternatives considered**:
- 独立的 Redis Pub/Sub 通道 — 需要额外订阅管理，与 Channels 重复
- 维护全局连接注册表 — 引入共享可变状态，并发管理复杂
- MQTT — 过度设计，引入新中间件依赖

---

## R-004: 环境监听 ASR 连接保活

**Decision**: 禁用 ambient 模式的空闲超时 + 延长会话 TTL 到 3600s + 利用 ASR WebSocket ping/pong 维持心跳

**Rationale**:
- 当前 ASRStreamClient 配置 `ping_interval=30, ping_timeout=60`，WebSocket 层心跳已具备
- 空闲超时（60s）是当前唯一的 ASR 断连触发器，禁用后连接可持续存活
- 会话 TTL 从 120s 延长到 3600s，每次音频帧到达自动续期（复用现有 refresh_session）
- Gateway ASR 连接无已知服务端主动断连限制（参考 linchat-integration-guide.md）

**Alternatives considered**:
- 定期重连（每 N 分钟断连重连） — 丢失 ASR 上下文，用户体验差
- 后端心跳探针（除 WS ping 外） — 多余，WS 协议已处理

---

## R-005: 聚合超时阈值选择

**Decision**: 默认 3.0 秒，可通过 `VOICE_AMBIENT_AGGREGATE_TIMEOUT` 配置调整

**Rationale**:
- CleanS2S 使用 `min_silence_duration_ms=1200ms` 作为 VAD 端点检测（过短，仅检测句内停顿）
- 当前 Gateway ASR `speech_pad_ms=2000ms` 用于触发转录（单句级别）
- 聚合超时需要更长：3 秒 = 用户说完一句后的自然停顿 + 考虑思考下一句的时间
- 3 秒是语音交互研究中常见的"完成发言"阈值（参考 Google Assistant / Alexa 设计）
- 过短（1-2s）会在用户思考间隙误触发；过长（5s+）会让用户等待焦虑

**Alternatives considered**:
- 2 秒 — 太短，用户短暂思考就会触发
- 5 秒 — 太长，用户会以为系统没反应
- 动态阈值（基于对话速度自适应） — 过度复杂，MVP 不需要
