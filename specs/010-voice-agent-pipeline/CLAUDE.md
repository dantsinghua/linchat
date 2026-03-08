# 010-voice-agent-pipeline 规范

> **状态**: ✅ 已完成

## 概述

语音推理管道，将 ASR 转录接入 LangGraph Agent Pipeline 并输出 TTS 流式合成。

## 核心产出

- VoicePipeline 编排器（`voice_pipeline.py`）— Agent + TTS + 持久化 + barge-in 打断
- TTS 流式客户端（`tts_stream_client.py`）— Gateway TTS WebSocket
- Consumer 3-Mixin 重构（SessionMixin / EventMixin / InferenceMixin）
- media app 从 chat 独立分离（MediaAttachment + MinIO 存储）
- 音频持久化服务（`voice_persist_service.py`）— PCM→WAV + MinIO 上传
- 频率限制 + 推理任务注册/取消
- 持续监听模式（continuous_listen）+ ResponseDecisionService 决策链

## 后续演进

- 013: TTS 安慰语音队列（TTSPipelineManager）
- 014: Jarvis 环境语音模式（话语聚合 + LLM 意图分类 + 跨设备 TTS）


<claude-mem-context>
# Recent Activity

### Mar 7, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1584 | 12:47 AM | 🔵 | LinChat Voice Pipeline Migration to Atomic Gateway Services | ~743 |
</claude-mem-context>
