/**
 * memberStore 单元测试
 *
 * T038: 测试家庭成员状态管理
 * - 初始状态
 * - setAuthUserId / setTargetUser / clearTarget
 * - isViewingOther() 判断逻辑
 * - restoreTargetFromStorage 各分支
 * - loadMembers 成功/失败
 */

// ========== localStorage Mock ==========

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();
Object.defineProperty(window, 'localStorage', { value: localStorageMock });

// ========== Mock memberService ==========

jest.mock('@/services/memberService', () => ({
  getMembers: jest.fn(),
}));

import { getMembers } from '@/services/memberService';
import { useMemberStore } from '@/stores/memberStore';

const mockGetMembers = getMembers as jest.Mock;

// ========== 测试辅助 ==========

/** 重置 store 和 localStorage 到干净状态 */
function resetStore() {
  useMemberStore.setState({
    targetUserId: null,
    targetUsername: null,
    members: [],
    authUserId: null,
    isLoading: false,
  });
  localStorageMock.clear();
}

// ─── 每次测试前重置 ───

beforeEach(() => {
  jest.clearAllMocks();
  resetStore();
});

// ========== 测试用例 ==========

describe('memberStore', () => {
  // ─── 初始状态 ───

  describe('初始状态', () => {
    it('targetUserId 应为 null', () => {
      const { targetUserId } = useMemberStore.getState();
      expect(targetUserId).toBeNull();
    });

    it('targetUsername 应为 null', () => {
      const { targetUsername } = useMemberStore.getState();
      expect(targetUsername).toBeNull();
    });

    it('authUserId 应为 null', () => {
      const { authUserId } = useMemberStore.getState();
      expect(authUserId).toBeNull();
    });

    it('members 应为空数组', () => {
      const { members } = useMemberStore.getState();
      expect(members).toEqual([]);
    });

    it('isLoading 应为 false', () => {
      const { isLoading } = useMemberStore.getState();
      expect(isLoading).toBe(false);
    });

    it('isViewingOther() 应返回 false', () => {
      const { isViewingOther } = useMemberStore.getState();
      expect(isViewingOther()).toBe(false);
    });
  });

  // ─── setAuthUserId ───

  describe('setAuthUserId()', () => {
    it('应正确设置 authUserId', () => {
      const { setAuthUserId } = useMemberStore.getState();
      setAuthUserId(100);

      expect(useMemberStore.getState().authUserId).toBe(100);
    });

    it('多次调用应覆盖前值', () => {
      const { setAuthUserId } = useMemberStore.getState();
      setAuthUserId(100);
      setAuthUserId(200);

      expect(useMemberStore.getState().authUserId).toBe(200);
    });
  });

  // ─── setTargetUser ───

  describe('setTargetUser()', () => {
    it('应正确设置 targetUserId 和 targetUsername', () => {
      const { setTargetUser } = useMemberStore.getState();
      setTargetUser(42, '橘猫团子');

      const state = useMemberStore.getState();
      expect(state.targetUserId).toBe(42);
      expect(state.targetUsername).toBe('橘猫团子');
    });

    it('应将 targetUserId 写入 localStorage', () => {
      const { setTargetUser } = useMemberStore.getState();
      setTargetUser(42, '橘猫团子');

      expect(localStorageMock.getItem('linchat_target_user_id')).toBe('42');
    });

    it('应将 targetUsername 写入 localStorage', () => {
      const { setTargetUser } = useMemberStore.getState();
      setTargetUser(42, '橘猫团子');

      expect(localStorageMock.getItem('linchat_target_username')).toBe(
        '橘猫团子'
      );
    });
  });

  // ─── isViewingOther ───

  describe('isViewingOther()', () => {
    it('targetUserId 为 null 时应返回 false', () => {
      useMemberStore.setState({ targetUserId: null, authUserId: 100 });

      expect(useMemberStore.getState().isViewingOther()).toBe(false);
    });

    it('targetUserId === authUserId 时应返回 false（查看自己）', () => {
      useMemberStore.setState({ targetUserId: 100, authUserId: 100 });

      expect(useMemberStore.getState().isViewingOther()).toBe(false);
    });

    it('targetUserId !== authUserId 时应返回 true（代查模式）', () => {
      useMemberStore.setState({ targetUserId: 42, authUserId: 100 });

      expect(useMemberStore.getState().isViewingOther()).toBe(true);
    });

    it('authUserId 为 null、targetUserId 有值时应返回 true', () => {
      useMemberStore.setState({ targetUserId: 42, authUserId: null });

      expect(useMemberStore.getState().isViewingOther()).toBe(true);
    });
  });

  // ─── clearTarget ───

  describe('clearTarget()', () => {
    it('应清除 targetUserId 和 targetUsername', () => {
      // 先设置目标用户
      useMemberStore.getState().setTargetUser(42, '橘猫团子');

      // 执行清除
      useMemberStore.getState().clearTarget();

      const state = useMemberStore.getState();
      expect(state.targetUserId).toBeNull();
      expect(state.targetUsername).toBeNull();
    });

    it('应清除 localStorage 中的目标用户信息', () => {
      // 先设置
      localStorageMock.setItem('linchat_target_user_id', '42');
      localStorageMock.setItem('linchat_target_username', '橘猫团子');

      // 执行清除
      useMemberStore.getState().clearTarget();

      expect(localStorageMock.getItem('linchat_target_user_id')).toBeNull();
      expect(localStorageMock.getItem('linchat_target_username')).toBeNull();
    });

    it('无目标用户时调用不应报错', () => {
      expect(() => {
        useMemberStore.getState().clearTarget();
      }).not.toThrow();
    });
  });

  // ─── restoreTargetFromStorage ───

  describe('restoreTargetFromStorage()', () => {
    it('localStorage 无数据时不应修改状态', () => {
      useMemberStore.getState().restoreTargetFromStorage();

      const state = useMemberStore.getState();
      expect(state.targetUserId).toBeNull();
      expect(state.targetUsername).toBeNull();
    });

    it('members 列表中有有效用户时应恢复目标', () => {
      // 设置前置状态
      useMemberStore.setState({
        authUserId: 100,
        members: [
          {
            user_id: 42,
            username: '橘猫团子',
            member_type: 'member',
            status: 1,
            guest_expires_at: null,
            is_expired: false,
            created_time: '2026-03-01T00:00:00Z',
          },
        ],
      });
      localStorageMock.setItem('linchat_target_user_id', '42');
      localStorageMock.setItem('linchat_target_username', '橘猫团子');

      useMemberStore.getState().restoreTargetFromStorage();

      const state = useMemberStore.getState();
      expect(state.targetUserId).toBe(42);
      expect(state.targetUsername).toBe('橘猫团子');
    });

    it('members 列表中无该用户时应自动清除', () => {
      useMemberStore.setState({
        authUserId: 100,
        members: [
          {
            user_id: 99,
            username: '其他用户',
            member_type: 'member',
            status: 1,
            guest_expires_at: null,
            is_expired: false,
            created_time: '2026-03-01T00:00:00Z',
          },
        ],
      });
      localStorageMock.setItem('linchat_target_user_id', '42');
      localStorageMock.setItem('linchat_target_username', '橘猫团子');

      useMemberStore.getState().restoreTargetFromStorage();

      // 状态应保持清空
      const state = useMemberStore.getState();
      expect(state.targetUserId).toBeNull();
      expect(state.targetUsername).toBeNull();
      // localStorage 也应被清除
      expect(localStorageMock.getItem('linchat_target_user_id')).toBeNull();
      expect(localStorageMock.getItem('linchat_target_username')).toBeNull();
    });

    it('用户已过期时应自动清除', () => {
      useMemberStore.setState({
        authUserId: 100,
        members: [
          {
            user_id: 42,
            username: '橘猫团子',
            member_type: 'guest',
            status: 1,
            guest_expires_at: '2026-03-01T00:00:00Z',
            is_expired: true,
            created_time: '2026-02-01T00:00:00Z',
          },
        ],
      });
      localStorageMock.setItem('linchat_target_user_id', '42');
      localStorageMock.setItem('linchat_target_username', '橘猫团子');

      useMemberStore.getState().restoreTargetFromStorage();

      const state = useMemberStore.getState();
      expect(state.targetUserId).toBeNull();
      expect(state.targetUsername).toBeNull();
      expect(localStorageMock.getItem('linchat_target_user_id')).toBeNull();
      expect(localStorageMock.getItem('linchat_target_username')).toBeNull();
    });

    it('目标是自己时应清除 localStorage 且不恢复', () => {
      useMemberStore.setState({
        authUserId: 100,
        members: [
          {
            user_id: 100,
            username: '安琳',
            member_type: 'member',
            status: 1,
            guest_expires_at: null,
            is_expired: false,
            created_time: '2026-01-01T00:00:00Z',
          },
        ],
      });
      localStorageMock.setItem('linchat_target_user_id', '100');
      localStorageMock.setItem('linchat_target_username', '安琳');

      useMemberStore.getState().restoreTargetFromStorage();

      // 不应设置目标
      const state = useMemberStore.getState();
      expect(state.targetUserId).toBeNull();
      expect(state.targetUsername).toBeNull();
      // localStorage 应被清除
      expect(localStorageMock.getItem('linchat_target_user_id')).toBeNull();
      expect(localStorageMock.getItem('linchat_target_username')).toBeNull();
    });

    it('仅有 user_id 没有 username 时不应恢复', () => {
      localStorageMock.setItem('linchat_target_user_id', '42');
      // 没有 linchat_target_username

      useMemberStore.getState().restoreTargetFromStorage();

      const state = useMemberStore.getState();
      expect(state.targetUserId).toBeNull();
    });
  });

  // ─── loadMembers ───

  describe('loadMembers()', () => {
    it('成功时应更新 members 列表', async () => {
      const mockResponse = {
        code: 'SUCCESS',
        message: 'ok',
        data: [
          {
            user_id: 42,
            username: '橘猫团子',
            member_type: 'member' as const,
            status: 1,
            guest_expires_at: null,
            is_expired: false,
            created_time: '2026-03-01T00:00:00Z',
          },
          {
            user_id: 43,
            username: '访客A',
            member_type: 'guest' as const,
            status: 1,
            guest_expires_at: '2026-04-01T00:00:00Z',
            is_expired: false,
            created_time: '2026-03-10T00:00:00Z',
          },
        ],
      };
      mockGetMembers.mockResolvedValueOnce(mockResponse);

      await useMemberStore.getState().loadMembers();

      const state = useMemberStore.getState();
      expect(state.members).toHaveLength(2);
      expect(state.members[0].user_id).toBe(42);
      expect(state.members[0].username).toBe('橘猫团子');
      expect(state.members[1].member_type).toBe('guest');
      expect(state.isLoading).toBe(false);
    });

    it('加载中 isLoading 应为 true', async () => {
      // 使用一个不会立即 resolve 的 Promise 来捕获中间状态
      let resolvePromise: (value: unknown) => void;
      const pendingPromise = new Promise((resolve) => {
        resolvePromise = resolve;
      });
      mockGetMembers.mockReturnValueOnce(pendingPromise);

      const loadPromise = useMemberStore.getState().loadMembers();

      // 加载中
      expect(useMemberStore.getState().isLoading).toBe(true);

      // 完成加载
      resolvePromise!({
        code: 'SUCCESS',
        message: 'ok',
        data: [],
      });
      await loadPromise;

      expect(useMemberStore.getState().isLoading).toBe(false);
    });

    it('请求失败时 isLoading 应重置为 false', async () => {
      mockGetMembers.mockRejectedValueOnce(new Error('Network Error'));

      await useMemberStore.getState().loadMembers();

      const state = useMemberStore.getState();
      expect(state.isLoading).toBe(false);
      // members 不应被修改
      expect(state.members).toEqual([]);
    });

    it('响应 code 非 SUCCESS 时不应更新 members', async () => {
      mockGetMembers.mockResolvedValueOnce({
        code: 'FORBIDDEN',
        message: '无权限',
        data: null,
      });

      await useMemberStore.getState().loadMembers();

      expect(useMemberStore.getState().members).toEqual([]);
    });

    it('应使用 include_expired=true 参数调用 getMembers', async () => {
      mockGetMembers.mockResolvedValueOnce({
        code: 'SUCCESS',
        message: 'ok',
        data: [],
      });

      await useMemberStore.getState().loadMembers();

      expect(mockGetMembers).toHaveBeenCalledWith(true);
    });
  });
});
