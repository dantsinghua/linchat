/**
 * modelStore 测试 (T031)
 *
 * 覆盖:
 * - setModels 状态管理
 * - updateModelInList 更新列表中的模型
 * - 加载状态切换
 * - 错误状态管理
 * - reset 重置
 */
import { act, renderHook } from '@testing-library/react';

import { useModelStore } from '@/stores/modelStore';
import type { ModelConfig } from '@/types/model';

const mockModel: ModelConfig = {
  id: 1,
  type: 'language',
  name: 'test-model',
  url: 'https://api.example.com/v1',
  apiKey: 'test****5678',
  maxContextWindow: 65536,
  maxInputTokens: 32768,
  maxOutputTokens: 8192,
  temperature: null,
  topP: null,
  frequencyPenalty: null,
  presencePenalty: null,
  embeddingDimensions: null,
  isActive: true,
  effectiveContextWindow: 58982,
  createdAt: '2026-01-29T00:00:00Z',
  updatedAt: '2026-01-29T00:00:00Z',
};

describe('useModelStore', () => {
  beforeEach(() => {
    // 重置 store
    const { result } = renderHook(() => useModelStore());
    act(() => {
      result.current.reset();
    });
  });

  it('初始状态应为空列表', () => {
    const { result } = renderHook(() => useModelStore());

    expect(result.current.models).toEqual([]);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('setModels 应设置模型列表', () => {
    const { result } = renderHook(() => useModelStore());

    act(() => {
      result.current.setModels([mockModel]);
    });

    expect(result.current.models).toEqual([mockModel]);
  });

  it('updateModelInList 应更新指定模型', () => {
    const { result } = renderHook(() => useModelStore());
    const updatedModel = { ...mockModel, name: 'updated-name' };

    act(() => {
      result.current.setModels([mockModel]);
    });
    act(() => {
      result.current.updateModelInList(1, updatedModel);
    });

    expect(result.current.models[0].name).toBe('updated-name');
  });

  it('setIsLoading 应切换加载状态', () => {
    const { result } = renderHook(() => useModelStore());

    act(() => {
      result.current.setIsLoading(true);
    });
    expect(result.current.isLoading).toBe(true);

    act(() => {
      result.current.setIsLoading(false);
    });
    expect(result.current.isLoading).toBe(false);
  });

  it('setError 应设置错误信息', () => {
    const { result } = renderHook(() => useModelStore());

    act(() => {
      result.current.setError('加载失败');
    });
    expect(result.current.error).toBe('加载失败');
  });

  it('reset 应恢复初始状态', () => {
    const { result } = renderHook(() => useModelStore());

    act(() => {
      result.current.setModels([mockModel]);
      result.current.setIsLoading(true);
      result.current.setError('error');
    });
    act(() => {
      result.current.reset();
    });

    expect(result.current.models).toEqual([]);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });
});
