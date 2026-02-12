/**
 * 媒体预览组件
 *
 * T028: 按媒体类型显示占位图或加载原始文件
 * - 图片: <img> 直接渲染
 * - 视频: <video> 标签播放
 * - 音频: <audio> 控件播放
 * - 文档: SVG 占位图
 * - 已过期: 显示"文件已过期"提示（HTTP 410）
 *
 * 参考: specs/008-multimodal-minicpm/tasks.md T028
 */
'use client';

import { memo, useCallback, useState } from 'react';

import { getMediaUrl } from '@/services/mediaApi';
import type { MediaAttachment } from '@/types/media';

interface MediaPreviewProps {
  attachment: MediaAttachment;
  /** 本地预览 URL（刚上传时使用，优先于远程 URL） */
  localPreviewUrl?: string;
}

export const MediaPreview = memo(function MediaPreview({
  attachment,
  localPreviewUrl,
}: MediaPreviewProps) {
  const [isExpired, setIsExpired] = useState(false);
  const [loadError, setLoadError] = useState(false);

  const mediaUrl = localPreviewUrl || getMediaUrl(attachment.attachment_uuid);

  const handleError = useCallback(() => {
    if (attachment.is_expired) {
      setIsExpired(true);
    } else {
      setLoadError(true);
    }
  }, [attachment.is_expired]);

  // 已过期
  if (isExpired || attachment.is_expired) {
    return (
      <div className="flex h-20 w-20 items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
        <span className="text-center text-[10px] text-gray-400">
          文件已过期
        </span>
      </div>
    );
  }

  // 加载失败
  if (loadError) {
    return (
      <div className="flex h-20 w-20 items-center justify-center rounded-lg border border-gray-200 bg-gray-50">
        <span className="text-center text-[10px] text-gray-400">
          加载失败
        </span>
      </div>
    );
  }

  // 图片
  if (attachment.media_type === 'image') {
    return (
      <img
        src={mediaUrl}
        alt={attachment.file_name}
        className="max-h-60 max-w-full rounded-lg object-contain"
        onError={handleError}
        loading="lazy"
      />
    );
  }

  // 视频
  if (attachment.media_type === 'video') {
    return (
      <video
        src={mediaUrl}
        className="max-h-60 max-w-full rounded-lg"
        controls
        preload="metadata"
        onError={handleError}
      />
    );
  }

  // 音频
  if (attachment.media_type === 'audio') {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-600 dark:bg-gray-700">
        <svg
          className="h-5 w-5 shrink-0 text-gray-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2z"
          />
        </svg>
        <audio
          src={mediaUrl}
          controls
          preload="metadata"
          onError={handleError}
          className="h-8 max-w-[200px]"
        />
      </div>
    );
  }

  // 文档（T043: 按类型显示不同图标）
  if (attachment.media_type === 'document') {
    const isPdf = attachment.mime_type === 'application/pdf';
    const iconColor = isPdf ? 'text-red-500' : 'text-blue-500';
    const labelText = isPdf ? 'PDF' : 'DOCX';

    return (
      <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-600 dark:bg-gray-700">
        <div className="relative shrink-0">
          <svg
            className={`h-6 w-6 ${iconColor}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
            />
          </svg>
          <span className={`absolute -bottom-1 -right-1 rounded px-0.5 text-[8px] font-bold ${iconColor}`}>
            {labelText}
          </span>
        </div>
        <span className="max-w-[160px] truncate text-sm text-gray-600 dark:text-gray-300">
          {attachment.file_name}
        </span>
      </div>
    );
  }

  return null;
});

/** 附件列表渲染组件（用于消息气泡） */
interface AttachmentListProps {
  attachments: MediaAttachment[];
}

export const AttachmentList = memo(function AttachmentList({
  attachments,
}: AttachmentListProps) {
  if (!attachments || attachments.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {attachments.map((attachment) => (
        <MediaPreview
          key={attachment.attachment_uuid}
          attachment={attachment}
        />
      ))}
    </div>
  );
});
