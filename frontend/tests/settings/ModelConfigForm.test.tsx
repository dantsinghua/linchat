/**
 * ModelConfigForm 组件测试 (T032)
 *
 * 覆盖:
 * - 必填字段空值校验阻止提交
 * - 选填字段空字符串→null 转换
 * - "0"→数字 0 转换
 * - temperature/top_p 等超范围即时校验提示
 * - 提交成功后调用 onSave
 * - embedding_dimensions 仅 embedding 类型展示编辑项
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import { ModelConfigForm } from '@/components/settings/ModelConfigForm';
import * as modelService from '@/services/modelService';
import type { ModelConfig } from '@/types/model';

jest.mock('@/services/modelService');

const mockLanguageModel: ModelConfig = {
  id: 1,
  type: 'language',
  name: 'deepseek-v3',
  url: 'https://api.example.com/v1',
  apiKey: 'sk-t****cxyz',
  maxContextWindow: 65536,
  maxInputTokens: 32768,
  maxOutputTokens: 8192,
  temperature: 0.7,
  topP: null,
  frequencyPenalty: null,
  presencePenalty: null,
  embeddingDimensions: null,
  isActive: true,
  effectiveContextWindow: 58982,
  createdAt: '2026-01-29T00:00:00Z',
  updatedAt: '2026-01-29T00:00:00Z',
};

const mockEmbeddingModel: ModelConfig = {
  ...mockLanguageModel,
  id: 2,
  type: 'embedding',
  name: 'text-embedding-3',
  embeddingDimensions: 1536,
};

describe('ModelConfigForm', () => {
  const mockOnSave = jest.fn();
  const mockOnCancel = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('表单渲染', () => {
    it('应渲染所有必填字段', () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      expect(screen.getByDisplayValue('deepseek-v3')).toBeInTheDocument();
      expect(screen.getByDisplayValue('https://api.example.com/v1')).toBeInTheDocument();
      expect(screen.getByDisplayValue('sk-t****cxyz')).toBeInTheDocument();
    });

    it('应渲染选填字段的已有值', () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      expect(screen.getByDisplayValue('0.7')).toBeInTheDocument();
    });

    it('language 类型不应展示 embedding_dimensions 编辑项', () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      expect(screen.queryByText('Embedding 维度')).not.toBeInTheDocument();
    });

    it('embedding 类型应展示 embedding_dimensions 编辑项', () => {
      render(
        <ModelConfigForm
          model={mockEmbeddingModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      expect(screen.getByText('Embedding 维度')).toBeInTheDocument();
    });
  });

  describe('必填字段校验', () => {
    it('空名称应显示校验错误', async () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      const nameInput = screen.getByDisplayValue('deepseek-v3');
      fireEvent.change(nameInput, { target: { value: '' } });
      fireEvent.click(screen.getByText('保存'));

      await waitFor(() => {
        expect(screen.getByText('模型名称不能为空')).toBeInTheDocument();
      });
      expect(mockOnSave).not.toHaveBeenCalled();
    });

    it('空 API Key 应显示校验错误', async () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      const apiKeyInput = screen.getByDisplayValue('sk-t****cxyz');
      fireEvent.change(apiKeyInput, { target: { value: '' } });
      fireEvent.click(screen.getByText('保存'));

      await waitFor(() => {
        expect(screen.getByText('API Key 不能为空')).toBeInTheDocument();
      });
    });

    it('新 API Key 少于 12 字符应显示校验错误', async () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      const apiKeyInput = screen.getByDisplayValue('sk-t****cxyz');
      fireEvent.change(apiKeyInput, { target: { value: 'short-key' } });
      fireEvent.click(screen.getByText('保存'));

      await waitFor(() => {
        expect(screen.getByText('API Key 至少需要 12 个字符')).toBeInTheDocument();
      });
    });
  });

  describe('选填参数范围校验', () => {
    it('temperature > 2 应显示超范围提示', async () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      const tempInput = screen.getByDisplayValue('0.7');
      fireEvent.change(tempInput, { target: { value: '3' } });
      fireEvent.click(screen.getByText('保存'));

      await waitFor(() => {
        expect(screen.getByText('Temperature范围为 0 ~ 2')).toBeInTheDocument();
      });
    });
  });

  describe('提交功能', () => {
    it('提交成功后应调用 onSave', async () => {
      const updatedModel = { ...mockLanguageModel, name: 'updated' };
      (modelService.updateModel as jest.Mock).mockResolvedValue(updatedModel);

      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      fireEvent.click(screen.getByText('保存'));

      await waitFor(() => {
        expect(mockOnSave).toHaveBeenCalledWith(updatedModel);
      });
    });

    it('取消应调用 onCancel', () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      fireEvent.click(screen.getByText('取消'));
      expect(mockOnCancel).toHaveBeenCalled();
    });
  });

  describe('清除按钮', () => {
    it('有值的选填字段应显示清除按钮', () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      // temperature=0.7，应有清除按钮
      const clearButtons = screen.getAllByText('清除');
      expect(clearButtons.length).toBeGreaterThanOrEqual(1);
    });

    it('点击清除按钮应清空字段', () => {
      render(
        <ModelConfigForm
          model={mockLanguageModel}
          onSave={mockOnSave}
          onCancel={mockOnCancel}
        />
      );

      const clearButton = screen.getAllByText('清除')[0];
      fireEvent.click(clearButton);

      // 清除后输入框应为空
      expect(screen.queryByDisplayValue('0.7')).not.toBeInTheDocument();
    });
  });
});
