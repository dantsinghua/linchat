/**
 * 模型配置状态管理
 *
 * 使用 Zustand 管理模型配置相关状态
 * 参考: specs/003-model-config/spec.md
 */
import { create } from 'zustand';

import type { ModelConfig } from '@/types/model';

interface ModelState {
  // 模型配置列表
  models: ModelConfig[];
  // 是否正在加载
  isLoading: boolean;
  // 错误信息
  error: string | null;

  // Actions
  setModels: (models: ModelConfig[]) => void;
  updateModelInList: (id: number, updated: ModelConfig) => void;
  setIsLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

const initialState = {
  models: [],
  isLoading: false,
  error: null,
};

export const useModelStore = create<ModelState>((set) => ({
  ...initialState,

  setModels: (models) => set({ models }),

  updateModelInList: (id, updated) =>
    set((state) => ({
      models: state.models.map((m) => (m.id === id ? updated : m)),
    })),

  setIsLoading: (isLoading) => set({ isLoading }),

  setError: (error) => set({ error }),

  reset: () => set(initialState),
}));
