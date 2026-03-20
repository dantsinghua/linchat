# components/chat 模块指南

## 模块概述

聊天页面的 UI 组件集合，包含消息列表、输入框、错误提示、媒体上传/预览/播放/录制、上下文监控面板和 Markdown/Mermaid 渲染器。所有组件使用 `memo()` 包裹以优化渲染性能。

## 文件清单

| 文件 | 用途 |
|------|------|
| `MessageList.tsx` | 消息列表渲染（消息气泡、滚动锚定、上滑加载更多、附件渲染、上下文压缩提示、视频推理超时提示） |
| `MessageInput.tsx` | 消息输入框（空消息拦截、4000 字符限制、防抖 300ms、文件上传触发、语音录制、发送/停止按钮切换、失败内容恢复、成员头像按钮 015） |
| `NetworkError.tsx` | 网络错误横幅（错误类型映射、Gateway E3002 倒计时重试、自动消失、内联错误组件、useNetworkError Hook） |
| `MediaUploader.tsx` | 媒体文件上传（多文件选择上限 5 个、格式/大小/时长前端校验、逐个上传进度、UploadTile 预览卡片） |
| `MediaPreview.tsx` | 媒体附件预览（图片/视频/音频/文档按类型渲染、文件过期检测、AttachmentList 列表组件） |
| `AudioPlayer.tsx` | 音频播放器（播放/暂停、进度条、`stopAndClear()` 外部打断接口） |
| `AudioRecorder.tsx` | 语音录音组件（开始/停止/预览/发送/取消、时长限制 1-60 秒） |
| `ContextMonitorPanel.tsx` | 上下文监控面板（MonitorSidebar 侧边栏、ContextStatusBar 状态条、MonitorToggleButton 切换按钮、useContextMonitor Hook） |
| `ContextMonitorPanel.design.tsx` | 上下文监控面板设计稿（静态预览，含模拟数据） |
| `MarkdownRenderer.tsx` | Markdown 渲染（GFM 支持、代码高亮 rehype-highlight、HTML 标签 rehype-raw、Mermaid 图表委托、引用标记 `[[N]]` 转上标） |
| `MermaidRenderer.tsx` | Mermaid 图表渲染（语法验证、异步渲染、加载/错误状态、源代码查看） |

## 关键组件说明

### MessageList

- 内部包含 `MessageBubble` 子组件，按角色区分样式（用户右侧蓝底、AI 左侧灰底）
- 滚动锚定: 新消息自动滚动到底部（用户未向上滚动时）
- 上滑加载: `scrollTop < 100` 时触发 `onLoadMore()`
- 消息状态渲染: 生成中（光标动画）、中断（[已中断] + 继续按钮）、失败（红色提示）
- 附件渲染: 用户消息附件在文本上方，AI 消息附件在文本下方；音频使用 AudioPlayer，其他使用 AttachmentList
- 视频推理提示: 检测视频附件时长，超过 `duration * 2` 秒未收到首个 content 时显示等待提示

### MessageInput

- 输入校验: `trim()` 后空消息拦截，4000 字符上限
- 防抖: 发送 300ms 防抖，停止 500ms 防抖
- 失败恢复: 监听 `failedContent` 和 `failedAttachments`，自动恢复到输入框和 uploadStore
- 文本域自适应: 高度自动增长，最大 170px，超出后内部滚动
- 快捷键: Enter 发送 / Shift+Enter 换行，生成中 Enter 停止

### ContextMonitorPanel

- `useContextMonitor()` Hook: 监听 `window.CustomEvent('context_status')` 事件，维护 token 历史（增量模式）和上下文历史，通过 sessionStorage 跨刷新持久化
- `MonitorSidebar`: 四区块面板 -- 大模型输入输出（折线图）、当前上下文（堆叠柱状图+趋势折线图）、当前记忆（类型占比+记忆卡片）、工具调用（按输出 token 倒序）
- `ContextStatusBar`: 上下文占用率提示条，仅 warning/critical 时显示
- `MonitorToggleButton`: 侧边栏开关按钮
- 内部纯 SVG 图表组件: `MiniLineChart`（折线图+面积图）、`StackedBar`（横向堆叠柱状图）

### NetworkError

- 错误类型映射: `getNetworkErrorType()` 将错误信息分类为 connection/timeout/rate_limit/content_filter/quota_exceeded/unknown
- 友好提示: `getErrorMessage()` 将技术错误转换为用户可理解的中文提示
- 可重试判断: `isRetryableError()` -- content_filter 和 quota_exceeded 不可重试
- Gateway 倒计时: `gatewayRetryAfter > 0` 时显示模型切换倒计时，倒计时期间重试按钮禁用
- `InlineNetworkError`: 小型内联错误提示组件
- `useNetworkError()`: 网络错误状态管理 Hook

## 依赖关系

```
MessageList
  ├── MarkdownRenderer (AI 消息渲染)
  ├── AudioPlayer (音频附件播放)
  ├── MediaPreview (AttachmentList, 附件渲染)
  └── mediaApi (getMediaUrl)

MessageInput
  ├── MediaUploader (文件上传组件)
  ├── AudioRecorder (语音录制组件)
  ├── uploadStore (上传任务状态)
  └── mediaApi (uploadMedia)

NetworkError (独立, 无组件依赖)

MediaUploader
  ├── uploadStore (任务管理)
  └── mediaApi (uploadMedia)

AudioRecorder → useAudioRecorder Hook

ContextMonitorPanel (独立, 通过 window.CustomEvent 接收数据)

MarkdownRenderer → MermaidRenderer (mermaid 代码块)
```

## 测试方法

- MessageList: 验证滚动锚定逻辑、消息状态渲染、附件渲染
- MessageInput: 验证空消息拦截、字符限制、防抖、失败内容恢复、快捷键
- NetworkError: 验证错误类型映射、友好提示、倒计时逻辑
- MediaUploader: 验证格式/大小/时长校验、文件数量限制
- ContextMonitorPanel: 验证 CustomEvent 监听、历史数据累积、sessionStorage 持久化
