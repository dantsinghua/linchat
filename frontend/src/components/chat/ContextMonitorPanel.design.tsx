/**
 * 上下文监控侧边栏 — 设计稿 v2
 *
 * 视觉风格：浅色主题，与 LinChat 现有 UI 一致
 * 布局参考：Windows 任务管理器/资源管理器看板式监控
 * 刷新频率：所有数据 500ms 同步刷新
 *
 * 四个区块：
 *   CPU — 大模型输入输出（模型名、token 折线图）
 *   内存 — 当前上下文（堆叠柱状图 + 趋势折线图）
 *   硬盘 — 当前记忆（类型占比 + 记忆列表）
 *   当前进程 — 工具调用（实时列表，按输出 token 倒序）
 */
'use client';

import { memo, useState } from 'react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface TokenBreakdown {
  system_prompt: number;
  history: number;
  memories: number;
  compaction: number;
  tool_defs: number;
  tool_calls: number;
  tool_results: number;
  tool_count: number;
  user_input: number;
  total: number;
}

type AlertLevel = 'normal' | 'warning' | 'critical';

interface MemoryRecord {
  id: number;
  content: string;
  tag: string;
  updated_at: string;
  token_count: number;
}

interface ToolProcess {
  name: string;
  task: string;
  input_tokens: number;
  output_tokens: number;
}

interface MonitorData {
  // CPU
  model_name: string;
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  // 内存
  context_breakdown: TokenBreakdown;
  max_context_tokens: number; // 模型配置的完整 max_tokens
  alert: AlertLevel;
  pct: number;
  // 硬盘
  memory_types: { type: string; tokens: number; color: string }[];
  memory_records: MemoryRecord[];
  memory_total: number;
  // 当前进程
  tool_processes: ToolProcess[];
}

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
  '其他': COLORS.slate,
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

/* ------------------------------------------------------------------ */
/*  MiniLineChart — 小型折线图（纯 SVG）                                  */
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
      {/* Grid lines */}
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
      {/* Area fills */}
      {data2 && (
        <path d={toArea(data2)} fill={color2} opacity="0.08" />
      )}
      <path d={toArea(data)} fill={color1} opacity="0.1" />
      {/* Lines */}
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
/*  StackedBar — 横向堆叠柱状图                                          */
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
/*  MonitorSidebar — 主组件                                             */
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
  if (!data) return null;

  const contextSegments = Object.entries(CONTEXT_COLORS)
    .map(([key, meta]) => ({
      color: meta.color,
      value: (data.context_breakdown as unknown as Record<string, number>)[key] ?? 0,
      label: meta.label,
    }))
    .filter((s) => s.value > 0);

  const memorySegments = data.memory_types.map((mt) => ({
    color: mt.color || COLORS.slate,
    value: mt.tokens,
    label: mt.type,
  }));

  const maxTokenHistoryVal = Math.max(
    ...tokenHistory.input,
    ...tokenHistory.output,
    100
  );

  return (
    <div
      className={`
        flex h-full w-[300px] flex-shrink-0 flex-col border-l border-gray-200 bg-white
        transition-all duration-300 ease-out overflow-hidden
        ${isOpen ? 'w-[300px] opacity-100' : 'w-0 opacity-0 border-l-0'}
      `}
    >
      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">

        {/* ═══════════ CPU — 大模型输入输出 ═══════════ */}
        <section>
          <SectionHeader title="大模型输入输出" />
          <p className="mb-1 text-xs text-gray-500">{data.model_name}</p>
          <div className="mb-2 flex items-baseline gap-3">
            <div className="text-xs text-gray-500">
              tokens: <span className="font-mono font-semibold text-gray-700">{formatTokens(data.total_tokens)}</span>
            </div>
          </div>
          {/* Mini line chart */}
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-2">
            <MiniLineChart
              data={tokenHistory.input}
              data2={tokenHistory.output}
              maxY={maxTokenHistoryVal}
              color1={COLORS.primary}
              color2={COLORS.purple}
              width={260}
              height={50}
            />
          </div>
          {/* Legend */}
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

        {/* ═══════════ 内存 — 当前上下文 ═══════════ */}
        <section>
          <SectionHeader
            title="当前上下文"
            rightText={`最大值 ${formatTokens(data.max_context_tokens)}`}
            rightSub="tokens"
          />
          {/* Context legend dots */}
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
          {/* Stacked bar */}
          <StackedBar
            segments={contextSegments}
            total={data.max_context_tokens}
          />
          {/* Context trend chart */}
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
              width={260}
              height={40}
            />
          </div>
          <div className="mt-1 text-right text-[10px] text-gray-400">
            {formatTokens(data.context_breakdown.total)} / {formatTokens(data.max_context_tokens)}
            {' '}({data.pct.toFixed(1)}%)
          </div>
        </section>

        <hr className="border-gray-100" />

        {/* ═══════════ 硬盘 — 当前记忆 ═══════════ */}
        <section>
          <SectionHeader
            title="当前记忆"
            rightText={`总计 ${data.memory_total}`}
            rightSub="条"
          />
          {/* Memory type bar */}
          {memorySegments.length > 0 && (
            <>
              <StackedBar segments={memorySegments} total={data.memory_total} height={10} />
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
          {/* Memory records */}
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

        {/* ═══════════ 当前进程 — 工具调用 ═══════════ */}
        <section>
          <SectionHeader
            title="可用工具"
            rightText={`总计 ${data.tool_processes.length}`}
            rightSub="个"
          />
          <div className="space-y-1">
            {data.tool_processes
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
/*  ContextStatusBar — 输入框下方状态提示条                                */
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
      <span className={`text-xs ${textColor}`}>
        上下文:
      </span>
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
/*  MonitorToggleButton — 侧边栏切换按钮                                 */
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

/* ------------------------------------------------------------------ */
/*  默认导出 — 设计预览                                                  */
/* ------------------------------------------------------------------ */

export default function ContextMonitorPanelPreview() {
  const [isOpen, setIsOpen] = useState(true);

  // Simulated time-series data
  const inputHistory = [120, 180, 250, 300, 280, 350, 420, 500, 480, 550, 600, 650, 700, 740, 784];
  const outputHistory = [80, 150, 220, 310, 350, 400, 480, 520, 580, 640, 700, 780, 830, 870, 892];
  const contextTrend = [8000, 12000, 18000, 22000, 28000, 32000, 35000, 38000, 40000, 42000, 43500, 44200, 44800, 45200, 45662];

  const demoData: MonitorData = {
    model_name: 'deepseek-v3-1-terminus',
    total_tokens: 1844,
    input_tokens: 784,
    output_tokens: 892,
    context_breakdown: {
      system_prompt: 1200,
      history: 35000,
      memories: 3500,
      compaction: 0,
      tool_defs: 800,
      tool_calls: 200,
      tool_results: 4000,
      tool_count: 2,
      user_input: 500,
      total: 45200,
    },
    max_context_tokens: 65536,
    alert: 'warning',
    pct: 73,
    memory_types: [
      { type: '个人喜好', tokens: 5, color: COLORS.pink },
      { type: '职业信息', tokens: 8, color: COLORS.primary },
      { type: '工作任务', tokens: 6, color: COLORS.orange },
      { type: '日常交谈', tokens: 5, color: COLORS.emerald },
    ],
    memory_records: [
      { id: 1, content: '我叫安琳，是一名产品经理...', tag: '职业信息', updated_at: '2026-02-03', token_count: 120 },
      { id: 2, content: 'vllm大模型动态整存管理器...', tag: '工作任务', updated_at: '2026-02-01', token_count: 85 },
      { id: 3, content: '2月15日大年初一，提醒我...', tag: '日常交谈', updated_at: '2026-01-30', token_count: 60 },
      { id: 4, content: '每日10:30提醒我', tag: '日常交谈', updated_at: '2025-10-11', token_count: 40 },
    ],
    memory_total: 24,
    tool_processes: [
      { name: 'Brave Search API', task: '搜索', input_tokens: 120, output_tokens: 450 },
      { name: 'python REPL', task: '执行代码', input_tokens: 80, output_tokens: 320 },
      { name: 'Memory', task: '记忆检索', input_tokens: 60, output_tokens: 180 },
      { name: 'Home Assistant', task: '智能家居', input_tokens: 40, output_tokens: 90 },
    ],
  };

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Simulated chat area */}
      <div className="flex flex-1 flex-col">
        <header className="flex items-center justify-between border-b bg-white px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-500 text-white font-bold">L</div>
            <h1 className="text-xl font-semibold text-gray-800">LinChat</h1>
          </div>
          <div className="flex items-center gap-3">
            <MonitorToggleButton isOpen={isOpen} onClick={() => setIsOpen(!isOpen)} />
          </div>
        </header>
        <main className="flex flex-1 items-center justify-center">
          <p className="text-gray-400">聊天区域</p>
        </main>
        <div className="border-t bg-white p-4">
          <div className="mx-auto max-w-3xl space-y-2">
            <textarea
              className="w-full resize-none rounded-lg border border-gray-300 px-4 py-3 text-sm focus:outline-none"
              rows={1}
              placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
              readOnly
            />
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <ContextStatusBar pct={73} alert="warning" />
              </div>
              <button className="ml-2 flex h-10 w-10 items-center justify-center rounded-lg bg-blue-500 text-white">
                <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M10.894 2.553a1 1 0 00-1.788 0l-7 14a1 1 0 001.169 1.409l5-1.429A1 1 0 009 15.571V11a1 1 0 112 0v4.571a1 1 0 00.725.962l5 1.428a1 1 0 001.17-1.408l-7-14z" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Monitor Sidebar */}
      <MonitorSidebar
        isOpen={isOpen}
        data={demoData}
        tokenHistory={{ input: inputHistory, output: outputHistory }}
        contextHistory={contextTrend}
      />
    </div>
  );
}
