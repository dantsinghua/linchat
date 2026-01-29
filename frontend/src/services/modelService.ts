/**
 * 模型配置 API 服务
 *
 * 参考: specs/003-model-config/contracts/api.yaml
 */
import { get, put } from '@/services/api';
import { ModelConfig, ModelUpdateRequest } from '@/types/model';

/**
 * 获取所有模型配置
 */
export async function fetchModels(): Promise<ModelConfig[]> {
  const response = await get<ModelConfig[]>('/models/');
  return response.data;
}

/**
 * 获取单个模型配置
 */
export async function fetchModelById(id: number): Promise<ModelConfig> {
  const response = await get<ModelConfig>(`/models/${id}/`);
  return response.data;
}

/**
 * 更新模型配置
 */
export async function updateModel(
  id: number,
  data: ModelUpdateRequest
): Promise<ModelConfig> {
  const response = await put<ModelConfig>(`/models/${id}/`, data);
  return response.data;
}
