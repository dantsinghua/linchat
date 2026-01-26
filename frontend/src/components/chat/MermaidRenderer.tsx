/**
 * Mermaid 图表渲染组件
 *
 * 参考:
 * - spec.md US2场景4 - Mermaid 流程图渲染
 * - spec.md SC-006 - 渲染时间 < 500ms
 */
'use client';

import { memo, useEffect, useId, useState } from 'react';
import mermaid from 'mermaid';

interface MermaidRendererProps {
  chart: string;
  className?: string;
}

// 初始化 Mermaid 配置
mermaid.initialize({
  startOnLoad: false, // 手动触发渲染
  theme: 'default',
  securityLevel: 'loose', // 允许点击事件等
  flowchart: {
    useMaxWidth: true,
    htmlLabels: true,
  },
  sequence: {
    useMaxWidth: true,
  },
});

/**
 * Mermaid 图表渲染组件
 *
 * 在流式响应完成后渲染 Mermaid 图表
 */
export const MermaidRenderer = memo(function MermaidRenderer({
  chart,
  className = '',
}: MermaidRendererProps) {
  const [svg, setSvg] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const id = useId().replace(/:/g, '-'); // Mermaid 不支持冒号

  useEffect(() => {
    let mounted = true;

    async function renderChart() {
      if (!chart.trim()) {
        setIsLoading(false);
        return;
      }

      setIsLoading(true);
      setError(null);

      try {
        // 验证语法
        const isValid = await mermaid.parse(chart);
        if (!isValid && !mounted) return;

        // 渲染图表
        const { svg: renderedSvg } = await mermaid.render(`mermaid-${id}`, chart);

        if (mounted) {
          setSvg(renderedSvg);
          setIsLoading(false);
        }
      } catch (err) {
        if (mounted) {
          setError((err as Error).message || '图表渲染失败');
          setIsLoading(false);
        }
      }
    }

    renderChart();

    return () => {
      mounted = false;
    };
  }, [chart, id]);

  if (isLoading) {
    return (
      <div className={`flex items-center justify-center p-4 ${className}`}>
        <div className="flex items-center gap-2 text-gray-500">
          <svg
            className="h-5 w-5 animate-spin"
            xmlns="http://www.w3.org/2000/svg"
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
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
          <span className="text-sm">正在渲染图表...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={`rounded-lg border border-red-200 bg-red-50 p-4 ${className}`}>
        <div className="flex items-start gap-2">
          <svg
            className="h-5 w-5 flex-shrink-0 text-red-500"
            fill="currentColor"
            viewBox="0 0 20 20"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
              clipRule="evenodd"
            />
          </svg>
          <div>
            <p className="text-sm font-medium text-red-800">图表渲染失败</p>
            <p className="mt-1 text-xs text-red-600">{error}</p>
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-red-500 hover:underline">
                查看源代码
              </summary>
              <pre className="mt-2 overflow-x-auto rounded bg-red-100 p-2 text-xs text-red-800">
                {chart}
              </pre>
            </details>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`mermaid-container overflow-x-auto rounded-lg bg-white p-4 ${className}`}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
});
