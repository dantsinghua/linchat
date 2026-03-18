/**
 * CreateMemberWizard 单元测试 (T049)
 *
 * 015-family-multiuser:
 * - Step1 显示类型选择 + 用户名密码输入
 * - Step1 填写完成后"下一步"按钮可点击
 * - Step1 不填时"下一步"禁用
 * - 进入 Step2 显示声纹录音界面
 */
import { render, screen, fireEvent } from '@testing-library/react';

// ========== Mock 依赖 ==========

jest.mock('@/services/memberService', () => ({
  createMember: jest.fn(),
}));

jest.mock('@/utils/crypto', () => ({
  sm4Encrypt: (text: string) => `encrypted_${text}`,
}));

// Mock VoiceprintRecorder 以隔离测试
jest.mock('@/components/members/VoiceprintRecorder', () => ({
  VoiceprintRecorder: ({ onRecordingComplete }: { onRecordingComplete: (blob: Blob) => void; disabled?: boolean }) => (
    <div data-testid="voiceprint-recorder">
      <button
        onClick={() => onRecordingComplete(new Blob(['audio'], { type: 'audio/webm' }))}
        data-testid="mock-record-btn"
      >
        模拟录音完成
      </button>
    </div>
  ),
}));

import { CreateMemberWizard } from '@/components/members/CreateMemberWizard';

// ========== 测试辅助 ==========

const defaultProps = {
  isOpen: true,
  onClose: jest.fn(),
  onCreated: jest.fn(),
};

function renderWizard(props = {}) {
  return render(<CreateMemberWizard {...defaultProps} {...props} />);
}

// ========== 测试用例 ==========

beforeEach(() => {
  jest.clearAllMocks();
});

describe('CreateMemberWizard (T049)', () => {
  // ─── 渲染控制 ───

  describe('渲染控制', () => {
    it('isOpen=true 时应渲染向导', () => {
      renderWizard();

      expect(screen.getByText('添加用户')).toBeInTheDocument();
    });

    it('isOpen=false 时不应渲染', () => {
      const { container } = renderWizard({ isOpen: false });

      expect(container.innerHTML).toBe('');
    });
  });

  // ─── Step1: 基本信息 ───

  describe('Step1 显示类型选择 + 用户名密码输入', () => {
    it('应显示"成员"和"访客"类型选择按钮', () => {
      renderWizard();

      expect(screen.getByText('成员')).toBeInTheDocument();
      expect(screen.getByText('访客')).toBeInTheDocument();
    });

    it('应显示类型描述文本', () => {
      renderWizard();

      expect(screen.getByText('长期家庭成员')).toBeInTheDocument();
      expect(screen.getByText('临时使用')).toBeInTheDocument();
    });

    it('应显示用户名输入框', () => {
      renderWizard();

      expect(screen.getByPlaceholderText('3-50位字母数字下划线')).toBeInTheDocument();
    });

    it('应显示密码输入框', () => {
      renderWizard();

      expect(screen.getByPlaceholderText('6-50位密码')).toBeInTheDocument();
    });

    it('应显示用户类型标签', () => {
      renderWizard();

      expect(screen.getByText('用户类型')).toBeInTheDocument();
    });

    it('应显示步骤指示器', () => {
      renderWizard();

      expect(screen.getByText('1')).toBeInTheDocument();
      expect(screen.getByText('2')).toBeInTheDocument();
    });
  });

  // ─── Step1: "下一步"按钮状态 ───

  describe('Step1 "下一步"按钮状态', () => {
    it('未填写时"下一步"按钮应禁用', () => {
      renderWizard();

      const nextButton = screen.getByRole('button', { name: '下一步' });
      expect(nextButton).toBeDisabled();
    });

    it('仅填写用户名时"下一步"应禁用', () => {
      renderWizard();

      fireEvent.change(screen.getByPlaceholderText('3-50位字母数字下划线'), {
        target: { value: 'testuser' },
      });

      const nextButton = screen.getByRole('button', { name: '下一步' });
      expect(nextButton).toBeDisabled();
    });

    it('仅填写密码时"下一步"应禁用', () => {
      renderWizard();

      fireEvent.change(screen.getByPlaceholderText('6-50位密码'), {
        target: { value: 'password123' },
      });

      const nextButton = screen.getByRole('button', { name: '下一步' });
      expect(nextButton).toBeDisabled();
    });

    it('用户名格式不合法时"下一步"应禁用', () => {
      renderWizard();

      // 用户名含中文（不符合 /^[a-zA-Z0-9_]{3,50}$/）
      fireEvent.change(screen.getByPlaceholderText('3-50位字母数字下划线'), {
        target: { value: '中文名' },
      });
      fireEvent.change(screen.getByPlaceholderText('6-50位密码'), {
        target: { value: 'password123' },
      });

      const nextButton = screen.getByRole('button', { name: '下一步' });
      expect(nextButton).toBeDisabled();
    });

    it('密码不足 6 位时"下一步"应禁用', () => {
      renderWizard();

      fireEvent.change(screen.getByPlaceholderText('3-50位字母数字下划线'), {
        target: { value: 'testuser' },
      });
      fireEvent.change(screen.getByPlaceholderText('6-50位密码'), {
        target: { value: '12345' },
      });

      const nextButton = screen.getByRole('button', { name: '下一步' });
      expect(nextButton).toBeDisabled();
    });

    it('用户名和密码都合法时"下一步"按钮应可点击', () => {
      renderWizard();

      fireEvent.change(screen.getByPlaceholderText('3-50位字母数字下划线'), {
        target: { value: 'testuser' },
      });
      fireEvent.change(screen.getByPlaceholderText('6-50位密码'), {
        target: { value: 'password123' },
      });

      const nextButton = screen.getByRole('button', { name: '下一步' });
      expect(nextButton).not.toBeDisabled();
    });
  });

  // ─── Step2: 声纹录音界面 ───

  describe('Step2 声纹录音界面', () => {
    it('点击"下一步"后应进入 Step2 显示声纹录音界面', () => {
      renderWizard();

      // 填写 Step1
      fireEvent.change(screen.getByPlaceholderText('3-50位字母数字下划线'), {
        target: { value: 'testuser' },
      });
      fireEvent.change(screen.getByPlaceholderText('6-50位密码'), {
        target: { value: 'password123' },
      });

      // 点击下一步
      fireEvent.click(screen.getByRole('button', { name: '下一步' }));

      // 应显示 Step2 标题和声纹录音组件
      expect(screen.getByText('声纹录音')).toBeInTheDocument();
      expect(screen.getByTestId('voiceprint-recorder')).toBeInTheDocument();
    });

    it('Step2 应显示"上一步"和"提交创建"按钮', () => {
      renderWizard();

      // 进入 Step2
      fireEvent.change(screen.getByPlaceholderText('3-50位字母数字下划线'), {
        target: { value: 'testuser' },
      });
      fireEvent.change(screen.getByPlaceholderText('6-50位密码'), {
        target: { value: 'password123' },
      });
      fireEvent.click(screen.getByRole('button', { name: '下一步' }));

      expect(screen.getByRole('button', { name: '上一步' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '提交创建' })).toBeInTheDocument();
    });

    it('未录音时"提交创建"按钮应禁用', () => {
      renderWizard();

      // 进入 Step2
      fireEvent.change(screen.getByPlaceholderText('3-50位字母数字下划线'), {
        target: { value: 'testuser' },
      });
      fireEvent.change(screen.getByPlaceholderText('6-50位密码'), {
        target: { value: 'password123' },
      });
      fireEvent.click(screen.getByRole('button', { name: '下一步' }));

      const submitButton = screen.getByRole('button', { name: '提交创建' });
      expect(submitButton).toBeDisabled();
    });

    it('点击"上一步"应返回 Step1', () => {
      renderWizard();

      // 进入 Step2
      fireEvent.change(screen.getByPlaceholderText('3-50位字母数字下划线'), {
        target: { value: 'testuser' },
      });
      fireEvent.change(screen.getByPlaceholderText('6-50位密码'), {
        target: { value: 'password123' },
      });
      fireEvent.click(screen.getByRole('button', { name: '下一步' }));

      // 点击上一步
      fireEvent.click(screen.getByRole('button', { name: '上一步' }));

      // 应回到 Step1，显示"添加用户"标题
      expect(screen.getByText('添加用户')).toBeInTheDocument();
      expect(screen.queryByTestId('voiceprint-recorder')).not.toBeInTheDocument();
    });
  });

  // ─── 类型切换 ───

  describe('类型切换', () => {
    it('默认选中"成员"类型', () => {
      renderWizard();

      // 成员按钮应有选中样式（border-primary-500）
      const memberButton = screen.getByText('成员').closest('button');
      expect(memberButton?.className).toContain('border-primary-500');
    });

    it('点击"访客"应切换选中类型', () => {
      renderWizard();

      const guestButton = screen.getByText('访客').closest('button');
      fireEvent.click(guestButton!);

      // 访客按钮应有选中样式（border-amber-500）
      expect(guestButton?.className).toContain('border-amber-500');
    });
  });
});
