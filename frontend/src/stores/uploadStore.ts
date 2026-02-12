/**
 * 上传状态管理
 *
 * 使用 Zustand 管理媒体上传相关状态
 * 参考: specs/008-multimodal-minicpm/research.md#8 前端媒体上传方案
 */
import { create } from 'zustand';

import type { UploadTask, UploadProgress, MediaAttachment } from '@/types/media';

interface UploadState {
  // 上传任务列表
  tasks: UploadTask[];
  // 已完成的附件 UUID 列表（用于发送消息）
  completedAttachments: string[];

  // Actions
  addTask: (task: UploadTask) => void;
  removeTask: (taskId: string) => void;
  updateTaskProgress: (taskId: string, progress: UploadProgress) => void;
  updateTaskStatus: (
    taskId: string,
    status: UploadTask['status'],
    error?: string
  ) => void;
  completeTask: (taskId: string, attachment: MediaAttachment) => void;
  clearTasks: () => void;
  getCompletedUuids: () => string[];
  reset: () => void;
}

const initialState = {
  tasks: [] as UploadTask[],
  completedAttachments: [] as string[],
};

export const useUploadStore = create<UploadState>((set, get) => ({
  ...initialState,

  addTask: (task: UploadTask) => {
    set((state) => ({
      tasks: [...state.tasks, task],
    }));
  },

  removeTask: (taskId: string) => {
    set((state) => ({
      tasks: state.tasks.filter((t) => t.id !== taskId),
      completedAttachments: state.completedAttachments.filter(
        (uuid) =>
          !state.tasks.find((t) => t.id === taskId && t.attachment?.attachment_uuid === uuid)
      ),
    }));
  },

  updateTaskProgress: (taskId: string, progress: UploadProgress) => {
    set((state) => ({
      tasks: state.tasks.map((task) =>
        task.id === taskId ? { ...task, progress } : task
      ),
    }));
  },

  updateTaskStatus: (
    taskId: string,
    status: UploadTask['status'],
    error?: string
  ) => {
    set((state) => ({
      tasks: state.tasks.map((task) =>
        task.id === taskId ? { ...task, status, error } : task
      ),
    }));
  },

  completeTask: (taskId: string, attachment: MediaAttachment) => {
    set((state) => ({
      tasks: state.tasks.map((task) =>
        task.id === taskId
          ? {
              ...task,
              status: 'completed' as const,
              attachment,
              progress: { percent: 100, stage: 'processing' as const, status: '完成' },
            }
          : task
      ),
      completedAttachments: [
        ...state.completedAttachments,
        attachment.attachment_uuid,
      ],
    }));
  },

  clearTasks: () => {
    // 释放所有预览 URL
    const { tasks } = get();
    tasks.forEach((task) => {
      if (task.previewUrl) {
        URL.revokeObjectURL(task.previewUrl);
      }
    });
    set({ tasks: [], completedAttachments: [] });
  },

  getCompletedUuids: () => {
    return get().completedAttachments;
  },

  reset: () => {
    // 释放所有预览 URL
    const { tasks } = get();
    tasks.forEach((task) => {
      if (task.previewUrl) {
        URL.revokeObjectURL(task.previewUrl);
      }
    });
    set(initialState);
  },
}));

/**
 * 创建上传任务
 *
 * @param file 文件对象
 * @returns 上传任务
 */
export function createUploadTask(file: File): UploadTask {
  return {
    id: `upload-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    file,
    previewUrl: URL.createObjectURL(file),
    progress: { percent: 0, stage: 'uploading', status: '准备上传' },
    status: 'pending',
  };
}

/**
 * 获取任务的显示状态文本
 *
 * @param task 上传任务
 * @returns 状态文本
 */
export function getTaskStatusText(task: UploadTask): string {
  switch (task.status) {
    case 'pending':
      return '等待上传';
    case 'uploading':
      return task.progress.status;
    case 'processing':
      return '处理中...';
    case 'completed':
      return '上传完成';
    case 'failed':
      return task.error || '上传失败';
    default:
      return '';
  }
}

export default useUploadStore;
