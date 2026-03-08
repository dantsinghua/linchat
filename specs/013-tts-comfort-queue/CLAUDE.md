# 013-tts-comfort-queue 规范

> **状态**: ✅ 已完成

## 概述

TTS 播报队列管理器，实现安慰语音递进、Agent 完整回复一次性 TTS、错误播报、barge-in 取消。

## 核心产出

- TTSPipelineManager（`tts_pipeline_manager.py`）— asyncio 队列 + comfort/response/error/sentinel 4 种 QueueItem
- 安慰语音 3 级递进：每隔 VOICE_TTS_COMFORT_DELAY（3s）自动入队下一级安慰文本
- 段间静默控制（VOICE_TTS_SEGMENT_GAP = 1s）
- barge-in cancel：清空队列 + 断开当前 TTS + 取消 worker
- settings.py 新增配置：VOICE_TTS_COMFORT_DELAY / VOICE_TTS_SEGMENT_GAP / VOICE_TTS_COMFORT_TEXTS / VOICE_TTS_ERROR_TEXT

## 与 VoicePipeline 集成

```
Agent 开始 → TTSPipelineManager.start()（启动 worker + 安慰计时器）
Agent 流式输出 → 累积 full_response（不逐 chunk TTS）
Agent 完成 → stop_comfort_timer() + enqueue(full_response, "response")
Agent 出错 → stop_comfort_timer() + enqueue(error_text, "error")
全部播完 → wait_idle() → shutdown() → response.end
```

## 后续演进

- 014: Jarvis ambient 模式复用 TTSPipelineManager，on_audio 回调替换为 TTSRouter.get_on_audio_callback()


<claude-mem-context>
# Recent Activity

### Mar 7, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1585 | 12:48 AM | 🔵 | TTS Comfort Queue Requirements and Progressive Feedback Strategy | ~707 |
</claude-mem-context>
