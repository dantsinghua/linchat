/**
 * 模型配置编辑表单组件
 *
 * 参考: specs/003-model-config/spec.md US2, FR-004~FR-012
 */
'use client';

import { memo, useCallback, useState } from 'react';

import { updateModel } from '@/services/modelService';
import type { ModelConfig, ModelUpdateRequest } from '@/types/model';

interface ModelConfigFormProps {
  model: ModelConfig;
  onSave: (updated: ModelConfig) => void;
  onCancel: () => void;
}

interface FormErrors {
  [key: string]: string;
}

/** 表单数据字段 */
interface FormData {
  name: string;
  url: string;
  api_key: string;
  max_context_window: string;
  max_input_tokens: string;
  max_output_tokens: string;
  temperature: string;
  top_p: string;
  frequency_penalty: string;
  presence_penalty: string;
  embedding_dimensions: string;
}

/** 解析选填数值：空字符串→null，数字字符串→number */
function parseOptionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === '') return null;
  const num = Number(trimmed);
  if (isNaN(num)) return null;
  return num;
}

/** 前端范围校验 */
function validateForm(
  data: FormData,
  modelType: string
): FormErrors {
  const errors: FormErrors = {};

  // 必填字段
  if (!data.name?.trim()) errors.name = '模型名称不能为空';
  else if (data.name.trim().length > 100) errors.name = '模型名称不能超过 100 个字符';

  if (!data.url?.trim()) errors.url = 'API 地址不能为空';
  else if (data.url.trim().length > 500) errors.url = 'API 地址不能超过 500 个字符';

  if (!data.api_key?.trim()) errors.api_key = 'API Key 不能为空';
  else if (!data.api_key.includes('****') && data.api_key.trim().length < 12) {
    errors.api_key = 'API Key 至少需要 12 个字符';
  }

  // 容量参数：正整数
  const intFields: { key: keyof FormData; label: string }[] = [
    { key: 'max_context_window', label: '最大上下文窗口' },
    { key: 'max_input_tokens', label: '最大输入 Token' },
    { key: 'max_output_tokens', label: '最大输出 Token' },
  ];
  for (const { key, label } of intFields) {
    const val = Number(data[key]);
    if (!data[key] || isNaN(val) || val <= 0 || !Number.isInteger(val)) {
      errors[key] = `${label}必须为正整数`;
    }
  }

  // 选填数值范围校验
  const optionalRanges: {
    key: keyof FormData;
    label: string;
    min: number;
    max: number;
  }[] = [
    { key: 'temperature', label: 'Temperature', min: 0, max: 2 },
    { key: 'top_p', label: 'Top P', min: 0, max: 1 },
    { key: 'frequency_penalty', label: 'Frequency Penalty', min: -2, max: 2 },
    { key: 'presence_penalty', label: 'Presence Penalty', min: -2, max: 2 },
  ];
  for (const { key, label, min, max } of optionalRanges) {
    const trimmed = data[key]?.trim();
    if (trimmed && trimmed !== '') {
      const num = Number(trimmed);
      if (isNaN(num)) {
        errors[key] = `${label}必须为数字`;
      } else if (num < min || num > max) {
        errors[key] = `${label}范围为 ${min} ~ ${max}`;
      }
    }
  }

  // embedding_dimensions：仅 embedding 类型
  if (modelType === 'embedding') {
    const edTrimmed = data.embedding_dimensions?.trim();
    if (edTrimmed && edTrimmed !== '') {
      const num = Number(edTrimmed);
      if (isNaN(num) || num <= 0 || !Number.isInteger(num)) {
        errors.embedding_dimensions = 'Embedding 维度必须为正整数';
      }
    }
  }

  return errors;
}

export const ModelConfigForm = memo(function ModelConfigForm({
  model,
  onSave,
  onCancel,
}: ModelConfigFormProps) {
  // 表单数据（全部用字符串管理，提交时转换）
  const [formData, setFormData] = useState<FormData>({
    name: model.name,
    url: model.url,
    api_key: model.apiKey, // 脱敏值，用户可覆盖
    max_context_window: String(model.maxContextWindow),
    max_input_tokens: String(model.maxInputTokens),
    max_output_tokens: String(model.maxOutputTokens),
    temperature: model.temperature === null ? '' : String(model.temperature),
    top_p: model.topP === null ? '' : String(model.topP),
    frequency_penalty:
      model.frequencyPenalty === null ? '' : String(model.frequencyPenalty),
    presence_penalty:
      model.presencePenalty === null ? '' : String(model.presencePenalty),
    embedding_dimensions:
      model.embeddingDimensions === null
        ? ''
        : String(model.embeddingDimensions),
  });
  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const handleChange = useCallback(
    (key: keyof FormData, value: string) => {
      setFormData((prev) => ({ ...prev, [key]: value }));
      // 清除该字段的错误
      setErrors((prev) => {
        if (!prev[key]) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      });
    },
    []
  );

  /** 清除选填字段（设为空，提交时转 null） */
  const handleClear = useCallback((key: keyof FormData) => {
    setFormData((prev) => ({ ...prev, [key]: '' }));
    setErrors((prev) => {
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  const handleSubmit = useCallback(async () => {
    // 前端校验
    const validationErrors = validateForm(formData, model.type);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);

    try {
      const payload: ModelUpdateRequest = {
        name: formData.name.trim(),
        url: formData.url.trim(),
        api_key: formData.api_key.trim(),
        max_context_window: Number(formData.max_context_window),
        max_input_tokens: Number(formData.max_input_tokens),
        max_output_tokens: Number(formData.max_output_tokens),
        temperature: parseOptionalNumber(formData.temperature),
        top_p: parseOptionalNumber(formData.top_p),
        frequency_penalty: parseOptionalNumber(formData.frequency_penalty),
        presence_penalty: parseOptionalNumber(formData.presence_penalty),
      };

      // embedding 类型才发送 embedding_dimensions
      if (model.type === 'embedding') {
        payload.embedding_dimensions = parseOptionalNumber(
          formData.embedding_dimensions
        );
      }

      const updated = await updateModel(model.id, payload);
      onSave(updated);
    } catch (err: unknown) {
      const resp = (err as { response?: { data?: { message?: string } } })?.response;
      setSubmitError(resp?.data?.message || '保存失败，请稍后重试');
    } finally {
      setIsSubmitting(false);
    }
  }, [formData, model.id, model.type, onSave]);

  const isEmbedding = model.type === 'embedding';

  return (
    <div className="rounded-xl border border-primary-200 bg-white p-6 shadow-md dark:border-primary-800 dark:bg-gray-800">
      <h3 className="mb-4 text-lg font-semibold text-gray-900 dark:text-white">
        编辑{model.type === 'language' ? '语言' : '嵌入'}模型配置
      </h3>

      {submitError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
          {submitError}
        </div>
      )}

      <div className="space-y-4">
        {/* 基础配置 */}
        <FormField
          label="模型名称"
          value={formData.name}
          error={errors.name}
          required
          onChange={(v) => handleChange('name', v)}
        />
        <FormField
          label="API 地址"
          value={formData.url}
          error={errors.url}
          required
          onChange={(v) => handleChange('url', v)}
        />
        <FormField
          label="API Key"
          value={formData.api_key}
          error={errors.api_key}
          required
          type="password"
          onChange={(v) => handleChange('api_key', v)}
        />

        {/* 容量参数 */}
        <div className="border-t border-gray-100 pt-4 dark:border-gray-700">
          <h4 className="mb-3 text-sm font-medium text-gray-500">容量参数</h4>
          <div className="grid gap-4 sm:grid-cols-3">
            <FormField
              label="最大上下文窗口"
              value={formData.max_context_window}
              error={errors.max_context_window}
              required
              type="number"
              onChange={(v) => handleChange('max_context_window', v)}
            />
            <FormField
              label="最大输入 Token"
              value={formData.max_input_tokens}
              error={errors.max_input_tokens}
              required
              type="number"
              onChange={(v) => handleChange('max_input_tokens', v)}
            />
            <FormField
              label="最大输出 Token"
              value={formData.max_output_tokens}
              error={errors.max_output_tokens}
              required
              type="number"
              onChange={(v) => handleChange('max_output_tokens', v)}
            />
          </div>
        </div>

        {/* 生成参数（选填） */}
        <div className="border-t border-gray-100 pt-4 dark:border-gray-700">
          <h4 className="mb-3 text-sm font-medium text-gray-500">
            生成参数（选填）
          </h4>
          <div className="grid gap-4 sm:grid-cols-2">
            <OptionalField
              label="Temperature"
              value={formData.temperature}
              error={errors.temperature}
              placeholder="0 ~ 2"
              onChange={(v) => handleChange('temperature', v)}
              onClear={() => handleClear('temperature')}
            />
            <OptionalField
              label="Top P"
              value={formData.top_p}
              error={errors.top_p}
              placeholder="0 ~ 1"
              onChange={(v) => handleChange('top_p', v)}
              onClear={() => handleClear('top_p')}
            />
            <OptionalField
              label="Frequency Penalty"
              value={formData.frequency_penalty}
              error={errors.frequency_penalty}
              placeholder="-2 ~ 2"
              onChange={(v) => handleChange('frequency_penalty', v)}
              onClear={() => handleClear('frequency_penalty')}
            />
            <OptionalField
              label="Presence Penalty"
              value={formData.presence_penalty}
              error={errors.presence_penalty}
              placeholder="-2 ~ 2"
              onChange={(v) => handleChange('presence_penalty', v)}
              onClear={() => handleClear('presence_penalty')}
            />
            {isEmbedding && (
              <OptionalField
                label="Embedding 维度"
                value={formData.embedding_dimensions}
                error={errors.embedding_dimensions}
                placeholder="正整数"
                onChange={(v) => handleChange('embedding_dimensions', v)}
                onClear={() => handleClear('embedding_dimensions')}
              />
            )}
          </div>
        </div>
      </div>

      {/* 操作按钮 */}
      <div className="mt-6 flex items-center justify-end gap-3">
        <button
          onClick={onCancel}
          disabled={isSubmitting}
          className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
        >
          取消
        </button>
        <button
          onClick={handleSubmit}
          disabled={isSubmitting}
          className="rounded-lg bg-primary-500 px-4 py-2 text-sm text-white transition-colors hover:bg-primary-600 disabled:opacity-50"
        >
          {isSubmitting ? '保存中...' : '保存'}
        </button>
      </div>
    </div>
  );
});

/** 必填表单字段 */
function FormField({
  label,
  value,
  error,
  required,
  type = 'text',
  onChange,
}: {
  label: string;
  value: string;
  error?: string;
  required?: boolean;
  type?: string;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
        {label}
        {required && <span className="text-red-500"> *</span>}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full rounded-lg border px-3 py-2 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500 dark:bg-gray-700 dark:text-white ${
          error
            ? 'border-red-300 dark:border-red-600'
            : 'border-gray-300 dark:border-gray-600'
        }`}
      />
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  );
}

/** 选填字段（带清除按钮） */
function OptionalField({
  label,
  value,
  error,
  placeholder,
  onChange,
  onClear,
}: {
  label: string;
  value: string;
  error?: string;
  placeholder?: string;
  onChange: (value: string) => void;
  onClear: () => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          type="number"
          step="any"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={`flex-1 rounded-lg border px-3 py-2 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500 dark:bg-gray-700 dark:text-white ${
            error
              ? 'border-red-300 dark:border-red-600'
              : 'border-gray-300 dark:border-gray-600'
          }`}
        />
        {value !== '' && (
          <button
            type="button"
            onClick={onClear}
            className="rounded-lg border border-gray-300 px-2 py-2 text-xs text-gray-500 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-400 dark:hover:bg-gray-700"
            title="清除（设为未设置）"
          >
            清除
          </button>
        )}
      </div>
      {error && <p className="mt-1 text-xs text-red-500">{error}</p>}
    </div>
  );
}
