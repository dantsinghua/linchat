/**
 * Markdown 渲染组件
 *
 * 参考:
 * - spec.md US2场景3 - Markdown 实时渲染
 * - 支持格式：标题、列表、表格、代码块、加粗、斜体、删除线
 * - 扩展格式：下划线（通过 rehype-raw 支持 <u> 标签）
 */
'use client';

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';

import { MermaidRenderer } from './MermaidRenderer';

// 代码块高亮样式（需要在全局 CSS 中引入 highlight.js 主题）
import 'highlight.js/styles/github-dark.css';

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

/**
 * 将 [[N]] 引用标记转换为上标 HTML
 * 搜索工具返回编号结果，LLM 在回答中用 [[1]]、[[2]] 标注引用来源
 */
function preprocessCitations(text: string): string {
  return text.replace(
    /\[\[(\d+)\]\]/g,
    '<sup class="citation-ref">[$1]</sup>'
  );
}

/**
 * Markdown 渲染组件
 *
 * 功能：
 * - 支持 GFM（GitHub Flavored Markdown）
 * - 代码块语法高亮
 * - 支持 HTML 标签（如 <u> 下划线）
 * - Mermaid 图表渲染
 */
export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className = '',
}: MarkdownRendererProps) {
  return (
    <div className={`markdown-body prose prose-sm max-w-none dark:prose-invert ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, rehypeHighlight]}
        components={{
          // 自定义代码块渲染
          code({ node, className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || '');
            const language = match ? match[1] : '';

            // 检查是否是 Mermaid 图表
            if (language === 'mermaid') {
              const code = String(children).replace(/\n$/, '');
              return <MermaidRenderer chart={code} />;
            }

            // 是否是代码块（有语言标识或是 pre 的子元素）
            const isCodeBlock = language || node?.position?.start?.line !== node?.position?.end?.line;

            if (isCodeBlock) {
              return (
                <div className="relative">
                  {language && (
                    <div className="absolute right-2 top-2 text-xs text-gray-400">
                      {language}
                    </div>
                  )}
                  <code className={className} {...props}>
                    {children}
                  </code>
                </div>
              );
            }

            // 行内代码
            return (
              <code
                className="rounded bg-gray-100 px-1.5 py-0.5 text-sm dark:bg-gray-800"
                {...props}
              >
                {children}
              </code>
            );
          },

          // 自定义链接渲染（新标签页打开）
          a({ href, children, ...props }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary-600 hover:underline"
                {...props}
              >
                {children}
              </a>
            );
          },

          // 自定义表格样式
          table({ children, ...props }) {
            return (
              <div className="overflow-x-auto">
                <table className="min-w-full border-collapse" {...props}>
                  {children}
                </table>
              </div>
            );
          },

          th({ children, ...props }) {
            return (
              <th
                className="border border-gray-300 bg-gray-100 px-4 py-2 text-left font-semibold dark:border-gray-600 dark:bg-gray-700"
                {...props}
              >
                {children}
              </th>
            );
          },

          td({ children, ...props }) {
            return (
              <td
                className="border border-gray-300 px-4 py-2 dark:border-gray-600"
                {...props}
              >
                {children}
              </td>
            );
          },

          // 自定义列表样式
          ul({ children, ...props }) {
            return (
              <ul className="list-disc pl-6" {...props}>
                {children}
              </ul>
            );
          },

          ol({ children, ...props }) {
            return (
              <ol className="list-decimal pl-6" {...props}>
                {children}
              </ol>
            );
          },

          // 自定义引用块样式
          blockquote({ children, ...props }) {
            return (
              <blockquote
                className="border-l-4 border-gray-300 pl-4 italic text-gray-600 dark:border-gray-600 dark:text-gray-400"
                {...props}
              >
                {children}
              </blockquote>
            );
          },
        }}
      >
        {preprocessCitations(content)}
      </ReactMarkdown>
    </div>
  );
});
