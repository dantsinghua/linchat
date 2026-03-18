/**
 * 家庭成员管理服务
 *
 * 015-family-multiuser: 提供成员列表查询和创建功能
 */
import { get } from '@/services/api';
import apiClient from '@/services/api';
import type { ApiResponse } from '@/types';

export interface MemberResponse {
  user_id: number;
  username: string;
  member_type: 'member' | 'guest';
  status: number;
  guest_expires_at: string | null;
  is_expired: boolean;
  created_time: string;
}

/**
 * 获取家庭成员列表
 *
 * @param includeExpired - 是否包含已过期成员
 */
export async function getMembers(
  includeExpired = false
): Promise<ApiResponse<MemberResponse[]>> {
  return get<MemberResponse[]>('/members/', {
    include_expired: includeExpired,
  });
}

/**
 * 创建家庭成员
 *
 * 使用 FormData 因为包含音频文件（声纹注册）
 *
 * @param data - FormData 包含 username, password, member_type, audio(可选)
 */
export async function createMember(
  data: FormData
): Promise<ApiResponse<MemberResponse>> {
  const response = await apiClient.post<ApiResponse<MemberResponse>>(
    '/members/',
    data,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    }
  );
  return response.data;
}
