/**
 * ModelConfigCard 组件测试 (T031)
 *
 * 覆盖:
 * - 渲染 language/embedding 两种卡片
 * - API Key 脱敏显示
 * - 选填参数 NULL 显示"未设置"
 * - embedding_dimensions 仅 embedding 卡片展示
 * - 编辑按钮点击
 */
import { render, screen, fireEvent } from '@testing-library/react';

import { ModelConfigCard } from '@/components/settings/ModelConfigCard';
import type { ModelConfig } from '@/types/model';

const mockLanguageModel: ModelConfig = {
  id: 1,
  type: 'language',
  name: 'deepseek-v3',
  url: 'https://api.example.com/v1',
  apiKey: 'sk-t****cxyz',
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

const mockEmbeddingModel: ModelConfig = {
  id: 2,
  type: 'embedding',
  name: 'text-embedding-3',
  url: 'https://api.example.com/v1',
  apiKey: 'sk-e****wxyz',
  maxContextWindow: 8192,
  maxInputTokens: 8192,
  maxOutputTokens: 1,
  temperature: null,
  topP: null,
  frequencyPenalty: null,
  presencePenalty: null,
  embeddingDimensions: 1536,
  isActive: true,
  effectiveContextWindow: 7372,
  createdAt: '2026-01-29T00:00:00Z',
  updatedAt: '2026-01-29T00:00:00Z',
};

describe('ModelConfigCard', () => {
  describe('语言模型卡片', () => {
    it('应渲染模型名称和类型标签', () => {
      render(<ModelConfigCard model={mockLanguageModel} />);

      expect(screen.getByText('deepseek-v3')).toBeInTheDocument();
      expect(screen.getByText('语言模型')).toBeInTheDocument();
    });

    it('应显示脱敏的 API Key', () => {
      render(<ModelConfigCard model={mockLanguageModel} />);

      expect(screen.getByText('sk-t****cxyz')).toBeInTheDocument();
    });

    it('应显示选填参数为"未设置"', () => {
      render(<ModelConfigCard model={mockLanguageModel} />);

      const unsetElements = screen.getAllByText('未设置');
      // temperature, top_p, frequency_penalty, presence_penalty 都应为"未设置"
      expect(unsetElements.length).toBeGreaterThanOrEqual(4);
    });

    it('不应展示 embedding_dimensions', () => {
      render(<ModelConfigCard model={mockLanguageModel} />);

      expect(screen.queryByText('Embedding 维度')).not.toBeInTheDocument();
    });

    it('应显示容量参数', () => {
      render(<ModelConfigCard model={mockLanguageModel} />);

      expect(screen.getByText('65,536')).toBeInTheDocument();
      expect(screen.getByText('32,768')).toBeInTheDocument();
      expect(screen.getByText('8,192')).toBeInTheDocument();
    });
  });

  describe('嵌入模型卡片', () => {
    it('应渲染 embedding 类型标签', () => {
      render(<ModelConfigCard model={mockEmbeddingModel} />);

      expect(screen.getByText('嵌入模型')).toBeInTheDocument();
    });

    it('应展示 embedding_dimensions', () => {
      render(<ModelConfigCard model={mockEmbeddingModel} />);

      expect(screen.getByText('Embedding 维度')).toBeInTheDocument();
      expect(screen.getByText('1536')).toBeInTheDocument();
    });
  });

  describe('编辑按钮', () => {
    it('传入 onEdit 时应显示编辑按钮', () => {
      const mockOnEdit = jest.fn();
      render(<ModelConfigCard model={mockLanguageModel} onEdit={mockOnEdit} />);

      expect(screen.getByText('编辑')).toBeInTheDocument();
    });

    it('点击编辑按钮应调用 onEdit', () => {
      const mockOnEdit = jest.fn();
      render(<ModelConfigCard model={mockLanguageModel} onEdit={mockOnEdit} />);

      fireEvent.click(screen.getByText('编辑'));
      expect(mockOnEdit).toHaveBeenCalledWith(mockLanguageModel);
    });

    it('不传 onEdit 时不应显示编辑按钮', () => {
      render(<ModelConfigCard model={mockLanguageModel} />);

      expect(screen.queryByText('编辑')).not.toBeInTheDocument();
    });
  });

  describe('激活状态', () => {
    it('应显示已激活状态', () => {
      render(<ModelConfigCard model={mockLanguageModel} />);

      expect(screen.getByText('已激活')).toBeInTheDocument();
    });

    it('应显示未激活状态', () => {
      const inactiveModel = { ...mockLanguageModel, isActive: false };
      render(<ModelConfigCard model={inactiveModel} />);

      expect(screen.getByText('未激活')).toBeInTheDocument();
    });
  });
});
