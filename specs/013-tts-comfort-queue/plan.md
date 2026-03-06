# Implementation Plan: TTS 播报队列

**Branch**: `013-tts-comfort-queue` | **Date**: 2026-03-06 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/013-tts-comfort-queue/spec.md`

## Summary

语音模式下 Agent 推理耗时 2-6s，期间用户听到静音体验差。本特性引入 TTS 播报队列管理器（TTSPipelineManager），实现：
- 3 级递进安慰语音（3s 间隔触发）
- Agent 完成后完整回复文本一次性 TTS 播报（不做流式 TTS）
- Agent 出错时播报错误提示
- 段间 1s 静默、Barge-in 打断
- TTS 不可用时退化为纯文字模式

纯后端变更，无新数据模型、无新 API、无前端改动。

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Django 4.2+, asyncio, websockets 12.0+ (Gateway TTS WS)
**Storage**: N/A（无新数据模型，运行时管道变更）
**Testing**: pytest + pytest-asyncio
**Target Platform**: Linux server (ASGI uvicorn)
**Project Type**: web (backend only)
**Performance Goals**: 安慰语音在 Agent 推理 3s 后 2s 内开始播放；Barge-in 500ms 内停止 TTS
**Constraints**: 同一用户同时只有 1 个 pipeline 实例；TTS 队列串行执行
**Scale/Scope**: 家庭场景单用户系统

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 评估 | 状态 |
|------|------|------|
| 1.1 分层架构 | 变更限于 services 层（voice_pipeline.py + 新 tts_pipeline_manager.py），不涉及 views/repositories | ✅ PASS |
| 1.3 数据一致性 | 无数据模型变更 | ✅ N/A |
| 2.1 Python 规范 | 遵循 PEP 8 + Black + 类型注解 + Google docstring | ✅ PASS |
| 3.1 测试覆盖 | 新增 test_tts_pipeline_manager.py，服务层 ≥ 95% | ✅ PASS |
| 4.1 用户隔离 | 管道互斥锁按 user_id 隔离（复用现有 _pipeline_locks） | ✅ PASS |
| 4.3 LLM 异常处理 | 现有 Agent 错误处理不变，新增错误语音播报为附加功能 | ✅ PASS |
| 5.1 性能指标 | 安慰语音 3-5s 启动，TTS 不影响 Agent 推理延迟 | ✅ PASS |
| 9.2 单用户 | 家庭场景，无多用户并发控制 | ✅ PASS |

**结论**: 全部通过，无违规项。

## Project Structure

### Documentation (this feature)

```text
specs/013-tts-comfort-queue/
├── spec.md              # 特性规范
├── plan.md              # 本文件
├── research.md          # Phase 0（无需研究，标记 N/A）
├── checklists/
│   └── requirements.md  # 质量检查清单
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── apps/voice/services/
│   ├── tts_pipeline_manager.py   # 【新建】TTSPipelineManager 队列管理器 ~150 行
│   └── voice_pipeline.py         # 【修改】_run_pipeline_inner 改用 manager + _active_managers 注册
├── core/
│   └── settings.py               # 【修改】新增 4 个 VOICE_TTS_ 配置项
└── tests/voice/
    └── test_tts_pipeline_manager.py  # 【新建】单元测试
```

**Structure Decision**: 新增 `tts_pipeline_manager.py` 在现有 `apps/voice/services/` 目录下，遵循项目服务层目录模式。

## Phase 0: Research

无需研究。所有技术方案基于已有代码模式：
- TTSStreamClient 接口已验证（connect/configure/send_text_delta/send_text_done/wait_for_done/disconnect）
- asyncio.Queue + asyncio.Task + asyncio.Event 为标准 Python 异步模式
- 安慰计时器使用 asyncio.create_task(asyncio.sleep()) + CancelledError 安全取消

## Phase 1: Design

### 1.1 核心组件 — TTSPipelineManager

**位置**: `backend/apps/voice/services/tts_pipeline_manager.py`

**职责**: 管理 TTS 播报队列，协调安慰语音计时器、段间静默、完整文本播放。

**队列项类型**:

| item_type | 含义 | 触发 |
|-----------|------|------|
| `comfort` | 安慰语音 | 3s 计时器到期自动入队 |
| `response` | Agent 完整回复 | Agent 推理完成后入队 |
| `error` | 错误提示 | Agent 推理出错后入队 |
| `sentinel` | 停止信号 | shutdown() 时入队 |

**状态管理**:
- `_comfort_index`: 当前安慰级数（0-2），到达 3 不再触发
- `_comfort_enabled`: stop 后置 False，禁止所有安慰计时器
- `_cancelled`: barge-in 后置 True
- `_idle`: asyncio.Event，队列空时 set，有项时 clear
- `_last_end`: 上一段 TTS 结束时间戳，用于计算段间静默

**核心方法**:

| 方法 | 说明 |
|------|------|
| `start()` | 启动 worker task + 首个安慰计时器 |
| `enqueue(text, item_type)` | 非阻塞入队 |
| `start_comfort_timer()` | 启动/重启 3s 安慰倒计时 |
| `stop_comfort_timer()` | 永久停止安慰（Agent 完成/出错时） |
| `wait_idle()` | 等所有 TTS 播完 |
| `cancel()` | Barge-in — 清空队列 + 断开 TTS |
| `shutdown()` | Pipeline 结束时优雅清理 |

**Worker 循环**:
```
while True:
    item = await queue.get()
    if cancelled or sentinel: break
    ensure_gap(1s)       # 段间静默
    play_text(item.text) # 连接 TTS WS → send_text_delta → send_text_done → wait_for_done → disconnect
    if comfort 播完 and enabled: start_comfort_timer()  # 重启计时器
    if queue.empty(): idle.set()
```

### 1.2 voice_pipeline.py 改动

**改动前** (`_run_pipeline_inner`):
```
rate_limit → register_task → _connect_tts() → response.start
→ Agent.execute(): chunk → send_json(delta) + tts.send_text_delta(chunk)
→ _flush_tts() → response.end
```

**改动后**:
```
rate_limit → register_task → TTSPipelineManager(on_audio, voice).start()
→ _active_managers[user_id] = manager          # 注册，供 cancel() 直达
→ response.start
→ Agent.execute(): chunk → send_json(delta) + full_response 累积
→ Agent 完成: manager.stop_comfort_timer() + manager.enqueue(full_response)
   Agent 出错: manager.stop_comfort_timer() + manager.enqueue(ERROR_TEXT, "error")
→ manager.wait_idle() + manager.shutdown()
→ _active_managers.pop(user_id, None)          # 注销
→ response.end
```

**删除方法**: `_connect_tts()`, `_flush_tts()`

**cancel() 修改 + _active_managers 注册**:

语音模式的取消信号来自语音事件（barge-in / 停止词 / response.cancel），通过 `_active_managers` 类属性直接取消 TTS，不依赖 Agent 的三层取消链路（Redis 键 / signal_stop / Pub/Sub）。

```python
class VoicePipeline:
    _active_managers: ClassVar[dict[int, TTSPipelineManager]] = {}

    @classmethod
    async def cancel(cls, user_id: int) -> bool:
        success, _ = await InferenceService.cancel_task(user_id)  # 取消 Agent
        mgr = cls._active_managers.pop(user_id, None)              # 取消 TTS
        if mgr:
            await mgr.cancel()
            return True
        return success
```

### 1.3 settings.py 新增

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `VOICE_TTS_COMFORT_DELAY` | `3.0` | 安慰语音触发延迟（秒） |
| `VOICE_TTS_SEGMENT_GAP` | `1.0` | 播报段间静默（秒） |
| `VOICE_TTS_COMFORT_TEXTS` | `["正在思考，请稍后。", ...]` | 3 级安慰文本（JSON 数组） |
| `VOICE_TTS_ERROR_TEXT` | `"大模型调用失败了，请结合日志分析错误原因。"` | 错误播报文本 |

### 1.4 关键时序场景

**场景 A — Agent 2s 回复（无安慰）**:
```
t=0    start() → comfort_timer(3s)
t=2    Agent done → stop → enqueue(response) → TTS 播放
t=5    回复播完 → idle → pipeline 结束
```

**场景 B — Agent 8s 回复（安慰1 + 回复）**:
```
t=0    comfort_timer(3s)
t=3    安慰1 入队 → TTS 播放"正在思考，请稍后"
t=5    安慰1 播完 → restart timer(3s)
t=8    Agent done → stop → enqueue(response) → ensure_gap(1s) → TTS 播放
```

**场景 C — Agent 3.5s（安慰正在播 + 等播完 + gap）**:
```
t=3    安慰1 入队 → 开始播放
t=3.5  Agent done → stop → enqueue(response)
t=5    安慰1 播完 → worker 取 response → ensure_gap(1s)
t=6    TTS 播放回复
```

**场景 D — Barge-in（Agent 仍在跑）**:
```
t=3    安慰1 播放中
t=4    新语音指令 → VoicePipeline.cancel():
         ├─ cancel_task() → Agent 收到 interrupted
         └─ _active_managers.pop() → manager.cancel() → 清空队列 + 断开 TTS + idle.set()
       → wait_idle() 立即返回 → 旧 pipeline 释放锁 → 新 pipeline 开始
```

**场景 E — Barge-in（Agent 已完成，TTS 播放回复中）**:
```
t=8    Agent 完成 → 回复入队 → TTS 播放回复中
t=10   新语音指令 → VoicePipeline.cancel():
         ├─ cancel_task() → 三层链路失效（Agent 已完成，无效但无害）
         └─ _active_managers.pop() → manager.cancel() → 断开 TTS + idle.set()
       → wait_idle() 立即返回 → 旧 pipeline 释放锁 → 新 pipeline 开始
```

### 1.5 边界情况处理

| 情况 | 处理 |
|------|------|
| TTS 连接失败 | `_play_text` catch 异常，跳过该段，worker 继续 |
| Agent 空回复 | `full_response == ""` → 不 enqueue，等安慰播完 |
| VOICE_TTS_ENABLED=False | tts_manager=None，所有 TTS 跳过 |
| stop 与 countdown 竞态 | CancelledError 安全返回 + `_comfort_enabled` 双重检查 |
| cancel 与 worker 竞态 | `_cancelled` 标记 + queue 清空 + idle.set() |
| Agent 完成后 barge-in | cancel() 通过 `_active_managers` 直接取消 TTS，不依赖 Agent 取消链路 |

### 1.6 测试计划

**文件**: `backend/tests/voice/test_tts_pipeline_manager.py`

| 测试用例 | 验证点 |
|----------|--------|
| 3 级安慰递进 | mock sleep → 验证 3 次 comfort enqueue + 第 4 次不触发 |
| 段间 1s gap | mock time.monotonic → 验证 sleep 调用 |
| stop 清理 | stop 后 comfort 被清除、response 保留 |
| cancel 安全 | cancel 后 wait_idle 立即返回 |
| 快速回复 | 2s 内完成 → comfort 未触发 |
| TTS 连接失败 | play_text 异常 → worker 继续处理下一项 |
| TTS 禁用 | VOICE_TTS_ENABLED=False → manager 不创建 |
| Agent 完成后 barge-in | _active_managers.pop → manager.cancel → TTS 停止 |

**E2E 验证**:
- Playwright 登录 → 语音模式发送 HA 查询
- 确认：3s 后听到安慰语音 → Agent 完成后 1s 静默 → 完整回复播报

## Complexity Tracking

无违规项，无需复杂性追踪。
