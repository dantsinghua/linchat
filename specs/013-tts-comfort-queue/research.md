# Research: TTS 播报队列

**Feature**: 013-tts-comfort-queue
**Date**: 2026-03-06

## Status: N/A — 无需研究

本特性基于已有代码模式，无技术未知项：

1. **TTSStreamClient 接口** — 已在 010-voice-agent-pipeline 验证，connect/configure/send_text_delta/send_text_done/wait_for_done/disconnect 完整可用
2. **asyncio 异步模式** — Queue + Task + Event + CancelledError 为标准 Python 异步原语
3. **安慰计时器** — asyncio.create_task(asyncio.sleep(N)) + cancel() 为常见模式
4. **Gateway TTS 协议** — 语音延迟测试已验证：config(voice) → text.delta(text) → text.done → binary PCM + audio.done
