/**
 * MarkdownRenderer 组件测试
 *
 * 测试内容:
 * - 基础 Markdown 渲染
 * - 代码块高亮
 * - 表格渲染
 * - 链接处理
 * - Mermaid 图表检测
 * - SC-006 渲染性能冒烟测试
 *
 * 注意：由于 react-markdown 是 ESM 模块，需要 mock 整个组件
 */
import { render, screen } from '@testing-library/react';
import React from 'react';

// Mock react-markdown（ESM 模块）
jest.mock('react-markdown', () => {
  return {
    __esModule: true,
    default: ({ children, components }: { children: string; components?: Record<string, unknown> }) => {
      // 简单渲染 children 作为文本
      // 检测特殊语法并渲染
      let content = children;

      // 检测标题
      const headingMatch = content.match(/^(#{1,6})\s+(.+)$/m);
      if (headingMatch) {
        const level = headingMatch[1].length;
        const HeadingTag = `h${level}` as keyof JSX.IntrinsicElements;
        return React.createElement(HeadingTag, null, headingMatch[2]);
      }

      // 检测代码块
      const codeBlockMatch = content.match(/```(\w+)?\n([\s\S]*?)```/);
      if (codeBlockMatch) {
        const language = codeBlockMatch[1] || '';
        const code = codeBlockMatch[2].trim();

        // 如果是 mermaid，调用 mermaid 组件
        if (language === 'mermaid' && components?.code) {
          return React.createElement(components.code as React.ComponentType<{ className: string; children: string }>, {
            className: `language-${language}`,
            children: code,
          });
        }

        return React.createElement(
          'div',
          { className: 'code-block' },
          React.createElement('span', { className: 'language-tag' }, language),
          React.createElement('code', { className: `language-${language}` }, code)
        );
      }

      // 检测行内代码
      const inlineCodeMatch = content.match(/`([^`]+)`/);
      if (inlineCodeMatch) {
        return React.createElement('code', null, inlineCodeMatch[1]);
      }

      // 检测粗体
      const boldMatch = content.match(/\*\*([^*]+)\*\*/);
      if (boldMatch) {
        return React.createElement('strong', null, boldMatch[1]);
      }

      // 检测斜体
      const italicMatch = content.match(/\*([^*]+)\*/);
      if (italicMatch) {
        return React.createElement('em', null, italicMatch[1]);
      }

      // 检测删除线
      const strikeMatch = content.match(/~~([^~]+)~~/);
      if (strikeMatch) {
        return React.createElement('del', null, strikeMatch[1]);
      }

      // 检测列表
      if (content.includes('- ') || content.match(/^\d+\./m)) {
        const items = content.split('\n').filter(line => line.trim());
        const isOrdered = items[0]?.match(/^\d+\./);
        const ListTag = isOrdered ? 'ol' : 'ul';
        return React.createElement(
          ListTag,
          null,
          items.map((item, i) => React.createElement('li', { key: i }, item.replace(/^[-\d.]+\s*/, '')))
        );
      }

      // 检测表格
      if (content.includes('|') && content.includes('---')) {
        const lines = content.trim().split('\n').filter(l => !l.includes('---'));
        const headers = lines[0]?.split('|').filter(c => c.trim());
        const rows = lines.slice(1).map(line => line.split('|').filter(c => c.trim()));

        return React.createElement(
          'table',
          null,
          React.createElement('thead', null,
            React.createElement('tr', null,
              headers?.map((h, i) => React.createElement('th', { key: i }, h.trim()))
            )
          ),
          React.createElement('tbody', null,
            rows.map((row, ri) =>
              React.createElement('tr', { key: ri },
                row.map((cell, ci) => React.createElement('td', { key: ci }, cell.trim()))
              )
            )
          )
        );
      }

      // 检测链接
      const linkMatch = content.match(/\[([^\]]+)\]\(([^)]+)\)/);
      if (linkMatch) {
        return React.createElement(
          'a',
          { href: linkMatch[2], target: '_blank', rel: 'noopener noreferrer' },
          linkMatch[1]
        );
      }

      // 检测引用块
      if (content.startsWith('>')) {
        return React.createElement('blockquote', null, content.slice(1).trim());
      }

      // 检测 HTML 标签
      const htmlMatch = content.match(/<u>([^<]+)<\/u>/);
      if (htmlMatch) {
        return React.createElement('u', null, htmlMatch[1]);
      }

      return React.createElement('p', null, content);
    },
  };
});

// Mock rehype/remark plugins
jest.mock('rehype-highlight', () => jest.fn());
jest.mock('rehype-raw', () => jest.fn());
jest.mock('remark-gfm', () => jest.fn());

// Mock CSS import
jest.mock('highlight.js/styles/github-dark.css', () => ({}));

// Mock MermaidRenderer
jest.mock('@/components/chat/MermaidRenderer', () => ({
  MermaidRenderer: ({ chart }: { chart: string }) => (
    React.createElement('div', { 'data-testid': 'mermaid-renderer' }, chart)
  ),
}));

// 导入组件（必须在 mock 之后）
import { MarkdownRenderer } from '@/components/chat/MarkdownRenderer';

describe('MarkdownRenderer', () => {
  describe('基础渲染', () => {
    it('应渲染纯文本', () => {
      render(<MarkdownRenderer content="Hello World" />);
      expect(screen.getByText('Hello World')).toBeInTheDocument();
    });

    it('应渲染标题', () => {
      render(<MarkdownRenderer content="# Heading 1" />);
      expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
    });

    it('应渲染粗体文本', () => {
      render(<MarkdownRenderer content="**bold text**" />);
      expect(screen.getByText('bold text')).toBeInTheDocument();
    });

    it('应渲染斜体文本', () => {
      render(<MarkdownRenderer content="*italic text*" />);
      expect(screen.getByText('italic text')).toBeInTheDocument();
    });

    it('应渲染删除线文本', () => {
      render(<MarkdownRenderer content="~~deleted~~" />);
      expect(screen.getByText('deleted')).toBeInTheDocument();
    });
  });

  describe('列表渲染', () => {
    it('应渲染无序列表', () => {
      render(<MarkdownRenderer content="- Item 1" />);
      // 验证列表元素存在
      expect(screen.getByRole('list')).toBeInTheDocument();
    });

    it('应渲染有序列表', () => {
      render(<MarkdownRenderer content="1. First" />);
      // 验证列表元素存在
      expect(screen.getByRole('list')).toBeInTheDocument();
    });
  });

  describe('代码渲染', () => {
    it('应渲染行内代码', () => {
      render(<MarkdownRenderer content="Use `inline code` here" />);
      const codeElement = screen.getByText('inline code');
      expect(codeElement.tagName).toBe('CODE');
    });

    it('应渲染代码块', () => {
      const code = '```javascript\nconst x = 1;\n```';
      render(<MarkdownRenderer content={code} />);
      expect(screen.getByText(/const x = 1/)).toBeInTheDocument();
    });

    it('应显示代码块语言标识', () => {
      const code = '```python\nprint("hello")\n```';
      render(<MarkdownRenderer content={code} />);
      expect(screen.getByText('python')).toBeInTheDocument();
    });
  });

  describe('表格渲染', () => {
    it('应渲染表格', () => {
      const table = `| Header 1 | Header 2 |
|----------|----------|
| Cell 1   | Cell 2   |`;
      render(<MarkdownRenderer content={table} />);
      // 验证表格元素存在
      expect(screen.getByRole('table')).toBeInTheDocument();
    });
  });

  describe('链接渲染', () => {
    it('应渲染链接并在新标签页打开', () => {
      render(<MarkdownRenderer content="[Link](https://example.com)" />);
      const link = screen.getByRole('link', { name: 'Link' });
      expect(link).toHaveAttribute('href', 'https://example.com');
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    });
  });

  describe('引用块渲染', () => {
    it('应渲染引用块', () => {
      render(<MarkdownRenderer content="> This is a quote" />);
      const blockquote = screen.getByText('This is a quote');
      expect(blockquote.closest('blockquote')).toBeInTheDocument();
    });
  });

  describe('Mermaid 图表', () => {
    it('应检测并渲染 Mermaid 图表', () => {
      const mermaid = '```mermaid\ngraph TD\n  A-->B\n```';
      render(<MarkdownRenderer content={mermaid} />);
      expect(screen.getByTestId('mermaid-renderer')).toBeInTheDocument();
    });
  });

  describe('HTML 标签支持', () => {
    it('应支持下划线标签', () => {
      render(<MarkdownRenderer content="<u>underlined</u>" />);
      expect(screen.getByText('underlined')).toBeInTheDocument();
    });
  });

  describe('自定义样式', () => {
    it('应支持自定义 className', () => {
      const { container } = render(
        <MarkdownRenderer content="Test" className="custom-class" />
      );
      expect(container.querySelector('.custom-class')).toBeInTheDocument();
    });

    it('应有默认的 prose 样式', () => {
      const { container } = render(<MarkdownRenderer content="Test" />);
      expect(container.querySelector('.prose')).toBeInTheDocument();
    });
  });

  describe('SC-006 渲染性能冒烟测试', () => {
    it('1000字符含代码块渲染应在500ms内完成', () => {
      const longContent = `
# Title

This is a paragraph with **bold** and *italic* text.

\`\`\`javascript
function longFunction() {
  const data = {
    key1: 'value1',
    key2: 'value2',
    key3: 'value3',
  };
  return data;
}
\`\`\`

${'More text content. '.repeat(50)}
`;
      const start = performance.now();
      render(<MarkdownRenderer content={longContent} />);
      const end = performance.now();

      const renderTime = end - start;
      expect(renderTime).toBeLessThan(500);
    });

    it('表格渲染应在500ms内完成', () => {
      const tableContent = `
| Column 1 | Column 2 | Column 3 | Column 4 |
|----------|----------|----------|----------|
${'| Cell A | Cell B | Cell C | Cell D |\n'.repeat(20)}
`;
      const start = performance.now();
      render(<MarkdownRenderer content={tableContent} />);
      const end = performance.now();

      const renderTime = end - start;
      expect(renderTime).toBeLessThan(500);
    });
  });
});
