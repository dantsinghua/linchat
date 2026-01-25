'use client';

/**
 * 登录页面
 *
 * 参考:
 * - process-model.md#一、用户登录流程（P_AUTH_001）
 * - behavior-model.md#1.2 用户登录（B_AUTH_002）
 */
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { LoginForm } from '@/components/auth/LoginForm';
import { useAuth } from '@/hooks/useAuth';

function LoginContent() {
  const searchParams = useSearchParams();
  const { onLoginSuccess } = useAuth();

  // 获取登录后重定向的目标 URL
  const redirectUrl = searchParams.get('redirect') || '/chat';

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-white px-4">
      <div className="w-full max-w-md">
        {/* Logo 和标题 */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl mb-4">
            <svg
              className="w-10 h-10 text-white"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
              />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-gray-800">LinChat</h1>
          <p className="text-gray-500 mt-1">大模型聊天平台</p>
        </div>

        {/* 登录表单 */}
        <div className="bg-white rounded-xl shadow-lg p-8">
          <h2 className="text-xl font-semibold text-gray-800 mb-6 text-center">
            账号登录
          </h2>
          <LoginForm
            onLoginSuccess={onLoginSuccess}
            redirectUrl={redirectUrl}
          />
        </div>

        {/* 底部信息 */}
        <div className="text-center mt-6 text-sm text-gray-400">
          <p>Powered by LangGraph + vLLM</p>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}
