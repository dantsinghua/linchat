/**
 * 家庭成员状态管理
 *
 * 015-family-multiuser: 管理目标用户切换、成员列表
 */
import { create } from 'zustand';

import type { MemberResponse } from '@/services/memberService';
import { getMembers } from '@/services/memberService';

export interface Member {
  user_id: number;
  username: string;
  member_type: 'member' | 'guest';
  status: number;
  guest_expires_at: string | null;
  is_expired: boolean;
  created_time: string;
}

interface MemberState {
  // 当前目标用户 ID（null 表示查看自己）
  targetUserId: number | null;
  // 当前目标用户名
  targetUsername: string | null;
  // 家庭成员列表
  members: Member[];
  // 登录用户 ID（用于判断 isViewingOther）
  authUserId: number | null;
  // 成员列表加载状态
  isLoading: boolean;

  // Computed
  isViewingOther: () => boolean;

  // Actions
  setAuthUserId: (userId: number) => void;
  loadMembers: () => Promise<void>;
  setTargetUser: (userId: number, username: string) => void;
  clearTarget: () => void;
  restoreTargetFromStorage: () => void;
}

export const useMemberStore = create<MemberState>((set, get) => ({
  targetUserId: null,
  targetUsername: null,
  members: [],
  authUserId: null,
  isLoading: false,

  isViewingOther: () =>
    get().targetUserId !== null && get().targetUserId !== get().authUserId,

  setAuthUserId: (userId: number) => set({ authUserId: userId }),

  loadMembers: async () => {
    set({ isLoading: true });
    try {
      const response = await getMembers(true);
      if (response.code === 'SUCCESS' && response.data) {
        const members: Member[] = response.data.map(
          (m: MemberResponse) => ({
            user_id: m.user_id,
            username: m.username,
            member_type: m.member_type,
            status: m.status,
            guest_expires_at: m.guest_expires_at,
            is_expired: m.is_expired,
            created_time: m.created_time,
          })
        );
        set({ members });
      }
    } catch (error) {
      console.error('[LinChat] 加载成员列表失败:', error);
    } finally {
      set({ isLoading: false });
    }
  },

  setTargetUser: (userId: number, username: string) => {
    set({ targetUserId: userId, targetUsername: username });
    localStorage.setItem('linchat_target_user_id', String(userId));
    localStorage.setItem('linchat_target_username', username);
  },

  clearTarget: () => {
    set({ targetUserId: null, targetUsername: null });
    localStorage.removeItem('linchat_target_user_id');
    localStorage.removeItem('linchat_target_username');
  },

  restoreTargetFromStorage: () => {
    const storedId = localStorage.getItem('linchat_target_user_id');
    const storedName = localStorage.getItem('linchat_target_username');
    if (storedId && storedName) {
      const userId = Number(storedId);
      const { members, authUserId } = get();
      // 目标是自己则无需恢复
      if (userId === authUserId) {
        localStorage.removeItem('linchat_target_user_id');
        localStorage.removeItem('linchat_target_username');
        return;
      }
      // 检查成员列表中是否存在且未过期
      const member = members.find((m) => m.user_id === userId);
      if (member && !member.is_expired) {
        set({ targetUserId: userId, targetUsername: storedName });
      } else {
        // 成员不存在或已过期，清除无效缓存
        localStorage.removeItem('linchat_target_user_id');
        localStorage.removeItem('linchat_target_username');
      }
    }
  },
}));
