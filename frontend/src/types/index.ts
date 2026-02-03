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
  userId: number;
  username: string;
}

export interface LoginRequest {
  username: string;
  password: string;
  captchaId: string;
  captchaCode: string;
}

export interface CaptchaResponse {
  captchaId: string;
  captchaImage: string; // base64
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
}

export interface ChatRequest {
  content: string;
}

/**
 * SSE 流式响应事件
 * 参考: process-model.md#三、消息发送与流式响应流程
 */
export interface ChatStreamEvent {
  type: 'content' | 'done' | 'error' | 'interrupted' | 'context_compacting' | 'context_compacted';
  content: string;
  message_id?: number;
  request_id?: string; // 首个 chunk 返回，用于停止/继续生成
}

export interface HistoryResponse {
  messages: Message[];
  has_more: boolean;
}

export interface GeneratingResponse {
  message: Message | null;
}

// ============ 错误类型 ============

export interface ApiError {
  code: string;
  message: string;
  retryAfter?: number;
  remainingSeconds?: number;
}
