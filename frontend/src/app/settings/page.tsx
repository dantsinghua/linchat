/**
 * 模型配置设置页面
 *
 * 管理员查看和编辑模型配置
 * 参考: specs/003-model-config/spec.md US1, US2
 */
'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

import { ModelConfigCard } from '@/components/settings/ModelConfigCard';
import { ModelConfigForm } from '@/components/settings/ModelConfigForm';
import { useAuth } from '@/hooks/useAuth';
import { fetchModels } from '@/services/modelService';
import { useModelStore } from '@/stores/modelStore';
import type { ModelConfig } from '@/types/model';

export default function SettingsPage() {
  const router = useRouter();
  const { isAuthenticated, user } = useAuth();
  const { models, isLoading, error, setModels, updateModelInList, setIsLoading, setError } =
    useModelStore();
  const [authChecked, setAuthChecked] = useState(false);
  const [editingModel, setEditingModel] = useState<ModelConfig | null>(null);

  // 权限守卫：非管理员跳转 401
  useEffect(() => {
    if (isAuthenticated === null) return; // 认证状态加载中

    if (!isAuthenticated) {
      router.push('/401');
      return;
    }

    if (user && user.type !== 'admin') {
      router.push('/401');
      return;
    }

    if (user) {
      setAuthChecked(true);
    }
  }, [isAuthenticated, user, router]);

  // 加载模型配置
  useEffect(() => {
    if (!authChecked) return;

    const loadModels = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await fetchModels();
        setModels(data);
      } catch (err: unknown) {
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 403) {
          router.push('/401');
          return;
        }
        setError('加载模型配置失败，请稍后重试');
      } finally {
        setIsLoading(false);
      }
    };

    loadModels();
  }, [authChecked, setModels, setIsLoading, setError, router]);

  // 处理编辑
  const handleEdit = useCallback((model: ModelConfig) => {
    setEditingModel(model);
  }, []);

  // 保存编辑
  const handleSave = useCallback(
    (updated: ModelConfig) => {
      updateModelInList(updated.id, updated);
      setEditingModel(null);
    },
    [updateModelInList]
  );

  // 取消编辑
  const handleCancelEdit = useCallback(() => {
    setEditingModel(null);
  }, []);

  // 返回聊天
  const handleBack = useCallback(() => {
    router.push('/chat');
  }, [router]);

  // 认证和权限检查中
  if (!authChecked) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="text-gray-500">加载中...</div>
      </div>
    );
  }

  // 按类型排序：language 在前，embedding 在后
  const sortedModels = [...models].sort((a, b) => {
    if (a.type === 'language' && b.type === 'embedding') return -1;
    if (a.type === 'embedding' && b.type === 'language') return 1;
    return 0;
  });

  return (
    <div className="flex h-screen flex-col bg-gray-50 dark:bg-gray-900">
      {/* 顶部导航 */}
      <header className="border-b bg-white px-6 py-4 dark:bg-gray-800 dark:border-gray-700">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={handleBack}
              className="flex items-center gap-1 text-sm text-gray-600 transition-colors hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
            >
              <svg
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M15 19l-7-7 7-7"
                />
              </svg>
              返回聊天
            </button>
            <div className="h-5 w-px bg-gray-300 dark:bg-gray-600" />
            <h1 className="text-xl font-semibold text-gray-800 dark:text-white">
              模型配置
            </h1>
          </div>

          {user && (
            <span className="text-sm text-gray-600 dark:text-gray-300">
              {user.username}
            </span>
          )}
        </div>
      </header>

      {/* 内容区域 */}
      <main className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-5xl">
          {/* 错误提示 */}
          {error && (
            <div className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
              {error}
            </div>
          )}

          {/* 加载状态 */}
          {isLoading && (
            <div className="flex items-center justify-center py-12">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary-500 border-t-transparent" />
            </div>
          )}

          {/* 模型配置卡片列表 */}
          {!isLoading && !error && (
            <div className="grid gap-6 lg:grid-cols-2">
              {sortedModels.map((model) =>
                editingModel?.id === model.id ? (
                  <ModelConfigForm
                    key={model.id}
                    model={model}
                    onSave={handleSave}
                    onCancel={handleCancelEdit}
                  />
                ) : (
                  <ModelConfigCard
                    key={model.id}
                    model={model}
                    onEdit={handleEdit}
                  />
                )
              )}
            </div>
          )}

          {/* 空状态 */}
          {!isLoading && !error && models.length === 0 && (
            <div className="py-12 text-center text-gray-500">
              暂无模型配置
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
