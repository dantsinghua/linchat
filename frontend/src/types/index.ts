/**
 * LinChat 类型定义
 *
 * 将在 T017 完善
 */

// ============ API 响应类型 ============

export interface ApiResponse<T = unknown> {
  code: string;
  message: string;
  data: T;
}

// ============ 用户相关类型 ============

export interface User {
  user_id: number;
  username: string;
  member_type?: 'member' | 'guest';  // 015-family-multiuser
}

export interface LoginRequest {
  username: string;
  password: string;
  captchaId: string;
  captchaCode: string;
}

export interface CaptchaResponse {
  captcha_id: string;
  captcha_image: string; // base64
}

// ============ 聊天相关类型 ============

export type MessageRole = 'user' | 'assistant' | 'system';
export type MessageStatus = 0 | 1 | 2 | 3; // 0-失败, 1-正常, 2-生成中, 3-中断

/**
 * 消息实体
 * 参考: data-model.md#2.2 消息表
 */
export interface Message {
  message_id: number;
  message_uuid: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  sequence: number;
  created_time: string;
  request_id?: string | null;
  model_name?: string | null;
  response_time_ms?: number | null;
  /** 媒体附件列表（多模态消息） */
  attachments?: import('@/types/media').MediaAttachment[];
  /** 语音消息标记 */
  is_voice?: boolean;
  /** 说话人 ID（声纹识别） */
  speaker_id?: string | null;
  /** 说话人显示名（已识别=用户名，未识别=unknown_XX） */
  speaker_name?: string | null;
}

export interface ChatRequest {
  content: string;
}

/**
 * SSE 流式响应事件
 * 参考: process-model.md#三、消息发送与流式响应流程
 */
export interface ChatStreamEvent {
  type: 'content' | 'done' | 'error' | 'interrupted' | 'context_compacting' | 'context_compacted' | 'heartbeat';
  content: string;
  message_id?: number;
  request_id?: string; // 首个 chunk 返回，用于停止/继续生成
  data?: {
    gateway_error?: string; // E3001/E3002
    retry_after?: number;   // E3002 模型切换等待秒数
    content_control?: boolean; // 安全护栏触发标志
  };
}

export interface HistoryResponse {
  messages: Message[];
  has_more: boolean;
}

export interface GeneratingResponse {
  message: Message | null;
}

// ============ 上下文监控类型 ============

export interface TokenBreakdown {
  system_prompt: number;
  history: number;
  memories: number;
  compaction: number;
  tool_defs: number;
  tool_calls: number;
  tool_results: number;
  tool_count: number;
  user_input: number;
  total: number;
}

export type AlertLevel = 'normal' | 'warning' | 'critical';

export interface MemoryRecord {
  id: number;
  content: string;
  tag: string;
  updated_at: string;
  token_count: number;
}

export interface ToolProcess {
  name: string;
  task: string;
  input_tokens: number;
  output_tokens: number;
}

export interface MonitorData {
  model_name: string;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  breakdown: TokenBreakdown;
  max_context_tokens: number;
  alert: AlertLevel;
  pct: number;
  memory_types: { tag: string; tokens: number }[];
  memory_count: number;
  memory_records: MemoryRecord[];
  tool_processes: ToolProcess[];
}

export interface ContextStatus extends MonitorData {
  type: 'context_status';
  request_id?: string;
}

// ============ 错误类型 ============

export interface ApiError {
  code: string;
  message: string;
  retry_after?: number;
  remaining_seconds?: number;
}
