# Research: 语音交互

**Feature Branch**: `009-voice-interaction`
**Date**: 2026-02-14

## 1. WebSocket 框架选型

**Decision**: Django Channels 4.0+

**Rationale**:
- Django 标准 WebSocket 方案，与现有 Django 生态无缝集成
- `AuthMiddlewareStack` 自动解析 httpOnly Cookie 认证（Web 端）
- Consumer 模式结构清晰，可独立测试
- Redis channel layer 提供跨进程通信能力（语音会话管理）
- 与 uvicorn 完全兼容（通过 ProtocolTypeRouter 分流 HTTP/WebSocket）

**Alternatives considered**:
- **Raw ASGI WebSocket**: 无新依赖但需手动实现路由/认证/协议解析，维护成本高
- **Starlette WebSocket**: uvicorn 内置支持但与 Django 并行运行，混用两套框架增加复杂度

**Implementation notes**:
- `core/asgi.py` 改为 `ProtocolTypeRouter`，HTTP 仍走 `get_asgi_application()`
- 添加 `CHANNEL_LAYERS` 配置使用 Redis DB0（与缓存共用，键前缀区分）
- uvicorn 启动命令不变

## 2. 音频采集与传输策略

**Decision**: 前端使用 Web Audio API (AudioContext + AudioWorklet) 实时采集 PCM16 16kHz mono

**Rationale**:
- llmgateway 要求 Binary 帧传输原始 PCM16 音频（无 WAV 头），每帧 960 bytes (30ms × 16kHz × 2bytes)
- MediaRecorder API 输出 webm/opus 格式，无法直接发送给 llmgateway
- AudioWorklet 在独立线程运行，不阻塞 UI，低延迟
- 可直接输出 PCM16 samples，无需编解码转换

**Alternatives considered**:
- **MediaRecorder + 服务端转码**: 增加延迟（录完才发送），不适合实时流式场景
- **MediaRecorder + 前端 FFmpeg (WASM)**: 引入 ~25MB WASM 包，打包体积超标
- **ScriptProcessorNode**: 已废弃，在主线程运行会阻塞 UI

**Implementation notes**:
- `usePCMAudioCapture.ts` 创建 AudioContext (sampleRate: 16000)
- AudioWorklet Processor 每 30ms 输出一帧 PCM16 Int16Array
- 通过 WebSocket binary 帧直接发送到 LinChat 后端
- 声纹注册复用 `usePCMAudioCapture.ts`，录制完成后合并 PCM16 帧并添加 WAV 头，通过 HTTP multipart 上传

## 3. Message 模型扩展策略

**Decision**: 在 Message 模型新增 `is_voice` 和 `speaker_id` 两个字段，音频文件通过 MediaAttachment 关联

**Rationale**:
- `is_voice` (BooleanField): 显式标记语音消息，支持高效过滤查询
- `speaker_id` (CharField): 存储 llmgateway 声纹识别结果，用于消息归属
- 音频文件复用 MediaAttachment 机制（media_type='audio'），自动获得过期清理、MinIO 存储、预签名 URL 等能力
- `audio_url` 和 `audio_duration` 不作为 Message 独立字段，通过 `message.attachments.filter(media_type='audio')` 获取，避免字段冗余

**Alternatives considered**:
- **四个字段全加 Message**: 冗余（audio_url/duration 与 MediaAttachment 重复）
- **全用 extra_data JSON**: 不可索引，查询效率低
- **新建 VoiceMessage 子表**: 过度设计，增加 JOIN 开销

## 4. 语音会话状态管理

**Decision**: Redis 瞬态存储，不持久化到 PostgreSQL

**Rationale**:
- 语音会话是临时状态，与 WebSocket 连接生命周期绑定
- WebSocket 断开时会话自动失效，无需持久化
- Redis TTL 自动清理（120s），防止僵尸会话

**Redis Key 设计**:
- `voice:session:{user_id}` → JSON `{state, upstream_connected, started_at}` TTL=120s
- `voice:active_conv:{user_id}` → TTL=30s（活跃对话超时）
- `voice:audio_chunks:{user_id}:{segment_id}` → 音频帧缓存用于保存到 MinIO

## 5. 上游 llmgateway WebSocket 连接

**Decision**: 使用 `websockets` 库建立异步 WebSocket 客户端

**Rationale**:
- 轻量级异步 WebSocket 客户端，与 asyncio 原生兼容
- 支持 Binary/Text 帧混合传输（PCM16 音频 + JSON 控制）
- 自动 Ping/Pong 心跳保活

**Alternatives considered**:
- **httpx WebSocket**: httpx 的 WebSocket 支持仍处于实验阶段
- **aiohttp**: 功能更重，引入不必要的 HTTP 服务器依赖

**Implementation notes**:
- `gateway_client.py` 封装上游连接生命周期
- 进入语音模式时建立持久连接，退出时断开
- 连接断开自动重连一次（FR-034a）

## 6. 前端音频格式：声纹注册 vs 语音模式

**Decision**: 两种场景统一使用 AudioWorklet 采集 PCM16，输出格式不同

| 场景 | 录音方式 | 格式 | 传输方式 |
|------|----------|------|----------|
| 语音模式 (P1) | AudioWorklet 实时采集 | PCM16 16kHz mono | WebSocket Binary 帧 |
| 声纹注册 (P4) | AudioWorklet 采集 | PCM16→WAV | HTTP multipart 上传 |

**Rationale**:
- 语音模式需要实时流式传输，必须用 AudioWorklet 输出 PCM16
- llmgateway 所有 HTTP 端点仅接受 WAV (PCM16, 16kHz, mono)，其他格式返回 E6001
- 声纹注册复用语音模式的 AudioWorklet 采集链路，录制完成后将 PCM16 帧合并并添加 44-byte WAV 头后上传
- 统一采集链路减少前端代码复杂度，不再需要维护两套录音逻辑

## 7. Nginx WebSocket 代理

**Decision**: 复用现有 Nginx 配置的 WebSocket 支持

**Rationale**:
- 现有 `/linchat/api/` 路由已配置 `proxy_set_header Upgrade $http_upgrade` 和 `Connection "upgrade"`
- 需新增 `/linchat/ws/` 路由指向后端 8002 端口

**Implementation notes**:
- Nginx 新增 `location /linchat/ws/` 配置块
- 设置 `proxy_read_timeout 86400s`（与 SSE 一致）
- WebSocket 端点路径: `/ws/voice/`

## 8. 响应决策引擎

**Decision**: 纯文本规则引擎，不引入 LLM 推理

**Rationale**:
- 规范明确"基于文本判断，不涉及模型推理"（User Story 5）
- 唤醒词检测 = 字符串匹配（精确匹配 + 模糊匹配）
- 紧急命令词 = 白名单匹配
- 活跃对话判断 = Redis TTL 30s

**Implementation notes**:
- `ResponseDecisionService` 输入: STT 转写文字 + speaker_id + 活跃状态
- 输出: RESPOND（回复）| RECORD_ONLY（仅记录）| STOP（停止当前操作）
- 唤醒词列表从用户 VoiceSettings 加载，默认 "小鱼"
