/**
 * 创建家庭成员向导
 *
 * 015-family-multiuser T026 + T047:
 * - Step 1: 成员/访客类型选择 + 用户名密码输入
 * - Step 2: 声纹录音（VoiceprintRecorder 集成）
 */
'use client';

import { memo, useCallback, useState } from 'react';

import { VoiceprintRecorder } from '@/components/members/VoiceprintRecorder';
import { createMember } from '@/services/memberService';
import { sm4Encrypt } from '@/utils/crypto';

interface CreateMemberWizardProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: () => void;
}

type MemberType = 'member' | 'guest';

export const CreateMemberWizard = memo(function CreateMemberWizard({
  isOpen,
  onClose,
  onCreated,
}: CreateMemberWizardProps) {
  const [step, setStep] = useState(1);
  const [memberType, setMemberType] = useState<MemberType>('member');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // 校验规则
  const usernameValid = /^[a-zA-Z0-9_]{3,50}$/.test(username);
  const passwordValid = password.length >= 6 && password.length <= 50;
  const canProceed = usernameValid && passwordValid;

  const resetForm = useCallback(() => {
    setStep(1);
    setMemberType('member');
    setUsername('');
    setPassword('');
    setAudioBlob(null);
    setError(null);
    setIsSubmitting(false);
  }, []);

  const handleClose = useCallback(() => {
    resetForm();
    onClose();
  }, [resetForm, onClose]);

  const handleNextStep = useCallback(() => {
    if (!canProceed) return;
    setError(null);
    setStep(2);
  }, [canProceed]);

  // 声纹录音完成回调
  const handleRecordingComplete = useCallback((blob: Blob) => {
    setAudioBlob(blob);
    setError(null);
  }, []);

  const handleSubmit = useCallback(async () => {
    if (!audioBlob) {
      setError('请先完成声纹录音');
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const formData = new FormData();
      formData.append('username', username);
      formData.append('password', sm4Encrypt(password));
      formData.append('member_type', memberType);
      formData.append('audio', audioBlob, 'voiceprint.webm');

      const response = await createMember(formData);

      if (response.code === 'SUCCESS') {
        resetForm();
        onCreated();
      } else {
        // 根据错误码显示友好提示
        const errorCode = response.code;
        if (errorCode === 'VOICEPRINT_FAILED') {
          setError('声纹注册失败，请重新录制音频');
          setAudioBlob(null);
        } else if (errorCode === 'USERNAME_EXISTS') {
          setError('用户名已存在，请返回上一步修改');
        } else {
          setError(response.message || '创建失败，请重试');
        }
      }
    } catch (err) {
      setError((err as Error).message || '网络错误，请重试');
    } finally {
      setIsSubmitting(false);
    }
  }, [username, password, memberType, audioBlob, resetForm, onCreated]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={handleClose}
    >
      <div
        className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl dark:bg-gray-800"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题栏 */}
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-800 dark:text-white">
            {step === 1 ? '添加用户' : '声纹录音'}
          </h2>
          <button
            onClick={handleClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
          >
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 步骤指示器 */}
        <div className="mb-6 flex items-center gap-2">
          <div className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${
            step >= 1 ? 'bg-primary-500 text-white' : 'bg-gray-200 text-gray-500'
          }`}>
            1
          </div>
          <div className={`h-0.5 flex-1 ${step >= 2 ? 'bg-primary-500' : 'bg-gray-200'}`} />
          <div className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${
            step >= 2 ? 'bg-primary-500 text-white' : 'bg-gray-200 text-gray-500'
          }`}>
            2
          </div>
        </div>

        {/* Step 1: 基本信息 */}
        {step === 1 && (
          <div className="space-y-4">
            {/* 类型选择 */}
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                用户类型
              </label>
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setMemberType('member')}
                  className={`rounded-xl border-2 px-4 py-3 text-center transition-colors ${
                    memberType === 'member'
                      ? 'border-primary-500 bg-primary-50 text-primary-700 dark:bg-primary-900/20 dark:text-primary-300'
                      : 'border-gray-200 text-gray-600 hover:border-gray-300 dark:border-gray-600 dark:text-gray-400 dark:hover:border-gray-500'
                  }`}
                >
                  <div className="text-lg font-semibold">成员</div>
                  <div className="mt-1 text-xs opacity-70">长期家庭成员</div>
                </button>
                <button
                  type="button"
                  onClick={() => setMemberType('guest')}
                  className={`rounded-xl border-2 px-4 py-3 text-center transition-colors ${
                    memberType === 'guest'
                      ? 'border-amber-500 bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300'
                      : 'border-gray-200 text-gray-600 hover:border-gray-300 dark:border-gray-600 dark:text-gray-400 dark:hover:border-gray-500'
                  }`}
                >
                  <div className="text-lg font-semibold">访客</div>
                  <div className="mt-1 text-xs opacity-70">临时使用</div>
                </button>
              </div>
            </div>

            {/* 用户名 */}
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                用户名
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="3-50位字母数字下划线"
                className="w-full rounded-lg border border-gray-300 px-4 py-2.5 text-sm transition-colors focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
                maxLength={50}
              />
              {username.length > 0 && !usernameValid && (
                <p className="mt-1 text-xs text-red-500">
                  用户名需3-50位，仅支持字母、数字和下划线
                </p>
              )}
            </div>

            {/* 密码 */}
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                密码
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="6-50位密码"
                className="w-full rounded-lg border border-gray-300 px-4 py-2.5 text-sm transition-colors focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20 dark:border-gray-600 dark:bg-gray-700 dark:text-white"
                maxLength={50}
              />
              {password.length > 0 && !passwordValid && (
                <p className="mt-1 text-xs text-red-500">
                  密码长度需在6-50位之间
                </p>
              )}
            </div>

            {/* 下一步按钮 */}
            <button
              onClick={handleNextStep}
              disabled={!canProceed}
              className={`mt-2 w-full rounded-xl px-4 py-3 text-sm font-medium transition-colors ${
                canProceed
                  ? 'bg-primary-500 text-white hover:bg-primary-600'
                  : 'cursor-not-allowed bg-gray-200 text-gray-400 dark:bg-gray-700 dark:text-gray-500'
              }`}
            >
              下一步
            </button>
          </div>
        )}

        {/* Step 2: 声纹录音 */}
        {step === 2 && (
          <div className="space-y-4">
            {/* 声纹录音组件 */}
            <VoiceprintRecorder
              onRecordingComplete={handleRecordingComplete}
              disabled={isSubmitting}
            />

            {/* 错误提示 */}
            {error && (
              <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600 dark:bg-red-900/20 dark:text-red-400">
                {error}
              </div>
            )}

            {/* 提交中 loading 状态 */}
            {isSubmitting && (
              <div className="flex items-center justify-center gap-2 rounded-lg bg-primary-50 px-4 py-3 dark:bg-primary-900/20">
                <svg
                  className="h-4 w-4 animate-spin text-primary-500"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                <span className="text-sm text-primary-600 dark:text-primary-400">
                  正在注册声纹...
                </span>
              </div>
            )}

            {/* 操作按钮 */}
            <div className="flex gap-3">
              <button
                onClick={() => {
                  setStep(1);
                  setError(null);
                  setAudioBlob(null);
                }}
                disabled={isSubmitting}
                className="flex-1 rounded-xl border border-gray-300 px-4 py-3 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-600 dark:text-gray-400 dark:hover:bg-gray-700"
              >
                上一步
              </button>
              <button
                onClick={handleSubmit}
                disabled={!audioBlob || isSubmitting}
                className={`flex-1 rounded-xl px-4 py-3 text-sm font-medium transition-colors ${
                  audioBlob && !isSubmitting
                    ? 'bg-primary-500 text-white hover:bg-primary-600'
                    : 'cursor-not-allowed bg-gray-200 text-gray-400 dark:bg-gray-700 dark:text-gray-500'
                }`}
              >
                {isSubmitting ? '提交中...' : '提交创建'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
});
