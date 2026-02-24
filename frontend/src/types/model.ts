/**
 * 模型配置类型定义
 *
 * 参考: specs/003-model-config/contracts/api.yaml
 */

/** 模型类型 */
export type ModelType = 'tool' | 'multimodal' | 'embedding';

/** 模型配置（GET 响应） */
export interface ModelConfig {
  id: number;
  type: ModelType;
  name: string;
  url: string;
  apiKey: string; // 脱敏展示
  maxContextWindow: number;
  maxInputTokens: number;
  maxOutputTokens: number;
  temperature: number | null;
  topP: number | null;
  frequencyPenalty: number | null;
  presencePenalty: number | null;
  embeddingDimensions: number | null;
  isActive: boolean;
  effectiveContextWindow: number;
  createdAt: string;
  updatedAt: string;
}

/** 模型配置更新请求（PUT） */
export interface ModelUpdateRequest {
  name: string;
  url: string;
  api_key: string; // snake_case: 发送到后端
  max_context_window: number;
  max_input_tokens: number;
  max_output_tokens: number;
  temperature?: number | null;
  top_p?: number | null;
  frequency_penalty?: number | null;
  presence_penalty?: number | null;
  embedding_dimensions?: number | null;
}
