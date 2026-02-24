/**
 * 媒体上传组件
 *
 * T027: 支持多文件选择（≤5）、批量预览、逐个上传进度、
 * 前端格式和大小双重校验、WebM 类型分类、音频时长校验
 *
 * 参考: specs/008-multimodal-minicpm/tasks.md T027
 */
'use client';

import {
  forwardRef,
  memo,
  useCallback,
  useImperativeHandle,
  useRef,
} from 'react';
import { toast } from 'sonner';

import { uploadMedia } from '@/services/mediaApi';
import { useUploadStore, createUploadTask } from '@/stores/uploadStore';
import {
  MEDIA_LIMITS,
  getMediaTypeFromMime,
  getFileSizeLimit,
  formatFileSize,
} from '@/types/media';
import type { UploadTask } from '@/types/media';

/** 所有支持的 MIME 类型（用于 <input accept> ） */
const ACCEPT_TYPES = [
  ...MEDIA_LIMITS.SUPPORTED_IMAGE_TYPES,
  ...MEDIA_LIMITS.SUPPORTED_VIDEO_TYPES,
  ...MEDIA_LIMITS.SUPPORTED_AUDIO_TYPES,
  ...MEDIA_LIMITS.SUPPORTED_DOCUMENT_TYPES,
].join(',');

/** 格式提示文本 */
const FORMAT_HINT =
  '支持: JPG, PNG, GIF, WebP, MP4, MOV, WebM, WAV, MP3, PDF';

export interface MediaUploaderRef {
  openFilePicker: () => void;
}

interface MediaUploaderProps {
  disabled?: boolean;
}

/**
 * 通过 HTML5 Audio 元素检测音频时长
 */
function checkAudioDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const audio = new Audio();
    const url = URL.createObjectURL(file);
    audio.onloadedmetadata = () => {
      resolve(audio.duration);
      URL.revokeObjectURL(url);
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('无法读取音频时长'));
    };
    audio.src = url;
  });
}

/**
 * 通过 HTML5 Video 元素检测视频时长 (T049)
 */
function checkVideoDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    const url = URL.createObjectURL(file);
    video.onloadedmetadata = () => {
      resolve(video.duration);
      URL.revokeObjectURL(url);
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('无法读取视频时长'));
    };
    video.src = url;
  });
}

export const MediaUploader = memo(
  forwardRef<MediaUploaderRef, MediaUploaderProps>(function MediaUploader(
    { disabled = false },
    ref
  ) {
    const fileInputRef = useRef<HTMLInputElement>(null);
    const store = useUploadStore();

    useImperativeHandle(ref, () => ({
      openFilePicker: () => fileInputRef.current?.click(),
    }));

    /** 上传单个文件 */
    const startUpload = useCallback(
      async (task: UploadTask) => {
        store.updateTaskStatus(task.id, 'uploading');
        try {
          const response = await uploadMedia(task.file, (progress) => {
            store.updateTaskProgress(task.id, progress);
          });
          store.completeTask(task.id, response.data);
        } catch (error) {
          store.updateTaskStatus(
            task.id,
            'failed',
            (error as Error).message
          );
        }
      },
      [store]
    );

    /** 处理文件选择 */
    const handleFileSelect = useCallback(
      async (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(e.target.files || []);
        e.target.value = '';
        if (files.length === 0) return;

        const currentCount = store.tasks.length;
        if (currentCount + files.length > MEDIA_LIMITS.MAX_ATTACHMENTS) {
          toast.error(
            `最多上传 ${MEDIA_LIMITS.MAX_ATTACHMENTS} 个文件`
          );
          return;
        }

        for (const file of files) {
          // 格式校验
          const mediaType = getMediaTypeFromMime(file.type);
          if (!mediaType) {
            toast.error(`不支持的文件格式: ${file.name}`, {
              description: FORMAT_HINT,
            });
            continue;
          }

          // 大小校验
          const sizeLimit = getFileSizeLimit(mediaType);
          if (file.size > sizeLimit) {
            toast.error(`${file.name} 超过大小限制`, {
              description: `${mediaType} 类型最大 ${formatFileSize(sizeLimit)}`,
            });
            continue;
          }

          // 音频时长校验（1-60 秒）
          if (mediaType === 'audio') {
            try {
              const duration = await checkAudioDuration(file);
              if (duration < 1) {
                toast.error(
                  `${file.name}: 音频时长过短（最短 1 秒）`
                );
                continue;
              }
              if (duration > MEDIA_LIMITS.MAX_DURATION_SECONDS) {
                toast.error(
                  `${file.name}: 音频时长不能超过 ${MEDIA_LIMITS.MAX_DURATION_SECONDS} 秒`
                );
                continue;
              }
            } catch {
              toast.error(`${file.name}: 无法读取音频时长`);
              continue;
            }
          }

          // 视频时长校验（≤60 秒）(T049)
          if (mediaType === 'video') {
            try {
              const duration = await checkVideoDuration(file);
              if (duration > MEDIA_LIMITS.MAX_DURATION_SECONDS) {
                toast.error(
                  `${file.name}: 视频时长不能超过 ${MEDIA_LIMITS.MAX_DURATION_SECONDS} 秒`
                );
                continue;
              }
            } catch {
              toast.error(`${file.name}: 无法读取视频时长`);
              continue;
            }
          }

          const task = createUploadTask(file);
          store.addTask(task);
          startUpload(task);
        }
      },
      [store, startUpload]
    );

    /** 移除上传任务 */
    const handleRemove = useCallback(
      (taskId: string) => {
        store.removeTask(taskId);
      },
      [store]
    );

    return (
      <>
        {/* 隐藏的文件选择器 */}
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT_TYPES}
          multiple
          className="hidden"
          onChange={handleFileSelect}
          disabled={disabled}
        />

        {/* 上传预览网格 */}
        {store.tasks.length > 0 && (
          <div className="flex flex-wrap gap-2 pb-2">
            {store.tasks.map((task) => (
              <UploadTile
                key={task.id}
                task={task}
                onRemove={handleRemove}
              />
            ))}
          </div>
        )}
      </>
    );
  })
);

/** 单个上传预览卡片 */
interface UploadTileProps {
  task: UploadTask;
  onRemove: (taskId: string) => void;
}

const UploadTile = memo(function UploadTile({
  task,
  onRemove,
}: UploadTileProps) {
  const isImage = task.file.type.startsWith('image/');
  const isVideo = task.file.type.startsWith('video/');
  const isFailed = task.status === 'failed';
  const isCompleted = task.status === 'completed';
  const isUploading =
    task.status === 'uploading' || task.status === 'pending';

  return (
    <div
      className={`group relative h-20 w-20 shrink-0 overflow-hidden rounded-lg border ${
        isFailed
          ? 'border-red-300'
          : isCompleted
            ? 'border-green-300'
            : 'border-gray-200'
      }`}
      title={task.file.name}
    >
      {/* 预览 */}
      {isImage && (
        /* eslint-disable-next-line @next/next/no-img-element */
        <img
          src={task.previewUrl}
          alt={task.file.name}
          className="h-full w-full object-cover"
        />
      )}
      {isVideo && (
        <video
          src={task.previewUrl}
          className="h-full w-full object-cover"
          muted
        />
      )}
      {!isImage && !isVideo && (
        <div className="flex h-full w-full flex-col items-center justify-center bg-gray-50 px-1">
          <svg
            className="h-6 w-6 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            {task.file.type.startsWith('audio/') ? (
              <>
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2z"
                />
              </>
            ) : (
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
              />
            )}
          </svg>
          <span className="mt-0.5 max-w-full truncate text-[10px] text-gray-400">
            {task.file.name.split('.').pop()?.toUpperCase()}
          </span>
        </div>
      )}

      {/* 上传进度覆盖层 */}
      {isUploading && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/40">
          <div className="text-xs font-medium text-white">
            {task.progress.percent}%
          </div>
        </div>
      )}

      {/* 失败覆盖层 */}
      {isFailed && (
        <div className="absolute inset-0 flex items-center justify-center bg-red-500/60">
          <span className="text-[10px] text-white">失败</span>
        </div>
      )}

      {/* 移除按钮 */}
      <button
        onClick={() => onRemove(task.id)}
        className="absolute right-0.5 top-0.5 hidden h-5 w-5 items-center justify-center rounded-full bg-black/60 text-white group-hover:flex"
        title="移除"
      >
        <svg
          className="h-3 w-3"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>

      {/* 完成标记 */}
      {isCompleted && (
        <div className="absolute bottom-0.5 right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-green-500">
          <svg
            className="h-3 w-3 text-white"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={3}
              d="M5 13l4 4L19 7"
            />
          </svg>
        </div>
      )}
    </div>
  );
});
