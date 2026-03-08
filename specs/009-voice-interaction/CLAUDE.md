# 009-voice-interaction 规范

> **状态**: ✅ 已完成

## 概述

语音交互基础设施，WebSocket 语音流 → Gateway ASR 实时转录 → 前端状态机。

## 核心产出

- WebSocket 语音通道（`ws/voice/`）+ Token 认证中间件
- ASR 流式转录客户端（`asr_stream_client.py`）
- 前端语音模式 UI 组件（VoiceModePanel / VoiceWaveform / VoiceMessageBubble）
- 前端 Hooks（useVoiceMode / useVoiceWebSocket / usePCMAudioCapture）
- 声纹注册/识别（SpeakerProfile + Gateway HTTP）
- 设备管理（RegisteredDevice + SM4 加密 Token）
- 语音设置（VoiceSettings: 唤醒词/录音模式/VAD 灵敏度）

## 后续演进

- 010: 语音 Agent Pipeline（Agent + TTS 集成）
- 013: TTS 安慰语音队列
- 014: Jarvis 环境语音模式


<claude-mem-context>
# Recent Activity

### Mar 7, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #1583 | 12:47 AM | 🔵 | LinChat Voice Interaction Specification and Architecture Decisions | ~767 |
</claude-mem-context>
