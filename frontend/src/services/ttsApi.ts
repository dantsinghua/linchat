/**
 * TTS 语音合成 API 服务
 *
 * 参考: specs/008-multimodal-minicpm/contracts/tts.yaml
 *
 * 使用 fetch API 直接请求，因为返回的是二进制音频流（audio/mpeg），
 * 不适合走 axios 的 JSON 响应拦截器。
 */

// API 基础路径
const TTS_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1';

/**
 * TTS 错误响应
 */
export interface TTSErrorResponse {
  code: string;
  message: string;
  data?: {
    retry_after?: number;
    estimated_wait_seconds?: number;
    gateway_error?: string;
  };
}

/**
 * TTS 错误类
 */
export class TTSError extends Error {
  code: string;
  statusCode: number;
  data?: TTSErrorResponse['data'];

  constructor(code: string, message: string, statusCode: number, data?: TTSErrorResponse['data']) {
    super(message);
    this.code = code;
    this.statusCode = statusCode;
    this.data = data;
  }
}

/**
 * 合成 TTS 语音
 *
 * @param messageUuid 消息 UUID
 * @param voice 语音类型（可选，默认 "default"）
 * @returns 音频 Blob（audio/mpeg）
 * @throws TTSError 合成失败时抛出
 */
export async function synthesizeTTS(
  messageUuid: string,
  voice: string = 'default'
): Promise<Blob> {
  const response = await fetch(`${TTS_BASE_URL}/chat/tts/`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      message_uuid: messageUuid,
      voice,
    }),
  });

  if (!response.ok) {
    // 尝试解析错误响应
    try {
      const errorData = (await response.json()) as TTSErrorResponse;
      throw new TTSError(
        errorData.code || 'TTS_ERROR',
        errorData.message || '语音合成失败',
        response.status,
        errorData.data
      );
    } catch (e) {
      if (e instanceof TTSError) throw e;
      throw new TTSError(
        'TTS_ERROR',
        '语音合成失败',
        response.status
      );
    }
  }

  return response.blob();
}
