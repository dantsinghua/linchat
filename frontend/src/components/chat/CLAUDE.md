# components/chat 模块指南

## 组件列表

| 组件 | 职责 |
|------|------|
| `MessageList.tsx` | 消息列表渲染（含 MessageBubble、MediaPreviewInBubble、TTSButton） |
| `MessageInput.tsx` | 消息输入框（含附件上传触发、录音按钮、发送/停止按钮） |
| `NetworkError.tsx` | 错误横幅（Gateway 倒计时重试、错误类型映射、自动消失） |
| `MediaUploader.tsx` | 媒体文件上传组件（拖拽/点击选择、格式/大小校验） |
| `MediaPreview.tsx` | 媒体附件预览（图片/视频/音频/文档类型渲染） |
| `AudioRecorder.tsx` | 语音录音组件（开始/停止、时长限制、波形显示） |
| `AudioPlayer.tsx` | 音频播放器（播放/暂停、进度条、打断清理） |
| `ContextMonitorPanel.tsx` | 上下文监控面板（Token 用量、记忆、工具调用） |
| `MarkdownRenderer.tsx` | Markdown 渲染（代码高亮、LaTeX） |
| `MermaidRenderer.tsx` | Mermaid 图表渲染 |

## 关键模式

- 所有组件使用 `memo()` 包裹避免不必要重渲染
- SSE 流数据通过 `useChatStream` Hook → `chatStore` → 组件 props 传递
- 错误处理：`getErrorMessage()` 将后端错误映射为用户友好提示
- Gateway E3002 模型切换：`NetworkError` 组件内置倒计时计时器

## 依赖关系

```
MessageList → useChatStore (messages), ttsApi (TTSButton)
MessageInput → uploadStore (附件), useAudioRecorder (录音)
NetworkError → getErrorMessage (错误映射)
MediaUploader → uploadStore, mediaApi
AudioRecorder → useAudioRecorder Hook
```


<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1049 | 11:01 AM | 🔵 | Frontend failedContent Recovery Only Restores Text Not Attachments | ~608 |
</claude-mem-context>