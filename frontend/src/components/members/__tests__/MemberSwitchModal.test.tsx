/**
 * MemberSwitchModal 单元测试 (T032 + T039)
 *
 * 015-family-multiuser:
 * - isOpen=true 渲染模态框，isOpen=false 不渲染
 * - 显示活跃用户列表
 * - 过期访客灰色展示在底部，不可点击
 * - 点击活跃用户触发 onSelect 回调
 * - 无删除按钮
 * - 点击"添加用户"触发 onCreateUser
 * - 当前用户显示"当前"标记
 */
import { render, screen, fireEvent } from '@testing-library/react';

// ========== Mock 依赖 ==========

import type { Member } from '@/stores/memberStore';

const mockMembers: Member[] = [
  {
    user_id: 1,
    username: 'anlin',
    member_type: 'member',
    status: 1,
    guest_expires_at: null,
    is_expired: false,
    created_time: '2026-01-01T00:00:00Z',
  },
  {
    user_id: 2,
    username: 'tuanzi',
    member_type: 'member',
    status: 1,
    guest_expires_at: null,
    is_expired: false,
    created_time: '2026-02-01T00:00:00Z',
  },
  {
    user_id: 3,
    username: 'guest_a',
    member_type: 'guest',
    status: 1,
    guest_expires_at: '2026-01-01T00:00:00Z',
    is_expired: true,
    created_time: '2025-12-01T00:00:00Z',
  },
  {
    // status=0 的成员不应显示
    user_id: 4,
    username: 'deleted_user',
    member_type: 'member',
    status: 0,
    guest_expires_at: null,
    is_expired: false,
    created_time: '2026-01-15T00:00:00Z',
  },
];

let mockAuthUserId = 1;

jest.mock('@/stores/memberStore', () => ({
  useMemberStore: (selector: (state: { members: Member[]; authUserId: number }) => unknown) => {
    const state = {
      members: mockMembers,
      authUserId: mockAuthUserId,
    };
    return selector(state);
  },
}));

// avatarUtils 使用真实实现
// 不需要 mock，直接 import 即可

import { MemberSwitchModal } from '@/components/members/MemberSwitchModal';

// ========== 测试辅助 ==========

const defaultProps = {
  isOpen: true,
  onClose: jest.fn(),
  onSelect: jest.fn(),
  onCreateUser: jest.fn(),
};

function renderModal(props = {}) {
  return render(<MemberSwitchModal {...defaultProps} {...props} />);
}

// ========== 测试用例 ==========

beforeEach(() => {
  jest.clearAllMocks();
  mockAuthUserId = 1;
});

describe('MemberSwitchModal (T032 + T039)', () => {
  // ─── 渲染控制 ───

  describe('渲染控制', () => {
    it('isOpen=true 时应渲染模态框', () => {
      renderModal({ isOpen: true });

      expect(screen.getByText('家庭成员')).toBeInTheDocument();
    });

    it('isOpen=false 时不应渲染任何内容', () => {
      const { container } = renderModal({ isOpen: false });

      expect(container.innerHTML).toBe('');
    });
  });

  // ─── 活跃用户列表 ───

  describe('活跃用户列表', () => {
    it('应显示活跃成员（status=1 且未过期）', () => {
      renderModal();

      expect(screen.getByText('anlin')).toBeInTheDocument();
      expect(screen.getByText('tuanzi')).toBeInTheDocument();
    });

    it('不应显示 status=0 的成员', () => {
      renderModal();

      expect(screen.queryByText('deleted_user')).not.toBeInTheDocument();
    });

    it('应显示成员类型标签', () => {
      renderModal();

      // anlin 和 tuanzi 都是 member 类型
      const memberBadges = screen.getAllByText('成员');
      expect(memberBadges.length).toBe(2);
    });
  });

  // ─── 过期访客 ───

  describe('过期访客', () => {
    it('过期访客应显示在列表中', () => {
      renderModal();

      expect(screen.getByText('guest_a')).toBeInTheDocument();
    });

    it('过期访客应展示"访客(已过期)"标签', () => {
      renderModal();

      expect(screen.getByText('访客(已过期)')).toBeInTheDocument();
    });

    it('过期访客容器应有 opacity-50 样式（灰色展示）', () => {
      renderModal();

      const expiredUserElement = screen.getByText('guest_a').closest('div.opacity-50');
      expect(expiredUserElement).not.toBeNull();
    });

    it('过期访客不应有 onClick（不可点击，渲染为 div 非 button）', () => {
      renderModal();

      // 过期访客渲染为 div 而非 button
      const expiredLabel = screen.getByText('guest_a');
      const container = expiredLabel.closest('[class*="opacity-50"]');
      expect(container).not.toBeNull();
      expect(container?.tagName).toBe('DIV');

      // 确认不是 button
      const buttons = screen.getAllByRole('button');
      const expiredButton = buttons.find((btn) =>
        btn.textContent?.includes('guest_a')
      );
      expect(expiredButton).toBeUndefined();
    });
  });

  // ─── 点击交互 ───

  describe('点击交互', () => {
    it('点击活跃用户应触发 onSelect 回调', () => {
      const onSelect = jest.fn();
      renderModal({ onSelect });

      // 点击 tuanzi（非当前用户）
      const tuanziButton = screen.getByText('tuanzi').closest('button');
      expect(tuanziButton).not.toBeNull();
      fireEvent.click(tuanziButton!);

      expect(onSelect).toHaveBeenCalledWith(2, 'tuanzi');
    });

    it('点击当前用户也应触发 onSelect 回调', () => {
      const onSelect = jest.fn();
      renderModal({ onSelect });

      const anlinButton = screen.getByText('anlin').closest('button');
      fireEvent.click(anlinButton!);

      expect(onSelect).toHaveBeenCalledWith(1, 'anlin');
    });

    it('点击"添加用户"应触发 onCreateUser 回调', () => {
      const onCreateUser = jest.fn();
      renderModal({ onCreateUser });

      const addButton = screen.getByText('添加用户');
      fireEvent.click(addButton);

      expect(onCreateUser).toHaveBeenCalledTimes(1);
    });
  });

  // ─── 无删除按钮 ───

  describe('无删除按钮', () => {
    it('模态框中不应存在删除按钮', () => {
      renderModal();

      expect(screen.queryByText('删除')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('删除')).not.toBeInTheDocument();
    });
  });

  // ─── 当前用户标记 ───

  describe('当前用户标记', () => {
    it('当前用户（authUserId=1）应显示"当前"标记', () => {
      mockAuthUserId = 1;
      renderModal();

      expect(screen.getByText('当前')).toBeInTheDocument();
    });

    it('"当前"标记应仅出现一次', () => {
      renderModal();

      const currentLabels = screen.getAllByText('当前');
      expect(currentLabels).toHaveLength(1);
    });

    it('非当前用户不应显示"当前"标记', () => {
      renderModal();

      // tuanzi (user_id=2) 不应有当前标记
      const tuanziButton = screen.getByText('tuanzi').closest('button');
      expect(tuanziButton?.textContent).not.toContain('当前');
    });
  });

  // ─── 关闭模态框 ───

  describe('关闭模态框', () => {
    it('点击遮罩层应触发 onClose', () => {
      const onClose = jest.fn();
      renderModal({ onClose });

      // 点击遮罩层（最外层 div）
      const overlay = screen.getByText('家庭成员').closest('.fixed');
      expect(overlay).not.toBeNull();
      fireEvent.click(overlay!);

      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('点击模态框内容区域不应触发 onClose（stopPropagation）', () => {
      const onClose = jest.fn();
      renderModal({ onClose });

      // 点击标题
      fireEvent.click(screen.getByText('家庭成员'));

      expect(onClose).not.toHaveBeenCalled();
    });
  });
});
