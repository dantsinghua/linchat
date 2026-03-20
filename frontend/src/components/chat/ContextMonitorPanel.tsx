/**
 * 上下文监控面板
 *
 * 包含: MonitorSidebar、ContextStatusBar、MonitorToggleButton
 * 数据来源: SSE Event stream → window.CustomEvent('context_status')
 *
 * 参考: specs/005-context-monitoring/spec.md
 */
'use client';

import { memo, useEffect, useRef, useState } from 'react';
import type { AlertLevel, ContextStatus, MonitorData } from '@/types';
import { useChatStore } from '@/stores/chatStore';

/* ------------------------------------------------------------------ */
/*  Color Palette                                                      */
/* ------------------------------------------------------------------ */

const COLORS = {
  primary: '#3b82f6',
  green: '#22c55e',
  orange: '#f59e0b',
  red: '#ef4444',
  purple: '#8b5cf6',
  cyan: '#06b6d4',
  pink: '#ec4899',
  indigo: '#6366f1',
  slate: '#64748b',
  emerald: '#10b981',
};

const CONTEXT_COLORS: Record<string, { color: string; label: string }> = {
  system_prompt: { color: COLORS.indigo, label: 'System Prompt' },
  history: { color: COLORS.primary, label: '消息历史' },
  memories: { color: COLORS.purple, label: '记忆' },
  tool_defs: { color: COLORS.slate, label: '工具' },
  user_input: { color: COLORS.emerald, label: 'user_input' },
  tool_calls: { color: COLORS.orange, label: '工具调用' },
  tool_results: { color: COLORS.pink, label: '工具结果' },
  compaction: { color: COLORS.cyan, label: '压缩摘要' },
};

const MEMORY_TYPE_COLORS: Record<string, string> = {
  '个人喜好': COLORS.pink,
  '职业信息': COLORS.primary,
  '工作任务': COLORS.orange,
  '日常交谈': COLORS.emerald,
};

/* ------------------------------------------------------------------ */
/*  Utility                                                            */
/* ------------------------------------------------------------------ */

function formatTokens(n: number): string {
  if (n >= 100000) return `${(n / 1000).toFixed(0)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function formatTime(dateStr: string): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffDays === 0) return '今天';
  if (diffDays === 1) return '1天前';
  if (diffDays < 30) return `${diffDays}天前`;
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + '...' : s;
}

const MAX_HISTORY = 30;

/* ------------------------------------------------------------------ */
/*  useContextMonitor — 监听 CustomEvent + 维护历史                      */
/* ------------------------------------------------------------------ */

const STORAGE_KEY = 'linchat:monitor';

function loadCachedMonitor(): {
  data: MonitorData | null;
  tokenHistory: { input: number[]; output: number[] };
  contextHistory: number[];
} {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return { data: null, tokenHistory: { input: [], output: [] }, contextHistory: [] };
}

function saveCachedMonitor(
  data: MonitorData,
  tokenHistory: { input: number[]; output: number[] },
  contextHistory: number[],
) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ data, tokenHistory, contextHistory }));
  } catch { /* ignore */ }
}

export function useContextMonitor() {
  const [data, setData] = useState<MonitorData | null>(null);
  const tokenHistoryRef = useRef<{ input: number[]; output: number[] }>({
    input: [],
    output: [],
  });
  const contextHistoryRef = useRef<number[]>([]);
  const lastTokensRef = useRef<{ input: number; output: number }>({ input: 0, output: 0 });
  const isFirstEventRef = useRef(true);
  const [tokenHistory, setTokenHistory] = useState<{ input: number[]; output: number[] }>({
    input: [],
    output: [],
  });
  const [contextHistory, setContextHistory] = useState<number[]>([]);

  // 回复完成后归零输入输出 tokens
  const isGenerating = useChatStore((s) => s.isGenerating);
  const prevGeneratingRef = useRef(false);
  useEffect(() => {
    if (prevGeneratingRef.current && !isGenerating && data) {
      const zeroed: MonitorData = { ...data, input_tokens: 0, output_tokens: 0, total_tokens: 0 };
      setData(zeroed);
      saveCachedMonitor(zeroed, tokenHistory, contextHistory);
    }
    prevGeneratingRef.current = isGenerating;
  }, [isGenerating]); // eslint-disable-line react-hooks/exhaustive-deps

  // 客户端 mount 后从 sessionStorage 恢复缓存数据（避免 SSR hydration 不匹配）
  useEffect(() => {
    const cached = loadCachedMonitor();
    if (cached.data) {
      setData(cached.data);
      tokenHistoryRef.current = {
        input: [...(cached.tokenHistory.input || [])],
        output: [...(cached.tokenHistory.output || [])],
      };
      contextHistoryRef.current = [...(cached.contextHistory || [])];
      setTokenHistory(cached.tokenHistory || { input: [], output: [] });
      setContextHistory(cached.contextHistory || []);
    }
  }, []);

  useEffect(() => {
    function handler(e: Event) {
      const status = (e as CustomEvent<ContextStatus>).detail;
      if (!status || status.type !== 'context_status') return;

      // 映射 ContextStatus → MonitorData
      const monitor: MonitorData = {
        model_name: status.model_name,
        total_tokens: status.total_tokens,
        input_tokens: status.input_tokens,
        output_tokens: status.output_tokens,
        breakdown: status.breakdown,
        max_context_tokens: status.max_context_tokens,
        alert: status.alert,
        pct: status.pct,
        memory_types: status.memory_types,
        memory_count: status.memory_count,
        memory_records: status.memory_records,
        tool_processes: status.tool_processes,
      };
      setData(monitor);

      // 更新 token 历史数据（增量模式）
      const ih = tokenHistoryRef.current.input;
      const oh = tokenHistoryRef.current.output;

      if (isFirstEventRef.current) {
        // 首次事件：记录基准值，推入 0
        lastTokensRef.current = { input: status.input_tokens, output: status.output_tokens };
        isFirstEventRef.current = false;
        ih.push(0);
        oh.push(0);
      } else {
        // 后续事件：计算增量（新轮次检测：累计值回落时重置基准）
        if (status.input_tokens < lastTokensRef.current.input) {
          lastTokensRef.current = { input: 0, output: 0 };
        }
        const deltaInput = Math.max(0, status.input_tokens - lastTokensRef.current.input);
        const deltaOutput = Math.max(0, status.output_tokens - lastTokensRef.current.output);
        lastTokensRef.current = { input: status.input_tokens, output: status.output_tokens };
        ih.push(deltaInput);
        oh.push(deltaOutput);
      }

      if (ih.length > MAX_HISTORY) ih.shift();
      if (oh.length > MAX_HISTORY) oh.shift();
      const newTokenHistory = { input: [...ih], output: [...oh] };
      setTokenHistory(newTokenHistory);

      const ch = contextHistoryRef.current;
      ch.push(status.breakdown?.total ?? 0);
      if (ch.length > MAX_HISTORY) ch.shift();
      const newContextHistory = [...ch];
      setContextHistory(newContextHistory);

      // 持久化到 sessionStorage，刷新页面后恢复
      saveCachedMonitor(monitor, newTokenHistory, newContextHistory);
    }

    window.addEventListener('context_status', handler);
    return () => window.removeEventListener('context_status', handler);
  }, []);

  return { data, tokenHistory, contextHistory };
}

/* ------------------------------------------------------------------ */
/*  MiniLineChart                                                      */
/* ------------------------------------------------------------------ */

const MiniLineChart = memo(function MiniLineChart({
  data,
  data2,
  maxY,
  color1,
  color2,
  width = 200,
  height = 60,
}: {
  data: number[];
  data2?: number[];
  maxY: number;
  color1: string;
  color2?: string;
  width?: number;
  height?: number;
}) {
  const effectiveMax = Math.max(maxY, 1);
  const padding = { top: 4, right: 4, bottom: 4, left: 4 };
  const w = width - padding.left - padding.right;
  const h = height - padding.top - padding.bottom;

  function toPath(values: number[]): string {
    if (values.length === 0) return '';
    const step = w / Math.max(values.length - 1, 1);
    return values
      .map((v, i) => {
        const x = padding.left + i * step;
        const y = padding.top + h - (v / effectiveMax) * h;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }

  function toArea(values: number[]): string {
    if (values.length === 0) return '';
    const path = toPath(values);
    const step = w / Math.max(values.length - 1, 1);
    const lastX = padding.left + (values.length - 1) * step;
    const baseline = padding.top + h;
    return `${path} L${lastX.toFixed(1)},${baseline} L${padding.left},${baseline} Z`;
  }

  return (
    <svg width={width} height={height} className="overflow-visible">
      {[0.25, 0.5, 0.75].map((frac) => (
        <line
          key={frac}
          x1={padding.left}
          y1={padding.top + h * (1 - frac)}
          x2={padding.left + w}
          y2={padding.top + h * (1 - frac)}
          stroke="#e2e8f0"
          strokeWidth="0.5"
        />
      ))}
      {data2 && <path d={toArea(data2)} fill={color2} opacity="0.08" />}
      <path d={toArea(data)} fill={color1} opacity="0.1" />
      {data2 && (
        <path
          d={toPath(data2)}
          fill="none"
          stroke={color2}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      <path
        d={toPath(data)}
        fill="none"
        stroke={color1}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
});

/* ------------------------------------------------------------------ */
/*  StackedBar                                                         */
/* ------------------------------------------------------------------ */

const StackedBar = memo(function StackedBar({
  segments,
  total,
  height = 14,
}: {
  segments: { color: string; value: number; label: string }[];
  total: number;
  height?: number;
}) {
  const effectiveTotal = Math.max(total, 1);

  return (
    <div className="flex w-full overflow-hidden rounded-sm" style={{ height }}>
      {segments
        .filter((s) => s.value > 0)
        .map((seg, i) => (
          <div
            key={i}
            className="transition-all duration-500"
            style={{
              width: `${(seg.value / effectiveTotal) * 100}%`,
              backgroundColor: seg.color,
              minWidth: seg.value > 0 ? '2px' : '0',
            }}
            title={`${seg.label}: ${formatTokens(seg.value)}`}
          />
        ))}
    </div>
  );
});

/* ------------------------------------------------------------------ */
/*  SectionHeader                                                      */
/* ------------------------------------------------------------------ */

function SectionHeader({
  title,
  rightText,
  rightSub,
}: {
  title: string;
  rightText?: string;
  rightSub?: string;
}) {
  return (
    <div className="mb-2 flex items-baseline justify-between">
      <h3 className="text-sm font-bold text-gray-800">{title}</h3>
      {rightText && (
        <div className="text-right">
          <span className="text-sm font-semibold text-gray-700">{rightText}</span>
          {rightSub && (
            <span className="ml-1 text-xs text-gray-400">{rightSub}</span>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  MonitorSidebar                                                     */
/* ------------------------------------------------------------------ */

export const MonitorSidebar = memo(function MonitorSidebar({
  isOpen,
  data,
  tokenHistory,
  contextHistory,
}: {
  isOpen: boolean;
  data: MonitorData | null;
  tokenHistory: { input: number[]; output: number[] };
  contextHistory: number[];
}) {
  if (!data) {
    return (
      <div
        className={`
          flex h-full flex-shrink-0 flex-col border-l border-gray-200 bg-white
          transition-all duration-300 ease-out overflow-hidden
          ${isOpen ? 'w-[360px] opacity-100' : 'w-0 opacity-0 border-l-0'}
        `}
      >
        <div className="flex flex-1 items-center justify-center px-4">
          <div className="text-center">
            <svg className="mx-auto mb-3 h-10 w-10 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            <p className="text-sm text-gray-400">发送消息后</p>
            <p className="text-sm text-gray-400">将显示监控数据</p>
          </div>
        </div>
      </div>
    );
  }

  const bd = (data.breakdown ?? {}) as unknown as Record<string, number>;
  const contextSegments = Object.entries(CONTEXT_COLORS)
    .map(([key, meta]) => ({
      color: meta.color,
      value: bd[key] ?? 0,
      label: meta.label,
    }))
    .filter((s) => s.value > 0);

  const memorySegments = data.memory_types.map((mt) => ({
    color: MEMORY_TYPE_COLORS[mt.tag] ?? COLORS.slate,
    value: mt.tokens,
    label: mt.tag,
  }));

  const maxTokenHistoryVal = Math.max(
    ...(tokenHistory.input.length > 0 ? tokenHistory.input : [0]),
    ...(tokenHistory.output.length > 0 ? tokenHistory.output : [0]),
    100,
  );

  return (
    <div
      className={`
        flex h-full flex-shrink-0 flex-col border-l border-gray-200 bg-white
        transition-all duration-300 ease-out overflow-hidden
        ${isOpen ? 'w-[360px] opacity-100' : 'w-0 opacity-0 border-l-0'}
      `}
    >
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">

        {/* 大模型输入输出 */}
        <section>
          <SectionHeader title="大模型输入输出" />
          <p className="mb-1 text-xs text-gray-500">{data.model_name}</p>
          <div className="mb-2 flex items-baseline gap-3">
            <div className="text-xs text-gray-500">
              tokens: <span className="font-mono font-semibold text-gray-700">{formatTokens(data.total_tokens)}</span>
            </div>
          </div>
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-2">
            <MiniLineChart
              data={tokenHistory.input}
              data2={tokenHistory.output}
              maxY={maxTokenHistoryVal}
              color1={COLORS.primary}
              color2={COLORS.purple}
              width={320}
              height={50}
            />
          </div>
          <div className="mt-1.5 flex items-center gap-4 text-[10px] text-gray-400">
            <span className="flex items-center gap-1">
              <span className="inline-block h-1.5 w-3 rounded-sm" style={{ backgroundColor: COLORS.primary }} />
              输入 {formatTokens(data.input_tokens)}
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-1.5 w-3 rounded-sm" style={{ backgroundColor: COLORS.purple }} />
              输出 {formatTokens(data.output_tokens)}
            </span>
          </div>
        </section>

        <hr className="border-gray-100" />

        {/* 当前上下文 */}
        <section>
          <SectionHeader
            title="当前上下文"
            rightText={`最大值 ${formatTokens(data.max_context_tokens)}`}
            rightSub="tokens"
          />
          <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1">
            {contextSegments.map((seg) => (
              <span key={seg.label} className="flex items-center gap-1 text-[10px] text-gray-500">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: seg.color }}
                />
                {seg.label}
              </span>
            ))}
          </div>
          <StackedBar segments={contextSegments} total={data.max_context_tokens} />
          <div className="mt-2 rounded-lg border border-gray-100 bg-gray-50 p-2">
            <MiniLineChart
              data={contextHistory}
              maxY={data.max_context_tokens}
              color1={
                data.alert === 'critical'
                  ? COLORS.red
                  : data.alert === 'warning'
                    ? COLORS.orange
                    : COLORS.primary
              }
              width={320}
              height={40}
            />
          </div>
          <div className="mt-1 text-right text-[10px] text-gray-400">
            {formatTokens(data.breakdown.total)} / {formatTokens(data.max_context_tokens)}
            {' '}({data.pct.toFixed(1)}%)
          </div>
        </section>

        <hr className="border-gray-100" />

        {/* 当前记忆 */}
        <section>
          <SectionHeader
            title="当前记忆"
            rightText={`总计 ${data.memory_count}`}
            rightSub="条"
          />
          {memorySegments.length > 0 && (
            <>
              <StackedBar
                segments={memorySegments}
                total={memorySegments.reduce((s, m) => s + m.value, 0)}
                height={10}
              />
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
                {memorySegments.map((seg) => (
                  <span key={seg.label} className="flex items-center gap-1 text-[10px] text-gray-500">
                    <span
                      className="inline-block h-2 w-2 rounded-sm"
                      style={{ backgroundColor: seg.color }}
                    />
                    {seg.label}
                  </span>
                ))}
              </div>
            </>
          )}
          <div className="mt-2 space-y-1.5">
            {data.memory_records.slice(0, 4).map((rec) => (
              <div
                key={rec.id}
                className="flex items-start gap-2 rounded-md border border-gray-100 bg-gray-50 px-2.5 py-1.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs text-gray-700">
                    {truncate(rec.content, 30)}
                  </p>
                  <div className="mt-0.5 flex items-center gap-2">
                    <span
                      className="inline-flex rounded-sm px-1 py-0 text-[10px] font-medium"
                      style={{
                        backgroundColor: (MEMORY_TYPE_COLORS[rec.tag] ?? COLORS.slate) + '20',
                        color: MEMORY_TYPE_COLORS[rec.tag] ?? COLORS.slate,
                      }}
                    >
                      {rec.tag}
                    </span>
                  </div>
                </div>
                <span className="flex-shrink-0 text-[10px] text-gray-400">
                  {formatTime(rec.updated_at)}
                </span>
              </div>
            ))}
          </div>
        </section>

        <hr className="border-gray-100" />

        {/* 工具调用 */}
        <section>
          <SectionHeader
            title="工具调用"
            rightText={`总计 ${data.tool_processes.length}`}
            rightSub="个"
          />
          <div className="space-y-1">
            {[...data.tool_processes]
              .sort((a, b) => b.output_tokens - a.output_tokens)
              .map((proc, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between rounded-md border border-gray-100 bg-gray-50 px-2.5 py-1.5"
                >
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-gray-700">
                      {proc.name}
                    </p>
                    {proc.task && (
                      <p className="truncate text-[10px] text-gray-400">{proc.task}</p>
                    )}
                  </div>
                  <div className="flex-shrink-0 text-right">
                    <span className="text-[10px] text-gray-400">
                      {formatTokens(proc.input_tokens)} → {formatTokens(proc.output_tokens)}
                    </span>
                  </div>
                </div>
              ))}
          </div>
        </section>
      </div>
    </div>
  );
});

/* ------------------------------------------------------------------ */
/*  ContextStatusBar                                                   */
/* ------------------------------------------------------------------ */

export const ContextStatusBar = memo(function ContextStatusBar({
  pct,
  alert,
}: {
  pct: number;
  alert: AlertLevel;
}) {
  if (alert === 'normal') return null;

  const isWarning = alert === 'warning';
  const barColor = isWarning ? 'bg-amber-500' : 'bg-red-500';
  const textColor = isWarning ? 'text-amber-600' : 'text-red-600';
  const bgColor = isWarning ? 'bg-amber-50' : 'bg-red-50';

  return (
    <div className={`flex items-center gap-2 rounded px-2 py-1 ${bgColor}`}>
      <span className={`text-xs ${textColor}`}>上下文:</span>
      <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-gray-200">
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <span className={`font-mono text-xs font-medium tabular-nums ${textColor}`}>
        {Math.round(pct)}%
      </span>
      {alert === 'critical' && (
        <span className="text-[10px] text-red-500">建议开始新对话</span>
      )}
    </div>
  );
});

/* ------------------------------------------------------------------ */
/*  MonitorToggleButton                                                */
/* ------------------------------------------------------------------ */

export const MonitorToggleButton = memo(function MonitorToggleButton({
  isOpen,
  onClick,
}: {
  isOpen: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={isOpen ? '关闭监控' : '打开监控'}
      className={`
        flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-sm transition-all
        ${isOpen
          ? 'border-primary-500/40 bg-primary-50 text-primary-600'
          : 'border-gray-300 text-gray-600 hover:bg-gray-100'
        }
      `}
    >
      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
      {isOpen ? '收起' : '监控'}
    </button>
  );
});
