/**
 * 文档解析 Hook (T043a)
 *
 * 管理文档解析生命周期:
 * 1. 调用 POST /documents/parse/ 创建任务
 * 2. 通过 SSE 事件 doc_parse_progress 监听进度
 * 3. 完成后获取 Markdown 结果
 *
 * 进度事件通过 window CustomEvent 分发（useAuth.tsx 中注册）
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { createDocParseTask, getDocParseResult } from '@/services/mediaApi';

/** 解析状态 */
export type DocParseStatus = 'idle' | 'pending' | 'processing' | 'completed' | 'failed';

/** 解析进度 */
interface DocParseProgress {
  current: number;
  total: number;
}

/** Hook 返回值 */
interface UseDocParseReturn {
  /** 当前解析状态 */
  status: DocParseStatus;
  /** 解析进度 */
  progress: DocParseProgress | null;
  /** 解析结果（Markdown 文本） */
  result: string | null;
  /** 错误信息 */
  error: string | null;
  /** 发起解析 */
  parse: (attachmentUuid: string, pages?: string) => Promise<void>;
  /** 重置状态 */
  reset: () => void;
  /** 状态文本（用于 UI 显示） */
  statusText: string;
}

/** 最大结果字符数（FR-034, T003 定义） */
const DOC_PARSE_MAX_RESULT_LENGTH = 8000;

export function useDocParse(): UseDocParseReturn {
  const [status, setStatus] = useState<DocParseStatus>('idle');
  const [progress, setProgress] = useState<DocParseProgress | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const taskIdRef = useRef<string | null>(null);

  // 监听 SSE doc_parse_progress 事件
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (!detail || !taskIdRef.current) return;
      if (detail.task_id !== taskIdRef.current) return;

      const eventStatus = detail.status as string;

      if (eventStatus === 'pending') {
        setStatus('pending');
      } else if (eventStatus === 'processing') {
        setStatus('processing');
        if (detail.progress) {
          setProgress({
            current: detail.progress.current ?? 0,
            total: detail.progress.total ?? 0,
          });
        }
      } else if (eventStatus === 'completed') {
        setStatus('completed');
        // 自动获取结果
        fetchResult(taskIdRef.current);
      } else if (eventStatus === 'failed') {
        setStatus('failed');
        setError(detail.error_message || '文档解析失败');
      }
    };

    window.addEventListener('doc_parse_progress', handler);
    return () => window.removeEventListener('doc_parse_progress', handler);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 获取解析结果 */
  const fetchResult = useCallback(async (taskId: string) => {
    try {
      const response = await getDocParseResult(taskId);
      let content = response.data?.content ?? '';
      if (content.length > DOC_PARSE_MAX_RESULT_LENGTH) {
        content = content.slice(0, DOC_PARSE_MAX_RESULT_LENGTH) + '\n\n[内容已截断]';
      }
      setResult(content);
    } catch (err) {
      setError((err as Error).message || '获取解析结果失败');
      setStatus('failed');
    }
  }, []);

  /** 发起文档解析 */
  const parse = useCallback(async (attachmentUuid: string, pages?: string) => {
    setStatus('pending');
    setProgress(null);
    setResult(null);
    setError(null);

    try {
      const response = await createDocParseTask(attachmentUuid, pages);
      const taskId = response.data?.task_id;
      if (!taskId) {
        throw new Error('未获取到解析任务 ID');
      }
      taskIdRef.current = taskId;
    } catch (err) {
      const errorMsg = (err as Error).message || '创建解析任务失败';
      // 特殊处理 E6006 页数超限
      if (errorMsg.includes('PAGE_LIMIT_EXCEEDED') || errorMsg.includes('E6006')) {
        setError('文档页数超过限制（最大 200 页），请使用 pages 参数指定范围或上传更短文档');
      } else {
        setError(errorMsg);
      }
      setStatus('failed');
    }
  }, []);

  /** 重置状态 */
  const reset = useCallback(() => {
    setStatus('idle');
    setProgress(null);
    setResult(null);
    setError(null);
    taskIdRef.current = null;
  }, []);

  /** 状态文本 */
  const statusText = (() => {
    switch (status) {
      case 'idle':
        return '';
      case 'pending':
        return '等待解析...';
      case 'processing':
        return progress
          ? `解析中 ${progress.current}/${progress.total} 页`
          : '解析中...';
      case 'completed':
        return result ? '解析完成' : '获取结果中...';
      case 'failed':
        return error || '解析失败';
    }
  })();

  return { status, progress, result, error, parse, reset, statusText };
}
