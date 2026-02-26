/**
 * 模型配置卡片组件
 *
 * 以卡片形式展示单个模型的配置详情
 * 参考: specs/003-model-config/spec.md US1
 */
'use client';

import { memo } from 'react';

import type { ModelConfig } from '@/types/model';

interface ModelConfigCardProps {
  model: ModelConfig;
  onEdit?: (model: ModelConfig) => void;
}

/** 格式化选填参数：NULL 显示"未设置"，数字显示原值 */
function formatOptional(value: number | null): string {
  return value === null ? '未设置' : String(value);
}

/** 模型类型标签 */
function TypeBadge({ type }: { type: string }) {
  const labels: Record<string, { text: string; cls: string }> = {
    tool: { text: '工具模型', cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' },
    multimodal: { text: '多模态模型', cls: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' },
    embedding: { text: '向量模型', cls: 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' },
  };
  const { text, cls } = labels[type] || labels.embedding!;
  return (
    <span
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-3 py-1 text-xs font-medium ${cls}`}
    >
      {text}
    </span>
  );
}

/** 配置项展示行 */
function ConfigRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between py-2">
      <span className="text-sm text-gray-500 dark:text-gray-400">{label}</span>
      <span className="ml-4 text-sm font-medium text-gray-900 dark:text-white text-right break-all max-w-[60%]">
        {value}
      </span>
    </div>
  );
}

export const ModelConfigCard = memo(function ModelConfigCard({
  model,
  onEdit,
}: ModelConfigCardProps) {
  const isEmbedding = model.type === 'embedding';

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      {/* 卡片头部 */}
      <div className="mb-4 flex items-center gap-3">
        <TypeBadge type={model.type} />
        <h3
          className="min-w-0 flex-1 truncate text-lg font-semibold text-gray-900 dark:text-white"
          title={model.name}
        >
          {model.name}
        </h3>
        <div className="flex shrink-0 items-center gap-2">
          <span
            className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs ${
              model.isActive
                ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
                : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                model.isActive ? 'bg-green-500' : 'bg-gray-400'
              }`}
            />
            {model.isActive ? '已激活' : '未激活'}
          </span>
          {onEdit && (
            <button
              onClick={() => onEdit(model)}
              className="shrink-0 whitespace-nowrap rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
            >
              编辑
            </button>
          )}
        </div>
      </div>

      {/* 配置详情 */}
      <div className="divide-y divide-gray-100 dark:divide-gray-700">
        {/* 基础配置 */}
        <div className="pb-3">
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-gray-400">
            基础配置
          </h4>
          <ConfigRow label="API 地址" value={model.url} />
          <ConfigRow label="API Key" value={model.apiKey} />
        </div>

        {/* 容量参数 */}
        <div className="py-3">
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-gray-400">
            容量参数
          </h4>
          <ConfigRow
            label="最大上下文窗口"
            value={model.maxContextWindow.toLocaleString()}
          />
          <ConfigRow
            label="最大输入 Token"
            value={model.maxInputTokens.toLocaleString()}
          />
          <ConfigRow
            label="最大输出 Token"
            value={model.maxOutputTokens.toLocaleString()}
          />
          <ConfigRow
            label="有效上下文窗口"
            value={model.effectiveContextWindow.toLocaleString()}
          />
        </div>

        {/* 生成参数（选填） */}
        <div className="pt-3">
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-gray-400">
            生成参数
          </h4>
          <ConfigRow label="Temperature" value={formatOptional(model.temperature)} />
          <ConfigRow label="Top P" value={formatOptional(model.topP)} />
          <ConfigRow
            label="Frequency Penalty"
            value={formatOptional(model.frequencyPenalty)}
          />
          <ConfigRow
            label="Presence Penalty"
            value={formatOptional(model.presencePenalty)}
          />
          {isEmbedding && (
            <ConfigRow
              label="Embedding 维度"
              value={formatOptional(model.embeddingDimensions)}
            />
          )}
        </div>
      </div>

      {/* 时间信息 */}
      <div className="mt-4 flex items-center justify-between text-xs text-gray-400">
        <span>创建: {new Date(model.createdAt).toLocaleString('zh-CN')}</span>
        <span>更新: {new Date(model.updatedAt).toLocaleString('zh-CN')}</span>
      </div>
    </div>
  );
});
