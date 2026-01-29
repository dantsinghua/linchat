/**
 * modelService 测试 (T031 + T032)
 *
 * 覆盖:
 * - fetchModels API 调用
 * - fetchModelById API 调用
 * - updateModel API 调用
 */
import * as api from '@/services/api';
import { fetchModels, fetchModelById, updateModel } from '@/services/modelService';

jest.mock('@/services/api');

const mockModelResponse = {
  code: 'SUCCESS',
  message: '操作成功',
  data: [
    {
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
    },
  ],
};

describe('modelService', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('fetchModels', () => {
    it('应调用 GET /models/', async () => {
      (api.get as jest.Mock).mockResolvedValue(mockModelResponse);

      const result = await fetchModels();

      expect(api.get).toHaveBeenCalledWith('/models/');
      expect(result).toEqual(mockModelResponse.data);
    });
  });

  describe('fetchModelById', () => {
    it('应调用 GET /models/{id}/', async () => {
      const singleResponse = { ...mockModelResponse, data: mockModelResponse.data[0] };
      (api.get as jest.Mock).mockResolvedValue(singleResponse);

      const result = await fetchModelById(1);

      expect(api.get).toHaveBeenCalledWith('/models/1/');
      expect(result).toEqual(singleResponse.data);
    });
  });

  describe('updateModel', () => {
    it('应调用 PUT /models/{id}/ 并传入数据', async () => {
      const updateData = {
        name: 'updated',
        url: 'https://api.example.com/v1',
        api_key: 'test****5678',
        max_context_window: 65536,
        max_input_tokens: 32768,
        max_output_tokens: 8192,
      };
      const updatedResponse = {
        ...mockModelResponse,
        data: { ...mockModelResponse.data[0], name: 'updated' },
      };
      (api.put as jest.Mock).mockResolvedValue(updatedResponse);

      const result = await updateModel(1, updateData);

      expect(api.put).toHaveBeenCalledWith('/models/1/', updateData);
      expect(result).toEqual(updatedResponse.data);
    });
  });
});
