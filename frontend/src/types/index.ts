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

export interface Message {
  messageId: number;
  messageUuid: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  createdTime: string;
}

export interface ChatRequest {
  content: string;
}

export interface ChatStreamEvent {
  type: 'token' | 'done' | 'error';
  content?: string;
  messageId?: string;
  error?: string;
}

// ============ 错误类型 ============

export interface ApiError {
  code: string;
  message: string;
  retryAfter?: number;
  remainingSeconds?: number;
}
