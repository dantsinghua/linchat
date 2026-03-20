# stores 模块指南

## 模块概述

使用 Zustand 管理全局状态，包含聊天消息、媒体上传、模型配置和语音交互四个独立 Store。所有 Store 遵循统一模式：`create<State>()` 定义状态和 actions。

## 文件清单

| 文件 | 用途 |
|------|------|
| `chatStore.ts` | 聊天状态管理（消息列表、生成状态、错误、上下文压缩、Gateway 倒计时） |
| `uploadStore.ts` | 媒体上传状态管理（上传任务队列、进度跟踪、附件收集） |
| `modelStore.ts` | 模型配置状态管理（模型列表、加载状态） |
| `voiceStore.ts` | 语音状态管理（语音模式开关、会话状态、录音状态、转写文本、WebSocket 连接） |
| `memberStore.ts` | 成员管理状态（目标用户切换、成员列表、代查模式）— 015-family-multiuser |

## 关键状态和 Actions

### chatStore

**状态字段:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `messages` | `Message[]` | 消息列表 |
| `isLoadingHistory` | `boolean` | 正在加载历史消息 |
| `isGenerating` | `boolean` | AI 正在生成响应 |
| `currentRequestId` | `string \| null` | 当前生成的请求 ID（停止/恢复用） |
| `hasMore` | `boolean` | 是否有更多历史消息 |
| `error` | `string \| null` | 错误信息 |
| `failedContent` | `string \| null` | 发送失败时保留的输入内容 |
| `failedAttachments` | `MediaAttachment[] \| null` | 发送失败时保留的附件 |
| `isCompacting` | `boolean` | 正在压缩上下文 |
| `gatewayRetryAfter` | `number` | Gateway 模型切换倒计时秒数 |

**核心 Actions:**

- `addMessage()` / `updateMessage()` / `appendContent()`: 消息增删改
- `prependMessages()`: 向前追加历史消息（上滑加载更多）
- `setFailedContent()` / `setFailedAttachments()`: 失败恢复机制

### uploadStore

**状态字段:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `tasks` | `UploadTask[]` | 上传任务列表 |
| `completedAttachments` | `string[]` | 已完成的附件 UUID 列表 |

**核心 Actions:**

- `addTask()` / `removeTask()`: 任务管理
- `updateTaskProgress()`: 更新上传进度
- `completeTask()`: 标记完成并收集附件 UUID
- `clearTasks()`: 清空所有任务，释放预览 URL（`URL.revokeObjectURL`）
- `getCompletedUuids()`: 获取已完成附件的 UUID 列表

**工具函数（模块级导出）:**

- `createUploadTask(file)`: 创建上传任务对象，生成本地预览 URL
- `getTaskStatusText(task)`: 获取任务状态的中文显示文本

### modelStore

**状态字段:** `models`、`isLoading`、`error`

**核心 Actions:** `setModels()`、`updateModelInList()`、`reset()`

### voiceStore

**状态字段:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `voiceMode` | `boolean` | 语音模式开关 |
| `sessionState` | `VoiceSessionState` | 会话状态（8 态） |
| `isRecording` | `boolean` | 正在录音 |
| `currentTranscription` | `string` | STT 转写文本 |
| `recordingMode` | `RecordingMode` | 录音模式（hold/toggle） |
| `settings` | `VoiceSettings \| null` | 语音设置 |
| `error` | `string \| null` | 错误信息 |
| `isConnected` | `boolean` | WebSocket 连接状态 |
| `currentSpeakerId` | `string \| null` | 当前识别说话人 |
| `hasSpeakerProfile` | `boolean` | 用户是否已注册声纹 |

**核心 Actions:**

- `setVoiceMode()`: 控制语音面板开关
- `setSessionState()`: 更新会话状态
- `setIsRecording()`: 更新录音状态
- `setCurrentTranscription()`: 更新转写文本
- `setSettings()`: 更新语音设置
- `reset()`: 重置为初始状态

## 状态管理规范

- 使用 Zustand `create<State>()` 定义，不使用 Redux
- 组件内通过 `useXxxStore()` Hook 消费状态
- 组件外（如 SSE 回调、事件处理器）通过 `useXxxStore.getState()` 访问
- 每个 Store 提供 `reset()` 方法重置为初始状态

## 依赖关系

- `chatStore.ts` 依赖 `@/types`（Message）和 `@/types/media`（MediaAttachment）
- `uploadStore.ts` 依赖 `@/types/media`（UploadTask、UploadProgress、MediaAttachment）
- `modelStore.ts` 依赖 `@/types/model`（ModelConfig）
- `voiceStore.ts` 依赖 `@/types/voice`（VoiceSessionState, RecordingMode, VoiceSettings）
- `memberStore.ts` 依赖 `@/services/memberService`（API 调用）
- 五个 Store 之间无直接依赖

## 测试方法

- 直接调用 Store actions 验证状态变化
- 测试 `clearTasks()` 和 `reset()` 是否正确释放 `ObjectURL`
- 测试 `appendContent()` 的内容拼接逻辑
- 测试 `completeTask()` 是否正确收集 UUID 到 `completedAttachments`
