# LinChat — 变更日志

> 基于 git 历史自动生成，按 Conventional Commits 分类
> 最后更新: 2026-03-31

---

## 近期变更 (最近 50 次提交)

### Features

- **015-family-multiuser**: 家庭多用户系统完整实现 — 成员管理、声纹绑定、上下文切换 (`8ad7391`)
- **014-jarvis-ambient-voice**: 环境监听模式 + UtteranceAggregator + TTSRouter + LLM 意图分类 (`ae76615`)
- **013-tts-comfort-queue**: TTS 播报队列管理器 + 安慰语音 + 错误播报 + barge-in 取消 (`ee95313`)
- **011+012**: 文档 SubAgent RAG 完整实现 + 解析进度条 (`3cf60a4`)
- **010-voice-agent-pipeline**: Gateway WS 迁移到 ASR 流式转录 + Agent Pipeline + TTS (`a89c1db`)
- **voice enriched**: 声纹识别 + 富上下文推理 (`1b89a75`)
- **009-voice-interaction**: 语音交互功能完整实现 + 多模态改进 (`4e1b25f`)
- **doc-parse**: 文档解析集成到 Agent 工具链 — GPU 互斥锁 + 超时配置 (`fa1e4c9`)
- **multimodal**: 多模态完整实现 — 图片/视频/音频/文档/TTS/语音录制 (`f1a8040`)
- **graph**: Home Assistant SubAgent 完整实现 (`82fe8a0`)
- **graph**: SubAgent 架构重构 + 监控面板记忆列表刷新 (`18f2a4a`)
- **graph**: web_search + python_exec 工具，前端支持引用上标 (`35e2976`)
- **monitor**: M1c 上下文监控面板实现 (`9fa601b`)

### Bug Fixes

- **sse**: 心跳超时检测解决 SSE 流断开前端卡死问题 (`67d07e6`)
- **ha**: brightness=None 崩溃 + 虚假成功 + Prompt 模板化 (`8061951`)
- **gateway**: Langfuse 单例化 + 移除同步 flush (`aae5b84`)
- **voice**: WebSocket 重连 + 发送风暴防护 + PCM 缓存编码 (`d3f9ba5`)
- **voice**: 语音推理频率限制逻辑反转 + STT 空响应崩溃 (`afb4a39`)
- **multimodal**: 流式多模态推理 E1000 错误 — SSE 错误检测 + max_tokens (`f2aa29f`)
- **multimodal**: 代码审查发现的 5 个高置信度问题 (`8aab8c4`)
- **graph**: Langfuse 3.x 兼容性 + PDF 文档解析诊断增强 (`c1a0348`)
- **memory**: daily-summary 静默失败 + 全链路日志 (`587c9cb`)
- **chat**: 消息丢失 + 监控面板刷新后数据消失 (`028e516`)
- **auth**: Token 过期后 401 无限循环请求 (`d62b525`)
- **auth**: 消除 token 过期后的 401 请求风暴 (`da70bf1`)
- **chat**: 优化消息输入框交互体验 (`3dc41e1`)
- **chat**: 异步上下文 SynchronousOnlyOperation 错误 (`07b1ae1`)
- **settings**: 模型配置卡片长名称溢出布局 (`2eab8c8`)

### Refactors

- **backend**: 激进代码精简 — 大文件拆分 + 公共工具提取 + 冗余删除 (`3389aac`)
- **voice**: 提取公共工具 + 精简语音模块代码 (`3410615`)
- **hooks**: 精简前端 hooks 层，总代码量减少 40% (`710ab74`)
- **services**: 精简前端 services 层，总代码量减少 41% (`913ec0c`)
- **users**: 精简用户认证模块，总代码量减少 48% (`3c83f28`)
- **chat**: 拆分 services.py 为服务包，提取 SSE 辅助模块 (`8148c0a`)
- **context/graph/memory**: M1b 代码重构 — 新建模块 + 前端优化 (`d648374`)

### Documentation

- 全量同步 CLAUDE.md + 监控面板 breakdown 空指针修复 (`82e2c5b`)
- CLAUDE.md 更新 + 过时文档清理 + 模型基准脚本 (`7812a5c`)
- 更新 backend/frontend 及子目录 CLAUDE.md (`58104ac`, `ea298e9`, `b4c58ac`)
- speckit.analyze 发现的 C1/F2 问题修复 (`b1a32fb`)
- 添加上下文与记忆管理特性规范 (`db17ae6`)
- 里程碑需求文档（M1/M1b/M1c/M2）(`0f2f9ec`)

### Security

- **memory**: user_id 隐式注入 + 工具使用指南增强 [R-004] (`0259c36`)

### Tests

- **test**: 修复 TestBuildContext 超时问题（49s→1s）(`0b6e799`)

### Chores

- Langfuse trace 名称区分 (`041a902`)
- 为 Message.created_time 添加数据库索引 (`ec21fc0`)

---

## 特性发布时间线

| 日期 | 特性 | 编号 |
|------|------|------|
| 2026-01 | 大模型聊天页面 | 001 |
| 2026-01 | ASGI 异步视图改造 | 002 |
| 2026-01 | 模型配置管理 | 003 |
| 2026-01 | 上下文与记忆管理 | 004 |
| 2026-01 | 动态监控面板 | 005 |
| 2026-02 | SubAgent 工具链 | 006 |
| 2026-02 | Home Assistant SubAgent | 007 |
| 2026-02 | 多模态 MiniCPM 接入 | 008 |
| 2026-02 | 语音交互 | 009 |
| 2026-02 | 语音 Agent Pipeline | 010 |
| 2026-03 | 文档 SubAgent + RAG | 011 |
| 2026-03 | 文档解析进度 | 012 |
| 2026-03 | TTS 播报队列 | 013 |
| 2026-03 | Jarvis 环境语音 | 014 |
| 2026-03 | 家庭多用户系统 | 015 |

---

*本文档由 autoresearch:learn 从 `git log` 自动生成。*
