# LinChat 语音交互实施方案 (M4)

> **版本**: v1.0
> **日期**: 2026-02-13
> **状态**: 待开发
> **前置依赖**: M3 多模态（已完成）、llmgateway 语音能力（部分已有）
> **关联文档**: `M4-voice-interaction-requirements.md`、`llmgateway/docs/voice-capability-requirements.md`

---

## 1. 背景

基于 CleanS2S 语音对话框架的调研，结合 llmgateway 语音能力需求文档，设计 linchat 侧的语音交互实现方案。

**核心发现**: linchat 已有完整的音频文件交互链路：录音 → MinIO 上传 → MiniCPM-o 推理 → 文本 SSE → TTS 语音合成。语音模式只需在此基础上实现"自动化流转"。

---

## 2. 方案对比总览

| 维度 | 方案 A: 传统 ASR→LLM→TTS | 方案 B: MiniCPM-o 原生多模态 (推荐) |
|------|-------------------------|--------------------------------------|
| 架构复杂度 | 新建 WebSocket + 3 个 Gateway 客户端 + 语音管线编排 | 复用现有 HTTP 链路，改动极小 |
| 新增文件 | ~21 个文件，~1200 行 | 0 个新文件，修改 ~6 个文件，~80 行 |
| 后端改动 | 新建 `apps/voice/` App + ASGI WS 路由 | 仅修改 StreamChunk 增加 1 个字段 |
| 前端改动 | AudioWorklet + WebSocket + 全新 UI | 在现有组件上增加"语音模式"开关 |
| 延迟链路 | 录音→VAD→ASR→LLM→TTS→播放（6跳） | 录音→上传→MiniCPM-o→TTS→播放（4跳） |
| Gateway 依赖 | 需要 VAD/ASR/TTS 三个 API（均未实现） | 仅需现有 `/v1/chat/completions` + `/v1/audio/speech`（已可用） |
| 可立即开发 | 否（需等 Gateway 就绪） | **是** |
| 适用场景 | 实时连续对话 | 一轮一答语音交互 |

---

## 3. 方案 A: 传统 ASR → LLM → TTS

### 3.1 架构

```
前端 AudioWorklet(16kHz PCM) → WebSocket → 后端 VoiceConsumer
  → Gateway /v1/audio/vad (未实现)
  → Gateway /v1/audio/transcriptions (未实现)
  → LangGraph Agent (create_chat_agent)
  → 句子分割 → Gateway /v1/audio/speech (stream, 未实现)
  → WebSocket 回传音频块 → 前端播放
```

### 3.2 需新建文件

```
backend/
├── core/asgi.py                    (修改: HTTP/WS 路由分发)
├── apps/voice/
│   ├── consumer.py                 (新建: WebSocket Handler, ~120行)
│   └── services/
│       ├── voice_service.py        (新建: 语音管线编排, ~250行)
│       ├── vad_client.py           (新建: Gateway VAD, ~50行)
│       ├── asr_client.py           (新建: Gateway ASR, ~60行)
│       ├── tts_client.py           (新建: Gateway TTS流式, ~70行)
│       └── interruption.py         (新建: 打断控制器, ~50行)
frontend/
├── services/voiceService.ts        (新建: WS客户端, ~120行)
├── stores/voiceStore.ts            (新建: 语音状态, ~50行)
├── hooks/useVoiceChat.ts           (新建: 语音Hook, ~150行)
├── components/chat/VoiceModePanel.tsx  (新建: 全屏UI, ~80行)
├── workers/audioWorklet.js         (新建: PCM处理器, ~30行)
```

### 3.3 WebSocket 协议

**上行（前端→后端）：**

| type | 字段 | 说明 |
|------|------|------|
| `audio_data` | `data` (base64 PCM), `seq` | 10ms 音频帧 |
| `start_voice` | - | 开始语音会话 |
| `stop_voice` | - | 结束语音会话 |
| `interrupt` | - | 用户打断 |
| `playback_started` | - | 前端开始播放 |
| `playback_finished` | - | 前端播放完毕 |

**下行（后端→前端）：**

| type | 字段 | 说明 |
|------|------|------|
| `vad_status` | `speaking: bool` | VAD 状态 |
| `asr_final` | `text: string` | ASR 识别结果 |
| `llm_chunk` | `text: string` | LLM 文本流 |
| `tts_chunk` | `data: base64`, `is_last: bool` | TTS 音频块 |
| `interrupted` | - | 打断确认 |
| `error` | `message`, `code` | 错误 |

### 3.4 打断机制（参考 CleanS2S）

```
AI 未播放 → 用户说话正常
AI 播放中 + 用户说话(VAD=true) → 触发打断：
  1. llm_stop_event.set()      → Agent 循环中断
  2. 清空 TTS 队列              → 不再合成后续句子
  3. 发 "interrupted" 到前端    → 前端停止播放
  4. 重置打断状态               → 等待新输入
```

### 3.5 阻塞点

- llmgateway 需先实现 `/v1/audio/vad`、`/v1/audio/transcriptions`、`/v1/audio/speech`（stream）— 当前均为"规划中"
- Nginx 需新增 WebSocket 代理规则

### 3.6 结论

**方案 A 暂不可行**，需等 Gateway 语音 API 就绪。适合未来需要"实时连续语音对话"时演进。

---

## 4. 方案 B: MiniCPM-o 原生多模态（推荐）

### 4.1 核心思路

**现有链路已完整工作**：

```
用户录音 → useAudioRecorder → audioBlob
  → uploadMedia() → MinIO 存储 → attachment_uuid
  → [用户手动点发送] → handleSend() → onSend(content, attachments)
  → useChatStream.send() → POST /api/v1/chat/ (SSE)
  → AgentService.execute() → 检测音频附件 → MiniCPM-o → 文本流响应
  → SSE done → [用户手动点 TTS] → synthesizeTTS(message_uuid) → Gateway TTS → 播放
```

**语音模式只需自动化两个"手动"步骤**：
1. 录音结束 → **自动发送**（跳过手动点击发送按钮）
2. 回复完成 → **自动 TTS + 播放**（跳过手动点击 TTS 按钮）

### 4.2 需修改的文件（共 6 个）

#### 4.2.1 后端: 补充 `message_uuid` 到 SSE done 事件

**问题**: 当前 SSE `done` 事件只携带 `message_id`（int），TTS API 需要 `message_uuid`（string）。

**文件 1**: `backend/apps/chat/services/types.py`

```python
# StreamChunk 增加字段
message_uuid: Optional[str] = None  # done 事件时携带，供前端自动 TTS
```

**文件 2**: `backend/apps/graph/services/agent_service.py:888`

```python
# 当前:
yield StreamChunk(type="done", content="", message_id=assistant_msg.message_id)
# 改为:
yield StreamChunk(type="done", content="", message_id=assistant_msg.message_id,
                  message_uuid=assistant_msg.message_uuid)
```

**文件 3**: `backend/apps/chat/sse.py` — SSE JSON 序列化需确认包含 `message_uuid` 字段

#### 4.2.2 前端: 类型补充

**文件**: `frontend/src/types/index.ts:66`

```typescript
export interface ChatStreamEvent {
  // ...existing...
  message_uuid?: string;  // done 事件携带，用于自动 TTS
}
```

#### 4.2.3 前端: SSE 回调传递 message_uuid

**文件**: `frontend/src/services/chatService.ts:81`

```typescript
// 当前:
case 'done': onDone?.(data.message_id); break;
// 改为:
case 'done': onDone?.(data.message_id, data.message_uuid); break;
```

StreamCallbacks 类型的 `onDone` 签名更新:

```typescript
onDone?: (messageId?: number, messageUuid?: string) => void;
```

#### 4.2.4 前端: useChatStream 接收 message_uuid 并触发自动 TTS

**文件**: `frontend/src/hooks/useChatStream.ts:205`

```typescript
onDone: (msgId, msgUuid) => {
  // 更新消息状态
  store.updateMessage(msgId || realId || tempAssistant.message_id, {
    status: 1 as MessageStatus,
    ...(msgUuid ? { message_uuid: msgUuid } : {}),
  });
  store.setIsCompacting(false);
  resetStream();

  // 语音模式: 自动 TTS + 播放
  if (voiceModeRef.current && msgUuid) {
    autoTTS(msgUuid);
  }
},
```

`autoTTS` 函数:

```typescript
const autoTTS = async (messageUuid: string) => {
  try {
    const audioBlob = await synthesizeTTS(messageUuid);
    const url = URL.createObjectURL(audioBlob);
    const audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
  } catch (err) {
    console.warn('Auto TTS failed:', err);
  }
};
```

#### 4.2.5 前端: MessageInput 语音模式开关 + 自动发送

**文件**: `frontend/src/components/chat/MessageInput.tsx`

核心改动:
- 新增 `voiceMode` state
- `handleRecordingComplete` 在 `voiceMode=true` 时，上传完成后自动调用 `handleSend()`
- 添加"语音模式"切换按钮（在录音按钮旁边）

```typescript
const [voiceMode, setVoiceMode] = useState(false);

const handleRecordingComplete = useCallback(async (blob: Blob, duration: number) => {
  setIsRecording(false);
  const file = new File([blob], `voice_${Date.now()}.webm`, { type: 'audio/webm' });
  const task = createUploadTask(file);
  uploadStore.addTask(task);
  setContent('[语音消息]');

  uploadStore.updateTaskStatus(task.id, 'uploading');
  try {
    const response = await uploadMedia(file, (progress) => {
      uploadStore.updateTaskProgress(task.id, progress);
    });
    uploadStore.completeTask(task.id, response.data);

    // ★ 语音模式: 上传完成后立即自动发送
    if (voiceMode) {
      setTimeout(() => handleSend(), 100); // 微延迟让 store 更新
    }
  } catch (error) {
    uploadStore.updateTaskStatus(task.id, 'failed', (error as Error).message);
  }
}, [voiceMode, uploadStore, handleSend]);
```

#### 4.2.6 前端: 将 voiceMode 传递给 useChatStream

**文件**: `frontend/src/hooks/useChatStream.ts`

useChatStream 新增参数 `voiceModeRef: React.RefObject<boolean>`，在 `onDone` 中根据此 ref 判断是否触发 `autoTTS`。

使用 `RefObject` 而非状态值，避免 SSE 回调闭包捕获过期值。

### 4.3 完整数据流

```
语音模式 ON:
  用户点击录音 → 录音中... → 松开/点停止
    → useAudioRecorder 返回 blob
    → handleRecordingComplete:
        uploadMedia(file) → MinIO → completeTask
        → 自动调用 handleSend()
    → useChatStream.send('[语音消息]', [attachment])
        → POST /api/v1/chat/ SSE
        → AgentService.execute() → MiniCPM-o 推理
        → SSE content chunks → 文本逐字显示
        → SSE done {message_id, message_uuid}  ★新增 message_uuid
    → onDone 回调:
        → 更新消息状态
        → voiceModeRef.current === true
        → autoTTS(message_uuid)
            → synthesizeTTS() → Gateway /v1/audio/speech → audio blob
            → new Audio(blob).play() → 自动播放
```

### 4.4 改动量统计

| # | 文件 | 操作 | 改动量 |
|---|------|------|--------|
| 1 | `backend/apps/chat/services/types.py` | 修改 | +1 行 |
| 2 | `backend/apps/graph/services/agent_service.py` | 修改 | +2 行 |
| 3 | `frontend/src/types/index.ts` | 修改 | +1 行 |
| 4 | `frontend/src/services/chatService.ts` | 修改 | +3 行 |
| 5 | `frontend/src/hooks/useChatStream.ts` | 修改 | +30 行 |
| 6 | `frontend/src/components/chat/MessageInput.tsx` | 修改 | +25 行 |
| **合计** | **6 个文件** | **修改** | **~62 行** |

### 4.5 验证方案

```bash
# 1. 后端测试 — StreamChunk message_uuid
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest tests/chat/ -v -k "stream"

# 2. 后端服务重启
kill $(pgrep -f "uvicorn core.asgi") 2>/dev/null
nohup uvicorn core.asgi:application --host 0.0.0.0 --port 8002 > /tmp/linchat-backend.log 2>&1 &

# 3. 前端构建
cd /home/dantsinghua/work/linchat/frontend
npm run build && npm run start -- -p 3784

# 4. E2E 验证（Playwright）
# a. 登录 linchat
# b. 开启"语音模式"开关
# c. 点击录音 → 说一句话 → 停止录音
# d. 验证: 自动上传 → 自动发送 → 文本流式显示 → 自动 TTS 播放
# e. 关闭"语音模式"→ 录音行为恢复为手动发送
```

---

## 5. 推荐方案

**推荐方案 B**，理由:

1. **即刻可用** — 不依赖 Gateway 新增 API
2. **改动极小** — 6 个文件 ~62 行，无新文件，零架构变更
3. **复用彻底** — 录音、上传、Agent 执行、TTS 全链路复用
4. **风险低** — 每个改动点都是在已验证的代码路径上加一个条件分支
5. **可演进** — 未来 Gateway 语音 API 就绪后，可在此基础上升级为方案 A 的实时体验

**方案 A 作为远期规划保留**，待 llmgateway 实现 VAD/ASR/TTS 流式 API 后再实施。

---

## 6. 关键复用点

| 复用目标 | 来源文件 | 说明 |
|---------|---------|------|
| 录音采集 | `frontend/src/hooks/useAudioRecorder.ts` | MediaRecorder → webm blob |
| 文件上传 | `frontend/src/services/mediaApi.ts` | uploadMedia → MinIO |
| 消息发送 | `frontend/src/hooks/useChatStream.ts` | send() → SSE stream |
| Agent 执行 | `backend/apps/graph/services/agent_service.py` | MiniCPM-o 多模态推理 |
| TTS 合成 | `frontend/src/services/ttsApi.ts` | synthesizeTTS → Gateway → audio blob |
| TTS 后端 | `backend/apps/chat/services/tts_service.py` | TTSService.synthesize() |

---

## 7. 风险与应对

| 风险 | 应对 |
|------|------|
| TTS 合成延迟（长文本） | 可限制自动 TTS 的文本长度上限（如 ≤500 字） |
| 音频上传耗时 | 已有 progress 回调，语音文件通常较小（<1MB） |
| 浏览器自动播放限制 | 用户已有交互（点击录音），满足 autoplay 策略 |
| MiniCPM-o 音频推理延迟 | 已验证可用，通常 3-6 秒响应 |
