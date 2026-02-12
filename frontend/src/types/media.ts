/**
 * 媒体相关类型定义
 *
 * 参考: specs/008-multimodal-minicpm/contracts/media-upload.yaml
 */

/**
 * 媒体类型枚举
 */
export type MediaType = 'image' | 'video' | 'audio' | 'document';

/**
 * 媒体附件基础信息
 */
export interface MediaAttachment {
  /** 附件 UUID */
  attachment_uuid: string;
  /** 媒体类型 */
  media_type: MediaType;
  /** MIME 类型 */
  mime_type: string;
  /** 原始文件名 */
  file_name: string;
  /** 文件大小（字节） */
  file_size: number;
  /** 宽度（像素，仅图片/视频） */
  width?: number;
  /** 高度（像素，仅图片/视频） */
  height?: number;
  /** 时长（秒，仅视频/音频） */
  duration_seconds?: number;
  /** 过期时间 */
  expires_at: string;
  /** 原始文件是否已过期 */
  is_expired?: boolean;
}

/**
 * 媒体上传响应
 */
export interface MediaUploadResponse {
  code: string;
  message: string;
  data: MediaAttachment;
}

/**
 * 上传进度状态
 */
export interface UploadProgress {
  /** 上传进度百分比 (0-100) */
  percent: number;
  /** 当前阶段: uploading | processing */
  stage: 'uploading' | 'processing';
  /** 状态描述 */
  status: string;
}

/**
 * 上传任务状态
 */
export interface UploadTask {
  /** 任务 ID（临时 ID，上传完成后替换为 attachment_uuid） */
  id: string;
  /** 本地文件对象 */
  file: File;
  /** 本地预览 URL（ObjectURL） */
  previewUrl: string;
  /** 上传进度 */
  progress: UploadProgress;
  /** 上传状态 */
  status: 'pending' | 'uploading' | 'processing' | 'completed' | 'failed';
  /** 错误信息 */
  error?: string;
  /** 上传成功后的附件信息 */
  attachment?: MediaAttachment;
}

/**
 * 推理取消响应
 */
export interface InferenceCancelResponse {
  code: string;
  message: string;
  data: {
    /** 是否成功取消 */
    cancelled: boolean;
    /** 被取消的请求 ID */
    request_id?: string;
  };
}

/**
 * 媒体文件限制
 */
export const MEDIA_LIMITS = {
  /** 图片最大大小（字节） */
  MAX_IMAGE_SIZE: 10 * 1024 * 1024, // 10MB
  /** 视频最大大小（字节） */
  MAX_VIDEO_SIZE: 50 * 1024 * 1024, // 50MB
  /** 音频最大大小（字节） */
  MAX_AUDIO_SIZE: 10 * 1024 * 1024, // 10MB
  /** 最大时长（秒） */
  MAX_DURATION_SECONDS: 60,
  /** 单次最多附件数 */
  MAX_ATTACHMENTS: 5,
  /** 支持的图片格式 */
  SUPPORTED_IMAGE_TYPES: ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
  /** 支持的视频格式 */
  SUPPORTED_VIDEO_TYPES: ['video/mp4', 'video/quicktime', 'video/webm'],
  /** 支持的音频格式 */
  SUPPORTED_AUDIO_TYPES: ['audio/webm', 'audio/wav', 'audio/mpeg'],
  /** 支持的文档格式 */
  SUPPORTED_DOCUMENT_TYPES: [
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  ],
  /** 文档最大大小（字节） */
  MAX_DOCUMENT_SIZE: 10 * 1024 * 1024, // 10MB
} as const;

/**
 * 错误码
 */
export const MEDIA_ERROR_CODES = {
  INVALID_FILE_TYPE: 'INVALID_FILE_TYPE',
  FILE_TOO_LARGE: 'FILE_TOO_LARGE',
  DURATION_TOO_LONG: 'DURATION_TOO_LONG',
  TOO_MANY_ATTACHMENTS: 'TOO_MANY_ATTACHMENTS',
  ATTACHMENT_NOT_FOUND: 'ATTACHMENT_NOT_FOUND',
  ATTACHMENT_EXPIRED: 'ATTACHMENT_EXPIRED',
  ATTACHMENT_ACCESS_DENIED: 'ATTACHMENT_ACCESS_DENIED',
  INFERENCE_IN_PROGRESS: 'INFERENCE_IN_PROGRESS',
  NO_ACTIVE_INFERENCE: 'NO_ACTIVE_INFERENCE',
} as const;

/**
 * 根据 MIME 类型获取媒体类型
 */
export function getMediaTypeFromMime(mimeType: string): MediaType | null {
  if (MEDIA_LIMITS.SUPPORTED_IMAGE_TYPES.includes(mimeType as never)) {
    return 'image';
  }
  if (MEDIA_LIMITS.SUPPORTED_VIDEO_TYPES.includes(mimeType as never)) {
    return 'video';
  }
  if (MEDIA_LIMITS.SUPPORTED_AUDIO_TYPES.includes(mimeType as never)) {
    return 'audio';
  }
  if (MEDIA_LIMITS.SUPPORTED_DOCUMENT_TYPES.includes(mimeType as never)) {
    return 'document';
  }
  return null;
}

/**
 * 获取文件大小限制
 */
export function getFileSizeLimit(mediaType: MediaType): number {
  switch (mediaType) {
    case 'image':
      return MEDIA_LIMITS.MAX_IMAGE_SIZE;
    case 'video':
      return MEDIA_LIMITS.MAX_VIDEO_SIZE;
    case 'audio':
      return MEDIA_LIMITS.MAX_AUDIO_SIZE;
    case 'document':
      return MEDIA_LIMITS.MAX_DOCUMENT_SIZE;
  }
}

/**
 * 格式化文件大小
 */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 格式化时长
 */
export function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}
