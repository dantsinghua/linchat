/**
 * MediaPreview 组件测试 (T082)
 *
 * 覆盖: 图片/视频/音频/文档类型渲染、占位图显示、过期文件提示
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { MediaPreview, AttachmentList } from '@/components/chat/MediaPreview';
import type { MediaAttachment } from '@/types/media';

jest.mock('@/services/mediaApi', () => ({
  getMediaUrl: jest.fn((uuid: string) => `/api/v1/chat/media/${uuid}/`),
}));

describe('MediaPreview', () => {
  function createAttachment(
    overrides: Partial<MediaAttachment> = {}
  ): MediaAttachment {
    return {
      attachment_uuid: 'uuid-123',
      media_type: 'image',
      mime_type: 'image/jpeg',
      file_name: 'test.jpg',
      file_size: 1024,
      thumbnail_url: '',
      expires_at: '2099-01-01T00:00:00Z',
      ...overrides,
    };
  }

  describe('图片类型', () => {
    it('应渲染 img 标签', () => {
      render(<MediaPreview attachment={createAttachment()} />);
      const img = screen.getByAltText('test.jpg');
      expect(img).toBeInTheDocument();
      expect(img.tagName).toBe('IMG');
    });

    it('应使用正确的 src', () => {
      render(
        <MediaPreview
          attachment={createAttachment({ attachment_uuid: 'img-uuid' })}
        />
      );
      const img = screen.getByAltText('test.jpg');
      expect(img).toHaveAttribute('src', '/api/v1/chat/media/img-uuid/');
    });

    it('本地预览 URL 优先', () => {
      render(
        <MediaPreview
          attachment={createAttachment()}
          localPreviewUrl="blob:local-preview"
        />
      );
      const img = screen.getByAltText('test.jpg');
      expect(img).toHaveAttribute('src', 'blob:local-preview');
    });

    it('应设置 lazy loading', () => {
      render(<MediaPreview attachment={createAttachment()} />);
      const img = screen.getByAltText('test.jpg');
      expect(img).toHaveAttribute('loading', 'lazy');
    });
  });

  describe('视频类型', () => {
    it('应渲染 video 标签', () => {
      const { container } = render(
        <MediaPreview
          attachment={createAttachment({
            media_type: 'video',
            mime_type: 'video/mp4',
            file_name: 'test.mp4',
          })}
        />
      );
      const video = container.querySelector('video');
      expect(video).toBeInTheDocument();
      expect(video).toHaveAttribute('controls');
      expect(video).toHaveAttribute('preload', 'metadata');
    });

    it('应使用正确的 src', () => {
      const { container } = render(
        <MediaPreview
          attachment={createAttachment({
            media_type: 'video',
            attachment_uuid: 'vid-uuid',
          })}
        />
      );
      const video = container.querySelector('video');
      expect(video).toHaveAttribute('src', '/api/v1/chat/media/vid-uuid/');
    });
  });

  describe('音频类型', () => {
    it('应渲染 audio 元素', () => {
      const { container } = render(
        <MediaPreview
          attachment={createAttachment({
            media_type: 'audio',
            mime_type: 'audio/webm',
            file_name: 'test.webm',
          })}
        />
      );
      const audio = container.querySelector('audio');
      expect(audio).toBeInTheDocument();
      expect(audio).toHaveAttribute('controls');
    });

    it('应显示音乐图标', () => {
      const { container } = render(
        <MediaPreview
          attachment={createAttachment({
            media_type: 'audio',
            mime_type: 'audio/webm',
          })}
        />
      );
      const svg = container.querySelector('svg');
      expect(svg).toBeInTheDocument();
    });
  });

  describe('文档类型', () => {
    it('PDF 应显示 PDF 标签和文件名', () => {
      render(
        <MediaPreview
          attachment={createAttachment({
            media_type: 'document',
            mime_type: 'application/pdf',
            file_name: 'report.pdf',
          })}
        />
      );
      expect(screen.getByText('PDF')).toBeInTheDocument();
      expect(screen.getByText('report.pdf')).toBeInTheDocument();
    });

    it('非 PDF 文档应显示 DOCX 标签', () => {
      render(
        <MediaPreview
          attachment={createAttachment({
            media_type: 'document',
            mime_type:
              'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            file_name: 'doc.docx',
          })}
        />
      );
      expect(screen.getByText('DOCX')).toBeInTheDocument();
      expect(screen.getByText('doc.docx')).toBeInTheDocument();
    });
  });

  describe('过期文件', () => {
    it('is_expired 为 true 时显示"文件已过期"', () => {
      render(
        <MediaPreview attachment={createAttachment({ is_expired: true })} />
      );
      expect(screen.getByText('文件已过期')).toBeInTheDocument();
    });

    it('过期文件不应渲染 img/video/audio', () => {
      const { container } = render(
        <MediaPreview attachment={createAttachment({ is_expired: true })} />
      );
      expect(container.querySelector('img')).not.toBeInTheDocument();
      expect(container.querySelector('video')).not.toBeInTheDocument();
      expect(container.querySelector('audio')).not.toBeInTheDocument();
    });
  });

  describe('加载失败', () => {
    it('非过期图片加载失败时显示"加载失败"', () => {
      render(
        <MediaPreview
          attachment={createAttachment({ is_expired: false })}
        />
      );
      const img = screen.getByAltText('test.jpg');
      fireEvent.error(img);
      expect(screen.getByText('加载失败')).toBeInTheDocument();
    });
  });

  describe('未知类型', () => {
    it('未知媒体类型应返回 null', () => {
      const { container } = render(
        <MediaPreview
          attachment={createAttachment({
            media_type: 'unknown' as any,
          })}
        />
      );
      expect(container.firstChild).toBeNull();
    });
  });
});

describe('AttachmentList', () => {
  function createAttachment(
    overrides: Partial<MediaAttachment> = {}
  ): MediaAttachment {
    return {
      attachment_uuid: 'uuid-123',
      media_type: 'image',
      mime_type: 'image/jpeg',
      file_name: 'test.jpg',
      file_size: 1024,
      thumbnail_url: '',
      expires_at: '2099-01-01T00:00:00Z',
      ...overrides,
    };
  }

  it('空列表不渲染', () => {
    const { container } = render(<AttachmentList attachments={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('null attachments 不渲染', () => {
    const { container } = render(
      <AttachmentList attachments={null as any} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('应渲染多个附件', () => {
    const attachments = [
      createAttachment({ attachment_uuid: 'a1', file_name: 'img1.jpg' }),
      createAttachment({ attachment_uuid: 'a2', file_name: 'img2.jpg' }),
    ];
    render(<AttachmentList attachments={attachments} />);
    expect(screen.getByAltText('img1.jpg')).toBeInTheDocument();
    expect(screen.getByAltText('img2.jpg')).toBeInTheDocument();
  });
});
