/**
 * 设备管理卡片组件
 *
 * 管理外部设备（智能音箱等），支持注册、删除设备和一次性展示 API Token
 * 参考: specs/009-voice-interaction/spec.md
 */
'use client';

import { memo, useCallback, useEffect, useState } from 'react';

import { getDevices, registerDevice, deleteDevice } from '@/services/voiceApi';
import type { RegisteredDevice, DeviceRegisterResponse } from '@/types/voice';

// ========== 状态徽章 ==========

function StatusBadge({ isActive }: { isActive: boolean }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs ${
        isActive
          ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
          : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'
      }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          isActive ? 'bg-green-500' : 'bg-gray-400'
        }`}
      />
      {isActive ? '活跃' : '已撤销'}
    </span>
  );
}

// ========== Token 展示对话框 ==========

interface TokenDialogProps {
  token: string;
  deviceName: string;
  onClose: () => void;
}

function TokenDialog({ token, deviceName, onClose }: TokenDialogProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // 降级：选中文本供手动复制
    }
  }, [token]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* 遮罩层 */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
      />
      {/* 对话框 */}
      <div className="relative z-10 mx-4 w-full max-w-lg rounded-xl border border-gray-200 bg-white p-6 shadow-xl dark:border-gray-700 dark:bg-gray-800">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
          设备 API Token
        </h3>
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          设备 <span className="font-medium text-gray-700 dark:text-gray-300">{deviceName}</span> 注册成功。请立即复制并安全保存此 Token，关闭后将无法再次查看。
        </p>

        {/* Token 展示区 */}
        <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-600 dark:bg-gray-900">
          <code className="block break-all font-mono text-sm text-gray-900 dark:text-gray-100 select-all">
            {token}
          </code>
        </div>

        {/* 操作按钮 */}
        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={handleCopy}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white transition-colors hover:bg-blue-700"
          >
            {copied ? '已复制' : '复制'}
          </button>
          <button
            onClick={onClose}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}

// ========== 删除确认对话框 ==========

interface DeleteConfirmDialogProps {
  deviceName: string;
  onConfirm: () => void;
  onCancel: () => void;
  isDeleting: boolean;
}

function DeleteConfirmDialog({ deviceName, onConfirm, onCancel, isDeleting }: DeleteConfirmDialogProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* 遮罩层 */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={onCancel}
      />
      {/* 对话框 */}
      <div className="relative z-10 mx-4 w-full max-w-md rounded-xl border border-gray-200 bg-white p-6 shadow-xl dark:border-gray-700 dark:bg-gray-800">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
          确认删除设备
        </h3>
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          确定要删除设备 <span className="font-medium text-gray-700 dark:text-gray-300">{deviceName}</span> 吗？删除后该设备的 API Token 将立即失效，此操作不可撤销。
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={isDeleting}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            disabled={isDeleting}
            className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white transition-colors hover:bg-red-700 disabled:opacity-50"
          >
            {isDeleting ? '删除中...' : '确认删除'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ========== 设备行 ==========

interface DeviceRowProps {
  device: RegisteredDevice;
  onDelete: (deviceUuid: string) => void;
}

function DeviceRow({ device, onDelete }: DeviceRowProps) {
  return (
    <div className="flex items-center justify-between py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-gray-900 dark:text-white">
            {device.name}
          </span>
          <StatusBadge isActive={device.isActive} />
        </div>
        <div className="mt-1 text-xs text-gray-400">
          {device.lastActiveAt
            ? `最后活跃: ${new Date(device.lastActiveAt).toLocaleString('zh-CN')}`
            : '尚未活跃'}
          <span className="ml-3">
            注册时间: {new Date(device.createdAt).toLocaleString('zh-CN')}
          </span>
        </div>
      </div>
      <button
        onClick={() => onDelete(device.deviceUuid)}
        className="ml-4 shrink-0 rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-600 transition-colors hover:bg-red-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-900/20"
      >
        删除
      </button>
    </div>
  );
}

// ========== 主组件 ==========

export const DeviceManageCard = memo(function DeviceManageCard() {
  const [devices, setDevices] = useState<RegisteredDevice[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRegistering, setIsRegistering] = useState(false);
  const [newDeviceName, setNewDeviceName] = useState('');
  const [showRegisterForm, setShowRegisterForm] = useState(false);
  const [newToken, setNewToken] = useState<DeviceRegisterResponse | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 加载设备列表
  const loadDevices = useCallback(async () => {
    try {
      setError(null);
      const res = await getDevices();
      setDevices(res.data);
    } catch {
      setError('加载设备列表失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  // 注册新设备
  const handleRegister = useCallback(async () => {
    const trimmedName = newDeviceName.trim();
    if (!trimmedName) return;

    setIsRegistering(true);
    setError(null);
    try {
      const res = await registerDevice({ name: trimmedName });
      setNewToken(res.data);
      setNewDeviceName('');
      setShowRegisterForm(false);
      // 重新加载列表
      await loadDevices();
    } catch {
      setError('注册设备失败，请重试');
    } finally {
      setIsRegistering(false);
    }
  }, [newDeviceName, loadDevices]);

  // 删除设备
  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    setError(null);
    try {
      await deleteDevice(deleteTarget);
      setDeleteTarget(null);
      await loadDevices();
    } catch {
      setError('删除设备失败，请重试');
    } finally {
      setIsDeleting(false);
    }
  }, [deleteTarget, loadDevices]);

  // 查找待删除设备的名称
  const deleteTargetDevice = deleteTarget
    ? devices.find((d) => d.deviceUuid === deleteTarget)
    : null;

  return (
    <>
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
        {/* 卡片头部 */}
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
            设备管理
          </h3>
          {!showRegisterForm && (
            <button
              onClick={() => setShowRegisterForm(true)}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white transition-colors hover:bg-blue-700"
            >
              注册新设备
            </button>
          )}
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
            {error}
          </div>
        )}

        {/* 注册表单 */}
        {showRegisterForm && (
          <div className="mb-4 rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-600 dark:bg-gray-900">
            <h4 className="mb-3 text-sm font-medium text-gray-700 dark:text-gray-300">
              注册新设备
            </h4>
            <div className="flex gap-3">
              <input
                type="text"
                value={newDeviceName}
                onChange={(e) => setNewDeviceName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newDeviceName.trim()) {
                    handleRegister();
                  }
                }}
                placeholder="输入设备名称"
                className="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-800 dark:text-white dark:placeholder-gray-500"
                disabled={isRegistering}
              />
              <button
                onClick={handleRegister}
                disabled={isRegistering || !newDeviceName.trim()}
                className="shrink-0 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white transition-colors hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isRegistering ? '注册中...' : '注册'}
              </button>
              <button
                onClick={() => {
                  setShowRegisterForm(false);
                  setNewDeviceName('');
                }}
                disabled={isRegistering}
                className="shrink-0 rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* 设备列表 */}
        {isLoading ? (
          <div className="py-8 text-center text-sm text-gray-400">
            加载中...
          </div>
        ) : devices.length === 0 ? (
          <div className="py-8 text-center text-sm text-gray-400">
            暂无已注册设备
          </div>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-700">
            {devices.map((device) => (
              <DeviceRow
                key={device.deviceUuid}
                device={device}
                onDelete={setDeleteTarget}
              />
            ))}
          </div>
        )}

        {/* 底部说明 */}
        <div className="mt-4 text-xs text-gray-400">
          注册设备后将获得 API Token，设备可通过该 Token 进行语音交互认证。
        </div>
      </div>

      {/* Token 展示对话框 */}
      {newToken && (
        <TokenDialog
          token={newToken.apiToken}
          deviceName={newToken.name}
          onClose={() => setNewToken(null)}
        />
      )}

      {/* 删除确认对话框 */}
      {deleteTarget && deleteTargetDevice && (
        <DeleteConfirmDialog
          deviceName={deleteTargetDevice.name}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
          isDeleting={isDeleting}
        />
      )}
    </>
  );
});
