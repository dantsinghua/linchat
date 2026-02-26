# components/voice 模块指南

## 模块概述

语音交互的 UI 组件集合，包含语音控制面板、实时音频波形可视化和语音消息气泡。配合 Hooks 和 Zustand Store 实现完整的语音交互前端链路。

---

## 组件清单

| 文件 | 用途 |
|------|------|
| `VoiceModePanel.tsx` | 底部弹出式语音控制面板（录音按钮、波形显示、状态指示、说话人标签） |
| `VoiceWaveform.tsx` | 实时音频波形可视化（Canvas 绘制，支持录音/播放两种模式） |
| `VoiceMessageBubble.tsx` | 语音消息展示组件（STT 转写文本 + 内嵌音频播放器） |
| `VoiceModeContainer.tsx` | 语音模式容器（动态导入 + 状态同步 + 条件渲染） |

---

## 关键组件说明

### VoiceModePanel

- 底部弹出面板，进入语音模式后显示
- 录音按钮：长按/点击切换录音状态
- 波形区域：录音时展示实时输入波形（委托 VoiceWaveform）
- 状态显示：录音中 / 转写中 / 等待回复 / AI 说话中
- 说话人标签：声纹识别后显示当前说话人名称

### VoiceWaveform

- 基于 Canvas 的实时音频波形绘制
- 支持两种模式：录音输入波形（绿色）、播放输出波形（蓝色）
- 使用 `requestAnimationFrame` 驱动动画，自动跟随音频数据更新
- 通过 props 接收 PCM 音频数据或 AnalyserNode

### VoiceMessageBubble

- 语音消息专用气泡组件，区别于普通文本消息
- 上方显示 STT 转写文本（支持转写中状态的打字机效果）
- 下方内嵌迷你音频播放器（播放/暂停、时长显示）
- 支持转写失败时的降级显示（仅展示音频播放器）

### VoiceModeContainer (82 行)

语音模式容器组件，通过 `next/dynamic` 动态导入。

- 监听 `voiceStore.voiceMode` 状态变化
- voiceMode false->true: 调用 `enableVoiceMode()`，渲染 `VoiceModePanel`
- voiceMode true->false: 调用 `disableVoiceMode()`
- voiceMode=false 时返回 null（完全卸载，减少包体积）

---

## 相关 Hooks

| Hook | 路径 | 用途 |
|------|------|------|
| `useVoiceMode` | `hooks/useVoiceMode.ts` | 语音模式总控（开启/关闭、状态机管理、面板显隐） |
| `useVoiceWebSocket` | `hooks/useVoiceWebSocket.ts` | 语音 WebSocket 连接管理（连接/断开/消息收发/重连） |
| `usePCMAudioCapture` | `hooks/usePCMAudioCapture.ts` | PCM 音频采集（麦克风权限、AudioWorklet、采样率转换） |

---

## 全局状态

| 文件 | 路径 | 说明 |
|------|------|------|
| `voiceStore.ts` | `stores/voiceStore.ts` | Zustand 语音状态管理（语音模式开关、会话状态、当前说话人、音频队列） |

---

## 类型定义

| 文件 | 路径 | 说明 |
|------|------|------|
| `voice.ts` | `types/voice.ts` | 语音相关类型定义（WebSocket 消息类型、会话状态枚举、说话人信息、设备信息、语音设置） |

---

## 依赖关系

```
VoiceModePanel
  ├── VoiceWaveform (实时波形)
  ├── useVoiceMode (模式控制)
  ├── useVoiceWebSocket (WebSocket 通信)
  ├── usePCMAudioCapture (音频采集)
  └── voiceStore (全局状态)

VoiceMessageBubble
  ├── AudioPlayer (复用 components/chat/AudioPlayer)
  └── voiceStore (转写状态)

VoiceWaveform (独立, 通过 props 接收音频数据)
```

---

## 测试要点

- VoiceModePanel: 验证面板弹出/收起、录音按钮状态切换、状态文案显示
- VoiceWaveform: 验证 Canvas 绑定、动画帧回调、音频数据渲染
- VoiceMessageBubble: 验证 STT 文本渲染、音频播放器嵌入、转写失败降级
- useVoiceWebSocket: 验证 WebSocket 连接生命周期、消息收发、自动重连
- usePCMAudioCapture: 验证麦克风权限请求、PCM 数据输出、采样率转换