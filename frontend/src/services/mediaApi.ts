/**
 * 媒体文件 API 服务
 *
 * 参考: specs/008-multimodal-minicpm/contracts/media-upload.yaml
 *
 * 使用 XMLHttpRequest 实现上传进度监控
 * 参考: specs/008-multimodal-minicpm/research.md#8 前端媒体上传方案
 */

import apiClient, { get, post } from './api';
import {
  MediaUploadResponse,
  InferenceCancelResponse,
  UploadProgress,
} from '@/types/media';

// API 基础路径
const MEDIA_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1';

/**
 * 上传媒体文件（带进度回调）
 *
 * 使用 XMLHttpRequest 实现上传进度监控
 * fetch API 不支持上传进度事件
 *
 * @param file 文件对象
 * @param onProgress 进度回调
 * @returns 上传响应
 */
export async function uploadMedia(
  file: File,
  onProgress?: (progress: UploadProgress) => void
): Promise<MediaUploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append('file', file);

    // 上传进度监听
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        const percent = Math.round((e.loaded / e.total) * 100);
        onProgress({
          percent,
          stage: 'uploading',
          status: `上传中 ${percent}%`,
        });
      }
    };

    // 上传完成
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const response = JSON.parse(xhr.responseText) as MediaUploadResponse;
          // 转换 snake_case 到 camelCase
          resolve(transformResponse(response));
        } catch {
          reject(new Error('解析响应失败'));
        }
      } else {
        try {
          const errorResponse = JSON.parse(xhr.responseText);
          reject(new Error(errorResponse.message || `上传失败: ${xhr.status}`));
        } catch {
          reject(new Error(`上传失败: ${xhr.status}`));
        }
      }
    };

    // 上传错误
    xhr.onerror = () => {
      reject(new Error('网络错误，上传失败'));
    };

    // 上传超时
    xhr.ontimeout = () => {
      reject(new Error('上传超时'));
    };

    // 配置请求
    xhr.open('POST', `${MEDIA_BASE_URL}/chat/media/upload/`);
    xhr.withCredentials = true; // 携带 Cookie
    xhr.timeout = 120000; // 2分钟超时

    // 发送请求
    xhr.send(formData);
  });
}

/**
 * 获取媒体文件 URL
 *
 * @param attachmentUuid 附件 UUID
 * @returns 媒体文件 URL
 */
export function getMediaUrl(attachmentUuid: string): string {
  return `${MEDIA_BASE_URL}/chat/media/${attachmentUuid}/`;
}

/**
 * 下载媒体文件
 *
 * @param attachmentUuid 附件 UUID
 * @returns 文件 Blob
 */
export async function downloadMedia(attachmentUuid: string): Promise<Blob> {
  const response = await apiClient.get(getMediaUrl(attachmentUuid), {
    responseType: 'blob',
  });
  return response.data;
}

/**
 * 取消推理任务
 *
 * @param requestId 请求 ID（可选）
 * @returns 取消响应
 */
export async function cancelInference(
  requestId?: string
): Promise<InferenceCancelResponse> {
  const data = requestId ? { request_id: requestId } : {};
  return post<InferenceCancelResponse['data']>('/chat/inference/cancel/', data) as unknown as Promise<InferenceCancelResponse>;
}

// ============ 文档解析 API (T043a) ============

/**
 * 查询文档解析任务状态（REST 轮询降级用）
 *
 * 复用已有后端接口: GET /api/v1/chat/documents/tasks/<task_id>/
 */
export interface DocParseStatusResponse {
  code: string;
  message: string;
  data: {
    status: string;
    progress?: { current: number; total: number };
  };
}

export async function getDocParseStatus(
  taskId: string
): Promise<DocParseStatusResponse> {
  return get<DocParseStatusResponse['data']>(
    `/chat/documents/tasks/${taskId}/`
  ) as unknown as Promise<DocParseStatusResponse>;
}

/**
 * 文档解析任务响应
 */
export interface DocParseResponse {
  code: string;
  message: string;
  data: {
    task_id: string;
    status: string;
  };
}

/**
 * 文档解析结果响应
 */
export interface DocParseResultResponse {
  code: string;
  message: string;
  data: {
    content: string;
    format: string;
  };
}

/**
 * 创建文档解析任务
 *
 * @param attachmentUuid 附件 UUID
 * @param pages 页码范围（可选）
 * @returns 解析任务响应
 */
export async function createDocParseTask(
  attachmentUuid: string,
  pages?: string
): Promise<DocParseResponse> {
  const data: Record<string, string> = { attachment_uuid: attachmentUuid };
  if (pages) data.pages = pages;
  return post<DocParseResponse['data']>('/chat/documents/parse/', data) as unknown as Promise<DocParseResponse>;
}

/**
 * 获取文档解析结果
 *
 * @param taskId 任务 ID
 * @param format 结果格式
 * @returns 解析结果
 */
export async function getDocParseResult(
  taskId: string,
  format: 'markdown' | 'json' = 'markdown'
): Promise<DocParseResultResponse> {
  return get<DocParseResultResponse['data']>(
    `/chat/documents/tasks/${taskId}/result/`,
    { format }
  ) as unknown as Promise<DocParseResultResponse>;
}

/**
 * 转换响应（snake_case -> camelCase）
 */
function transformResponse(response: MediaUploadResponse): MediaUploadResponse {
  return {
    code: response.code,
    message: response.message,
    data: {
      attachment_uuid: response.data.attachment_uuid,
      media_type: response.data.media_type,
      mime_type: response.data.mime_type,
      file_name: response.data.file_name,
      file_size: response.data.file_size,
      width: response.data.width,
      height: response.data.height,
      duration_seconds: response.data.duration_seconds,
      expires_at: response.data.expires_at,
      is_expired: response.data.is_expired,
    },
  };
}

const mediaApi = {
  uploadMedia,
  getMediaUrl,
  downloadMedia,
  cancelInference,
};
export default mediaApi;
