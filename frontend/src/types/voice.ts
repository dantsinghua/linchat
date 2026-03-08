/**
 * 语音交互类型定义
 *
 * 参考: specs/009-voice-interaction/data-model.md
 */

/** 语音会话状态枚举 */
export type VoiceSessionState =
  | 'idle'
  | 'configuring'
  | 'listening'
  | 'recording'
  | 'processing'
  | 'responding'
  | 'interrupted'
  | 'error';

/** 录音模式 */
export type RecordingMode = 'hold' | 'toggle';

/** 声纹档案 */
export interface SpeakerProfile {
  id: number;
  gatewaySpeakerId: string;
  name: string;
  qualityScore: number | null;
  enrolledAt: string;
}

/** 注册设备 */
export interface RegisteredDevice {
  deviceUuid: string;
  name: string;
  isActive: boolean;
  createdAt: string;
  lastActiveAt: string | null;
}

/** 语音设置 */
export interface VoiceSettings {
  wakeWords: string[];
  recordingMode: RecordingMode;
  vadSensitivity: number;
}

/** 语音设置更新请求（snake_case 发送到后端） */
export interface VoiceSettingsUpdateRequest {
  wake_words?: string[];
  recording_mode?: RecordingMode;
  vad_sensitivity?: number;
}

/** 语音消息（扩展 Message 类型） */
export interface VoiceMessage {
  isVoice: boolean;
  speakerId: string | null;
}

/** WebSocket 消息基础类型 */
export interface VoiceWSMessage {
  type: string;
  [key: string]: unknown;
}

/** 语音模式 */
export type VoiceMode = 'voice_chat' | 'ambient';

/** WebSocket 下行事件类型（Server → Client） */
export type VoiceWSEventType =
  | 'session.configured'
  | 'session.closed'
  | 'vad.speech_start'
  | 'vad.speech_end'
  | 'speaker.identified'
  | 'response.start'
  | 'response.delta'
  | 'response.end'
  | 'transcription.complete'
  | 'transcription.failed'
  | 'message.saved'
  | 'aggregation.utterance_added'
  | 'aggregation.completed'
  | 'decision.result'
  | 'tts.started'
  | 'tts.completed'
  | 'error';

/** WebSocket 上行消息类型（Client → Server） */
export type VoiceWSCommandType =
  | 'session.configure'
  | 'session.close'
  | 'response.cancel';

/** WebSocket 事件（下行） */
export interface VoiceWSEvent extends VoiceWSMessage {
  type: VoiceWSEventType;
  data?: Record<string, unknown>;
}

/** response.delta 嵌套结构 */
export interface VoiceResponseDelta {
  response_id: string;
  delta: {
    content: string | null;
    audio: string | null;
  };
}

/** response.end usage 数据 */
export interface VoiceResponseUsage {
  input_tokens: number;
  output_tokens: number;
  audio_duration_ms: number;
}

/** message.saved 数据 */
export interface VoiceMessageSaved {
  user_message_id: number;
  user_message_uuid: string;
  assistant_message_id: number;
  assistant_message_uuid: string;
  response_id?: string;
  interrupted?: boolean;
}

/** transcription.complete 数据 */
export interface VoiceTranscription {
  text: string;
  message_id: number;
  segment_id: string;
}

/** 设备注册请求 */
export interface DeviceRegisterRequest {
  name: string;
}

/** 设备注册响应 */
export interface DeviceRegisterResponse {
  deviceUuid: string;
  name: string;
  apiToken: string;
}

/** aggregation.utterance_added 数据 */
export interface VoiceAggregationUtteranceAdded {
  text: string;
  buffer_count: number;
  timeout_remaining: number;
}

/** aggregation.completed 数据 */
export interface VoiceAggregationCompleted {
  aggregated_text: string;
  utterance_count: number;
  first_ts: number;
  last_ts: number;
}

/** decision.result 数据 */
export interface VoiceDecisionResult {
  decision: 'RESPOND' | 'RECORD_ONLY' | 'STOP';
  reason: string;
  confidence?: number;
}

/** session.configured 扩展 — ambient 模式 features 信息 */
export interface VoiceAmbientFeatures {
  utterance_aggregation: boolean;
  llm_decision: boolean;
  cross_device_tts: boolean;
}

/** 声纹注册请求 */
export interface SpeakerEnrollRequest {
  name: string;
  audioData: string; // base64 encoded
}
