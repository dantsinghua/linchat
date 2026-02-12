/**
 * MediaUploader 组件测试 (T081)
 *
 * 覆盖: 文件选择、格式校验、大小校验、多文件≤5、上传进度显示
 */
import { render, fireEvent, waitFor } from '@testing-library/react';
import { MediaUploader } from '@/components/chat/MediaUploader';
import { useUploadStore } from '@/stores/uploadStore';
import { MEDIA_LIMITS } from '@/types/media';
import { toast } from 'sonner';

jest.mock('@/services/mediaApi', () => ({
  uploadMedia: jest.fn().mockResolvedValue({
    code: 'SUCCESS',
    message: 'ok',
    data: {
      attachment_uuid: 'uuid-123',
      media_type: 'image',
      mime_type: 'image/jpeg',
      file_name: 'test.jpg',
      file_size: 1024,
      thumbnail_url: '',
      expires_at: '2099-01-01T00:00:00Z',
    },
  }),
}));

jest.mock('sonner', () => ({
  toast: { error: jest.fn() },
}));

URL.createObjectURL = jest.fn(() => 'blob:mock-preview');
URL.revokeObjectURL = jest.fn();

// Mock Audio constructor for checkAudioDuration
let audioMockDuration = 5;
(global as any).Audio = class MockAudio {
  duration = audioMockDuration;
  onloadedmetadata: (() => void) | null = null;
  onerror: (() => void) | null = null;
  set src(_val: string) {
    const self = this;
    Promise.resolve().then(() => self.onloadedmetadata?.());
  }
};

describe('MediaUploader', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    audioMockDuration = 5;
    useUploadStore.getState().reset();
  });

  function createFile(name: string, size: number, type: string): File {
    const file = new File(['x'], name, { type });
    Object.defineProperty(file, 'size', { value: size, configurable: true });
    return file;
  }

  function selectFiles(container: HTMLElement, files: File[]) {
    const input = container.querySelector('input[type="file"]')!;
    Object.defineProperty(input, 'files', { value: files, configurable: true });
    fireEvent.change(input);
  }

  describe('渲染测试', () => {
    it('应渲染隐藏的文件输入框', () => {
      const { container } = render(<MediaUploader />);
      const input = container.querySelector('input[type="file"]');
      expect(input).toBeInTheDocument();
      expect(input).toHaveClass('hidden');
    });

    it('文件输入框应支持多选', () => {
      const { container } = render(<MediaUploader />);
      const input = container.querySelector('input[type="file"]');
      expect(input).toHaveAttribute('multiple');
    });

    it('accept 属性应包含所有支持的格式', () => {
      const { container } = render(<MediaUploader />);
      const input = container.querySelector('input[type="file"]') as HTMLInputElement;
      const accept = input.getAttribute('accept') || '';
      expect(accept).toContain('image/jpeg');
      expect(accept).toContain('image/png');
      expect(accept).toContain('video/mp4');
      expect(accept).toContain('audio/webm');
      expect(accept).toContain('application/pdf');
    });

    it('disabled 状态下输入框应禁用', () => {
      const { container } = render(<MediaUploader disabled={true} />);
      const input = container.querySelector('input[type="file"]');
      expect(input).toBeDisabled();
    });
  });

  describe('格式校验', () => {
    it('不支持的文件格式应显示错误', () => {
      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('test.exe', 1024, 'application/x-executable')]);

      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('不支持的文件格式'),
        expect.objectContaining({ description: expect.any(String) })
      );
    });

    it('支持的图片格式不应报错', () => {
      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('photo.jpg', 1024, 'image/jpeg')]);

      expect(toast.error).not.toHaveBeenCalled();
    });

    it('支持的文档格式不应报错', () => {
      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('doc.pdf', 1024, 'application/pdf')]);

      expect(toast.error).not.toHaveBeenCalled();
    });
  });

  describe('大小校验', () => {
    it('图片超过 10MB 应显示错误', () => {
      const { container } = render(<MediaUploader />);
      selectFiles(container, [
        createFile('big.jpg', MEDIA_LIMITS.MAX_IMAGE_SIZE + 1, 'image/jpeg'),
      ]);

      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('超过大小限制'),
        expect.any(Object)
      );
    });

    it('文档超过 10MB 应显示错误', () => {
      const { container } = render(<MediaUploader />);
      selectFiles(container, [
        createFile('big.pdf', MEDIA_LIMITS.MAX_DOCUMENT_SIZE + 1, 'application/pdf'),
      ]);

      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('超过大小限制'),
        expect.any(Object)
      );
    });

    it('大小在限制内不应报错', () => {
      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('ok.jpg', 5 * 1024 * 1024, 'image/jpeg')]);

      expect(toast.error).not.toHaveBeenCalled();
    });
  });

  describe('多文件限制（≤5）', () => {
    it('选择超过 5 个文件应显示错误', () => {
      const { container } = render(<MediaUploader />);
      const files = Array.from({ length: 6 }, (_, i) =>
        createFile(`file${i}.jpg`, 1024, 'image/jpeg')
      );
      selectFiles(container, files);

      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('最多上传'));
    });

    it('选择 5 个文件应通过', () => {
      const { container } = render(<MediaUploader />);
      const files = Array.from({ length: 5 }, (_, i) =>
        createFile(`file${i}.jpg`, 1024, 'image/jpeg')
      );
      selectFiles(container, files);

      expect(toast.error).not.toHaveBeenCalled();
    });

    it('已有文件时不应超过总数限制', async () => {
      const { container } = render(<MediaUploader />);
      // 先添加 3 个文件
      selectFiles(
        container,
        Array.from({ length: 3 }, (_, i) =>
          createFile(`first${i}.jpg`, 1024, 'image/jpeg')
        )
      );

      await waitFor(() => {
        expect(useUploadStore.getState().tasks.length).toBe(3);
      });

      // 再添加 3 个（总数 6 超限）
      selectFiles(
        container,
        Array.from({ length: 3 }, (_, i) =>
          createFile(`second${i}.jpg`, 1024, 'image/jpeg')
        )
      );

      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('最多上传'));
    });
  });

  describe('上传进度显示', () => {
    it('上传后应在 store 中创建任务', async () => {
      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('test.jpg', 1024, 'image/jpeg')]);

      await waitFor(() => {
        expect(useUploadStore.getState().tasks.length).toBe(1);
      });
    });

    it('上传后应显示预览卡片', async () => {
      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('test.jpg', 1024, 'image/jpeg')]);

      await waitFor(() => {
        const tiles = container.querySelectorAll('[title="test.jpg"]');
        expect(tiles.length).toBe(1);
      });
    });

    it('应调用 uploadMedia 上传文件', async () => {
      const { uploadMedia } = require('@/services/mediaApi');
      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('test.jpg', 1024, 'image/jpeg')]);

      await waitFor(() => {
        expect(uploadMedia).toHaveBeenCalled();
      });
    });
  });

  describe('音频时长校验', () => {
    it('音频时长不足 1 秒应显示错误', async () => {
      audioMockDuration = 0.5;
      // Re-create the mock with updated duration
      (global as any).Audio = class {
        duration = 0.5;
        onloadedmetadata: (() => void) | null = null;
        onerror: (() => void) | null = null;
        set src(_val: string) {
          const self = this;
          Promise.resolve().then(() => self.onloadedmetadata?.());
        }
      };

      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('short.wav', 1024, 'audio/wav')]);

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith(
          expect.stringContaining('音频时长过短')
        );
      });
    });

    it('音频时长超过 60 秒应显示错误', async () => {
      (global as any).Audio = class {
        duration = 61;
        onloadedmetadata: (() => void) | null = null;
        onerror: (() => void) | null = null;
        set src(_val: string) {
          const self = this;
          Promise.resolve().then(() => self.onloadedmetadata?.());
        }
      };

      const { container } = render(<MediaUploader />);
      selectFiles(container, [createFile('long.mp3', 1024, 'audio/mpeg')]);

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith(
          expect.stringContaining('音频时长不能超过')
        );
      });
    });
  });
});
