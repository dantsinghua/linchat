/**
 * 用户活动监听 Hook
 *
 * 参考:
 * - rule-model.md#R_TOKEN_003 Token双重过期规则
 *
 * 用户活动定义（刷新 idle_timeout）:
 * - 包括：页面点击、API 请求、页面刷新、浏览器回退等
 * - 不包括：系统响应（如大模型完成回复）
 *
 * 功能:
 * - 监听用户活动事件
 * - 定期向后端发送心跳，触发 Token TTL 刷新
 */
'use client';

import { useCallback, useEffect, useRef } from 'react';

// 活动事件类型
const ACTIVITY_EVENTS = [
  'mousedown',
  'mousemove',
  'keydown',
  'scroll',
  'touchstart',
  'click',
];

// 心跳间隔（毫秒）- 5分钟发送一次心跳
const HEARTBEAT_INTERVAL = 5 * 60 * 1000;

// 活动防抖间隔（毫秒）- 用户活动后等待1秒再标记
const ACTIVITY_DEBOUNCE = 1000;

// API 基础 URL (与 api.ts 保持一致)
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1';

interface UseActivityTrackerOptions {
  enabled?: boolean;
  onActivity?: () => void;
  heartbeatUrl?: string;
}

/**
 * 用户活动监听 Hook
 *
 * 监听用户活动并定期向后端发送心跳，触发 Token TTL 刷新
 */
export function useActivityTracker({
  enabled = true,
  onActivity,
  heartbeatUrl = `${API_BASE_URL}/auth/me`,
}: UseActivityTrackerOptions = {}) {
  const lastActivityRef = useRef<number>(Date.now());
  const activityTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const heartbeatIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const isActiveRef = useRef(false);

  /**
   * 记录用户活动
   */
  const recordActivity = useCallback(() => {
    lastActivityRef.current = Date.now();
    isActiveRef.current = true;

    // 清除之前的防抖定时器
    if (activityTimeoutRef.current) {
      clearTimeout(activityTimeoutRef.current);
    }

    // 设置新的防抖定时器
    activityTimeoutRef.current = setTimeout(() => {
      isActiveRef.current = false;
    }, ACTIVITY_DEBOUNCE);

    // 触发回调
    onActivity?.();
  }, [onActivity]);

  /**
   * 发送心跳请求
   *
   * 仅在用户有活动时发送，触发后端 Token TTL 刷新
   */
  const sendHeartbeat = useCallback(async () => {
    // 检查最近是否有用户活动（5分钟内）
    const now = Date.now();
    const timeSinceLastActivity = now - lastActivityRef.current;

    if (timeSinceLastActivity > HEARTBEAT_INTERVAL) {
      // 超过心跳间隔没有活动，不发送心跳
      return;
    }

    try {
      // 发送心跳请求（GET /auth/me）
      // 这会触发后端 Token TTL 刷新
      await fetch(heartbeatUrl, {
        method: 'GET',
        credentials: 'include',
      });
    } catch {
      // 忽略心跳失败
      // 如果 Token 已过期，会在下次 API 调用时处理
    }
  }, [heartbeatUrl]);

  /**
   * 开始监听
   */
  const startTracking = useCallback(() => {
    // 添加事件监听
    ACTIVITY_EVENTS.forEach((event) => {
      window.addEventListener(event, recordActivity, { passive: true });
    });

    // 监听页面可见性变化
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        recordActivity();
      }
    });

    // 启动心跳定时器
    heartbeatIntervalRef.current = setInterval(sendHeartbeat, HEARTBEAT_INTERVAL);

    // 立即发送一次心跳
    sendHeartbeat();
  }, [recordActivity, sendHeartbeat]);

  /**
   * 停止监听
   */
  const stopTracking = useCallback(() => {
    // 移除事件监听
    ACTIVITY_EVENTS.forEach((event) => {
      window.removeEventListener(event, recordActivity);
    });

    // 清除定时器
    if (activityTimeoutRef.current) {
      clearTimeout(activityTimeoutRef.current);
    }
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current);
    }
  }, [recordActivity]);

  /**
   * 组件挂载/卸载时处理
   */
  useEffect(() => {
    if (enabled) {
      startTracking();
    }

    return () => {
      stopTracking();
    };
  }, [enabled, startTracking, stopTracking]);

  return {
    lastActivity: () => lastActivityRef.current,
    isActive: () => isActiveRef.current,
    recordActivity,
  };
}
