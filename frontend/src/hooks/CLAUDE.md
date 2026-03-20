# hooks 模块指南

## 模块概述

自定义 React Hooks，封装复杂的异步状态逻辑。核心 Hook `useChatStream` 管理完整的聊天状态机，其他 Hook 封装媒体录制和文档解析功能。

## 文件清单

| 文件 | 用途 |
|------|------|
| `useChatStream.ts` | 聊天流式响应管理（发送、停止、恢复、循环重连、历史加载、错误处理） |
| `useAudioRecorder.ts` | 语音录音封装（MediaRecorder API、时长限制 1-60 秒、格式输出 audio/webm） |
| `useAuth.tsx` | 认证 Hook（登录/登出/SSE 事件监听，分发 context_status 和 doc_parse_progress 事件） |
| `useDocParse.ts` | 文档解析生命周期管理（SSE 进度事件监听、REST 轮询降级、5 分钟超时保护） |
| `useVoiceMode.ts` | 语音模式核心状态机（整合 WebSocket、PCM 采集、Store 同步） |
| `useVoiceWebSocket.ts` | 语音 WebSocket 连接管理（连接/断开/消息收发/心跳/重连） |
| `usePCMAudioCapture.ts` | AudioWorklet PCM16 音频采集（16kHz / 单声道 / 30ms 低延迟帧） |
| `useVoiceErrorHandler.ts` | 语音交互异常状态管理（麦克风权限、页面可见性、网络状态） |

## 关键 Hook 说明

### useChatStream

管理完整聊天状态机，是聊天页面的核心 Hook。

**状态机:**

```
idle → sending → generating → done/interrupted/error
                     ↓
              stop → interrupted (停止生成)
```

**返回值:**

| 属性/方法 | 说明 |
|-----------|------|
| `messages` | 消息列表 |
| `isGenerating` | 是否正在生成 |
| `isCompacting` | 是否正在压缩上下文 |
| `isLoadingHistory` | 是否正在加载历史 |
| `hasMore` | 是否有更多历史消息 |
| `error` | 错误信息 |
| `failedContent` | 发送失败保留的文本 |
| `failedAttachments` | 发送失败保留的附件 |
| `gatewayRetryAfter` | Gateway 模型切换倒计时 |
| `send(content, attachments?)` | 发送消息（支持多模态附件） |
| `stop()` | 停止生成（并行调用 stopGeneration + cancelInference） |
| `resume(messageId)` | 继续生成（从 status=3 中断处恢复） |
| `loadMore()` | 加载更多历史消息 |
| `reload()` | 重置并重新加载 |
| `clearFailedContent()` | 清除失败内容 |

**核心逻辑:**

- **乐观更新**: `send()` 先创建临时用户/助手消息插入列表，SSE 流返回后用真实 ID 替换
- **失败恢复**: 发送失败时回滚消息列表，将内容和附件保存到 `failedContent` / `failedAttachments`
- **循环重连 (`reconnectWithRetry`)**: 统一重连函数，最多 12 次循环重连（12 x ~45s ≈ 9 分钟），覆盖文档解析等长耗时工具的多次 SSE 超时断开。每次重连前先通过 `getGeneratingMessage()` 检查后端状态，确认仍在生成时才发起 `reconnectStream()`。`send()` 的 `onError` 和 `loadHistory()` 的刷新重连均复用此函数
- **Gateway 错误处理**: 检测 SSE error 事件的 `data.gateway_error`，E3002 设置 `gatewayRetryAfter` 倒计时
- **挂载/卸载**: `useEffect` 挂载时加载历史，卸载时 `abort()` 取消请求

### useAudioRecorder

封装浏览器 MediaRecorder API。

**返回值:** `status`（idle/recording/stopped）、`duration`、`audioBlob`、`audioUrl`、`startRecording()`、`stopRecording()`、`reset()`、`error`

**约束:** 最短 1 秒，最长 60 秒（`MEDIA_LIMITS.MAX_DURATION_SECONDS`），到达上限自动停止。

### useAuth

认证 Hook，管理登录状态和 SSE 事件监听。

**SSE 事件分发:**

- `context_status`: 上下文监控事件，通过 `window.CustomEvent` 分发给监控面板
- `doc_parse_progress`: 文档解析进度事件，写入 `chatStore.docParseProgress` 并通过 `window.CustomEvent` 分发给 `useDocParse`；终态（completed/incomplete/failed）延迟自动清除
- `logout`: SSO 单点登出（SSO_CONFLICT / TOKEN_EXPIRED / ADMIN_KICK）

**返回值:** `isAuthenticated`, `user`, `checkAuth()`, `onLoginSuccess()`, `logout()`

### useDocParse

管理文档解析的完整生命周期。

**流程:** `parse(attachmentUuid, pages?)` -> 创建任务 -> 监听 `window.CustomEvent('doc_parse_progress')` -> 完成后自动获取 Markdown 结果

**鲁棒性:** 5 分钟超时保护（SSE 终态事件丢失时自动标记失败）+ 10s REST 轮询降级（SSE 断线期间通过 `getDocParseStatus()` 兜底检测状态）

**返回值:** `status`（idle/pending/processing/completed/failed）、`progress`、`result`、`error`、`parse()`、`reset()`、`statusText`

**约束:** 结果最大 8000 字符，超出截断并追加 `[内容已截断]`；页数超限（E6006）特殊提示。

### useVoiceMode (483 行)

语音模式核心状态机 Hook，整合 WebSocket、PCM 采集、Store 同步。

**状态机:**

```
idle → configuring → listening → recording → processing → responding → listening (循环)
                                    ↓ (用户取消)
                                interrupted (300ms 后回到 listening)
```

**返回值:**

| 属性/方法 | 说明 |
|-----------|------|
| `isActive` | 语音模式是否活跃（sessionState !== 'idle' && !== 'error'） |
| `sessionState` | 当前状态（VoiceSessionState 8 态） |
| `isRecording` | 是否正在录音 |
| `volumeLevel` | 音量级别（0.0~1.0） |
| `duration` | 录音时长（秒） |
| `currentResponse` | AI 回复（流式累积） |
| `currentTranscription` | STT 转写文本 |
| `error` | 错误信息 |
| `enableVoiceMode()` | 开启语音模式（重置状态 + 连接 WebSocket） |
| `disableVoiceMode()` | 关闭语音模式（断开 WS + 停止采集 + 重置 Store） |
| `cancelCurrentResponse()` | 取消当前 AI 回复 |
| `manualStartRecording()` | 手动开始录音（hold/toggle 模式） |
| `manualStopRecording()` | 手动停止录音 |

**依赖**: `usePCMAudioCapture`, `useVoiceWebSocket`, `voiceStore`

### useVoiceWebSocket (424 行)

WebSocket 连接管理 Hook，负责与后端 VoiceConsumer 通信。

**返回值:** `isConnected`, `error`, `connect()`, `disconnect()`, `configure()`, `sendAudio()`, `cancelResponse()`, `closeSession()`, `sendReconnect()`

**事件回调（16 种下行事件映射）:** onSessionConfigured, onSessionClosed, onVadSpeechStart, onVadSpeechEnd, onSpeakerIdentified, onResponseStart, onResponseDelta, onResponseEnd, onTranscriptionComplete, onTranscriptionFailed, onMessageSaved, onError, onSessionConflict, onSessionReconnected, onSessionReconnectFailed, **onDecisionResult**（014 新增：LLM 响应决策结果）

**特性:** WS URL `wss://{host}/linchat/ws/voice/`，心跳 30s，自动重连一次（2s 延迟），JSON + Binary 帧混合传输，`sendReconnect(config)` 方法（014 新增）

### usePCMAudioCapture (307 行)

AudioWorklet PCM16 音频采集 Hook，低延迟 30ms/帧。

**返回值:** `isCapturing`, `duration`, `error`, `startCapture()`, `stopCapture()`

**采集规格:** 16000Hz 采样率 / 单声道 / 每帧 480 samples = 960 bytes / 回声消除 + 噪声抑制

**依赖**: 无外部库，仅 Web APIs（AudioWorklet）

### useVoiceErrorHandler (128 行)

语音交互异常状态管理（权限、页面可见性、网络状态）。

**返回值:** `isMicDenied`, `isPageHidden`, `isOffline`, `errorMessage`, `setMicDenied()`

## 数据流

```
ChatPage
  ↓ 调用
useChatStream (核心 Hook)
  ├── chatService (SSE 流 / REST API)
  ├── mediaApi (cancelInference)
  ├── reconnectWithRetry (循环重连，send/loadHistory 共用)
  └── chatStore (状态管理)
        ↓ 状态
    MessageList / MessageInput / NetworkError (UI 组件)

useAuth (认证 + SSE 事件中枢)
  ├── SSE /events 长连接
  ├── context_status → CustomEvent → ContextMonitorPanel
  ├── doc_parse_progress → chatStore + CustomEvent → useDocParse
  └── logout → 路由跳转

ChatPage
  ↓ VoiceModeContainer (动态导入)
useVoiceMode (核心状态机)
  ├── useVoiceWebSocket (WebSocket 通信)
  ├── usePCMAudioCapture (PCM 采集)
  └── voiceStore (全局状态)
```

## 依赖关系

- `useChatStream` -> `chatService`、`mediaApi`（cancelInference）、`chatStore`、`authGuard`、`NetworkError`（getErrorMessage）
- `useAudioRecorder` -> `@/types/media`（MEDIA_LIMITS）
- `useAuth` -> `authGuard`、`chatStore`（docParseProgress）、`memberStore`
- `useDocParse` -> `mediaApi`（createDocParseTask、getDocParseResult、getDocParseStatus）
- `useVoiceMode` -> `useVoiceWebSocket`, `usePCMAudioCapture`, `voiceStore`
- `useVoiceWebSocket` -> 无外部依赖
- `usePCMAudioCapture` -> 无外部依赖
- `useVoiceErrorHandler` -> 无外部依赖

## 测试方法

- `useChatStream`: mock `chatService` 和 `chatStore`，验证发送/停止/恢复/重连流程
- `useAudioRecorder`: mock `navigator.mediaDevices.getUserMedia`，验证录音时长限制和状态转换
- `useDocParse`: mock `mediaApi` 和 `window.CustomEvent`，验证进度监听和结果获取
