/**
 * VoiceMessageBubble 组件测试
 *
 * 测试语音消息气泡的转写文字显示、音频播放器渲染和过期状态处理。
 */
import { render, screen } from '@testing-library/react';

import type { Message } from '@/types';
import type { MediaAttachment } from '@/types/media';

// Mock AudioPlayer 组件
jest.mock('@/components/chat/AudioPlayer', () => ({
  AudioPlayer: (props: { src: string; duration?: number }) => (
    <div
      data-testid="audio-player"
      data-src={props.src}
      data-duration={props.duration}
    />
  ),
}));

// Mock getMediaUrl
jest.mock('@/services/mediaApi', () => ({
  getMediaUrl: (uuid: string) => `https://mock-media.test/${uuid}`,
}));

// 导入被测组件（必须在 mock 之后）
import { VoiceMessageBubble } from '../VoiceMessageBubble';

// ============ 辅助函数 ============

/** 生成基础消息对象 */
function createMessage(overrides?: Partial<Message>): Message {
  return {
    message_id: 1,
    message_uuid: 'msg-001',
    role: 'user',
    content: '这是一段转写文字',
    status: 1,
    sequence: 1,
    created_time: '2026-02-24T10:00:00Z',
    is_voice: true,
    ...overrides,
  };
}

/** 生成音频附件 */
function createAudioAttachment(
  overrides?: Partial<MediaAttachment>
): MediaAttachment {
  return {
    attachment_uuid: 'audio-001',
    media_type: 'audio',
    mime_type: 'audio/webm',
    file_name: 'recording.webm',
    file_size: 102400,
    duration_seconds: 5.2,
    expires_at: '2026-03-01T00:00:00Z',
    is_expired: false,
    ...overrides,
  };
}

// ============ 测试用例 ============

describe('VoiceMessageBubble', () => {
  // ---------- 语音标签 ----------

  describe('语音消息标签', () => {
    it('应始终显示 "[语音消息]" 标签', () => {
      const message = createMessage();
      render(<VoiceMessageBubble message={message} isUser={true} />);

      expect(screen.getByText('[语音消息]')).toBeInTheDocument();
    });

    it('无附件、无转写时也应显示标签', () => {
      const message = createMessage({ content: '', attachments: [] });
      render(<VoiceMessageBubble message={message} isUser={false} />);

      expect(screen.getByText('[语音消息]')).toBeInTheDocument();
    });
  });

  // ---------- 转写文字 ----------

  describe('转写文字显示', () => {
    it('有转写文字时应显示内容', () => {
      const message = createMessage({ content: '你好，请问今天天气如何？' });
      render(<VoiceMessageBubble message={message} isUser={true} />);

      expect(
        screen.getByText('你好，请问今天天气如何？')
      ).toBeInTheDocument();
    });

    it('内容为空时不应显示转写文字区域', () => {
      const message = createMessage({ content: '' });
      const { container } = render(
        <VoiceMessageBubble message={message} isUser={true} />
      );

      // 转写文字使用 <p> 标签
      const paragraphs = container.querySelectorAll('p');
      expect(paragraphs.length).toBe(0);
    });

    it('内容为 "[语音输入]" 占位符时不应显示转写文字', () => {
      const message = createMessage({ content: '[语音输入]' });
      const { container } = render(
        <VoiceMessageBubble message={message} isUser={true} />
      );

      const paragraphs = container.querySelectorAll('p');
      expect(paragraphs.length).toBe(0);
    });

    it('内容仅包含空白字符时不应显示转写文字', () => {
      const message = createMessage({ content: '   ' });
      const { container } = render(
        <VoiceMessageBubble message={message} isUser={false} />
      );

      const paragraphs = container.querySelectorAll('p');
      expect(paragraphs.length).toBe(0);
    });
  });

  // ---------- 音频播放器 ----------

  describe('音频播放器', () => {
    it('有未过期音频附件时应渲染 AudioPlayer', () => {
      const audioAttachment = createAudioAttachment();
      const message = createMessage({
        attachments: [audioAttachment],
      });
      render(<VoiceMessageBubble message={message} isUser={true} />);

      const player = screen.getByTestId('audio-player');
      expect(player).toBeInTheDocument();
      expect(player).toHaveAttribute(
        'data-src',
        'https://mock-media.test/audio-001'
      );
      expect(player).toHaveAttribute('data-duration', '5.2');
    });

    it('音频已过期时应显示 "音频已过期" 提示', () => {
      const audioAttachment = createAudioAttachment({ is_expired: true });
      const message = createMessage({
        attachments: [audioAttachment],
      });
      render(<VoiceMessageBubble message={message} isUser={false} />);

      expect(screen.getByText('音频已过期')).toBeInTheDocument();
      expect(screen.queryByTestId('audio-player')).not.toBeInTheDocument();
    });

    it('无音频附件时不渲染播放器和过期提示', () => {
      const message = createMessage({ attachments: [] });
      render(<VoiceMessageBubble message={message} isUser={true} />);

      expect(screen.queryByTestId('audio-player')).not.toBeInTheDocument();
      expect(screen.queryByText('音频已过期')).not.toBeInTheDocument();
    });

    it('附件类型不是 audio 时不渲染播放器', () => {
      const imageAttachment: MediaAttachment = {
        attachment_uuid: 'img-001',
        media_type: 'image',
        mime_type: 'image/png',
        file_name: 'photo.png',
        file_size: 204800,
        expires_at: '2026-03-01T00:00:00Z',
        is_expired: false,
      };
      const message = createMessage({
        attachments: [imageAttachment],
      });
      render(<VoiceMessageBubble message={message} isUser={true} />);

      expect(screen.queryByTestId('audio-player')).not.toBeInTheDocument();
    });

    it('附件为 undefined 时不渲染播放器', () => {
      const message = createMessage({ attachments: undefined });
      render(<VoiceMessageBubble message={message} isUser={true} />);

      expect(screen.queryByTestId('audio-player')).not.toBeInTheDocument();
    });
  });

  // ---------- 用户/助手样式区分 ----------

  describe('用户/助手样式区分', () => {
    it('isUser=true 时转写文字应使用白色文字', () => {
      const message = createMessage({ content: '测试文字' });
      render(<VoiceMessageBubble message={message} isUser={true} />);

      const textElement = screen.getByText('测试文字');
      expect(textElement.className).toContain('text-white');
    });

    it('isUser=false 时转写文字应使用灰色文字', () => {
      const message = createMessage({
        content: '测试文字',
        role: 'assistant',
      });
      render(<VoiceMessageBubble message={message} isUser={false} />);

      const textElement = screen.getByText('测试文字');
      expect(textElement.className).toContain('text-gray-800');
    });
  });

  // ---------- 多附件场景 ----------

  describe('多附件场景', () => {
    it('多个附件中应只使用第一个 audio 类型附件', () => {
      const imageAttachment: MediaAttachment = {
        attachment_uuid: 'img-001',
        media_type: 'image',
        mime_type: 'image/png',
        file_name: 'photo.png',
        file_size: 204800,
        expires_at: '2026-03-01T00:00:00Z',
        is_expired: false,
      };
      const audioAttachment = createAudioAttachment({
        attachment_uuid: 'audio-002',
      });
      const message = createMessage({
        attachments: [imageAttachment, audioAttachment],
      });
      render(<VoiceMessageBubble message={message} isUser={true} />);

      const player = screen.getByTestId('audio-player');
      expect(player).toHaveAttribute(
        'data-src',
        'https://mock-media.test/audio-002'
      );
    });
  });
});
