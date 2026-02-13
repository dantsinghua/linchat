# stores 模块指南

## Store 列表

| Store | 职责 |
|-------|------|
| `chatStore.ts` | 聊天状态（messages、isGenerating、error、gatewayRetryAfter） |
| `uploadStore.ts` | 媒体上传状态（pendingFiles、uploadProgress、attachments） |
| `modelStore.ts` | 模型配置状态 |

## 状态管理规范

- 使用 Zustand（非 Redux）
- Store 通过 `create<State>()` 定义
- 组件通过 `useXxxStore()` 消费状态
- 外部访问（非 React 上下文）使用 `useXxxStore.getState()`


<claude-mem-context>
# Recent Activity

### Feb 13, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1049 | 11:01 AM | 🔵 | Frontend failedContent Recovery Only Restores Text Not Attachments | ~608 |
| #1046 | 11:00 AM | 🔵 | Upload Store clearTasks() Confirms Agent3 Finding on User Data Loss | ~516 |
</claude-mem-context>