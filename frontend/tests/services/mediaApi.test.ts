/**
 * mediaApi / ttsApi 单元测试 (T085)
 *
 * 覆盖: 上传请求、取消推理、TTS 调用、错误处理
 */
import { getMediaUrl, uploadMedia } from '@/services/mediaApi';
import { synthesizeTTS, TTSError } from '@/services/ttsApi';

// ============ mediaApi 测试 ============

describe('mediaApi', () => {
  describe('getMediaUrl', () => {
    it('应返回正确的媒体文件 URL', () => {
      const url = getMediaUrl('test-uuid-123');
      expect(url).toContain('/chat/media/test-uuid-123/');
    });

    it('不同 UUID 应生成不同 URL', () => {
      const url1 = getMediaUrl('uuid-1');
      const url2 = getMediaUrl('uuid-2');
      expect(url1).not.toBe(url2);
    });
  });

  describe('uploadMedia', () => {
    let MockXHR: jest.Mock;
    let xhrInstance: any;

    beforeEach(() => {
      xhrInstance = {
        upload: { onprogress: null },
        onload: null as (() => void) | null,
        onerror: null as (() => void) | null,
        ontimeout: null as (() => void) | null,
        status: 200,
        responseText: '',
        withCredentials: false,
        timeout: 0,
        open: jest.fn(),
        send: jest.fn(),
      };
      MockXHR = jest.fn(() => xhrInstance);
      (global as any).XMLHttpRequest = MockXHR;
    });

    it('应使用 POST 方法和正确 URL', async () => {
      const file = new File(['test'], 'photo.jpg', { type: 'image/jpeg' });

      const promise = uploadMedia(file);

      // Simulate success response
      xhrInstance.status = 200;
      xhrInstance.responseText = JSON.stringify({
        code: 'SUCCESS',
        message: 'ok',
        data: {
          attachment_uuid: 'uuid-result',
          media_type: 'image',
          mime_type: 'image/jpeg',
          file_name: 'photo.jpg',
          file_size: 1024,
          thumbnail_url: '',
          expires_at: '2099-01-01',
        },
      });
      xhrInstance.onload?.();

      const result = await promise;
      expect(xhrInstance.open).toHaveBeenCalledWith(
        'POST',
        expect.stringContaining('/chat/media/upload/')
      );
      expect(result.data.attachment_uuid).toBe('uuid-result');
    });

    it('应携带 Cookie 认证', async () => {
      const file = new File(['test'], 'photo.jpg', { type: 'image/jpeg' });
      uploadMedia(file);

      expect(xhrInstance.withCredentials).toBe(true);
    });

    it('应调用进度回调', async () => {
      const onProgress = jest.fn();
      const file = new File(['test'], 'photo.jpg', { type: 'image/jpeg' });

      const promise = uploadMedia(file, onProgress);

      // Simulate progress event
      xhrInstance.upload.onprogress?.({
        lengthComputable: true,
        loaded: 50,
        total: 100,
      });

      expect(onProgress).toHaveBeenCalledWith(
        expect.objectContaining({
          percent: 50,
          stage: 'uploading',
        })
      );

      // Complete the upload
      xhrInstance.status = 200;
      xhrInstance.responseText = JSON.stringify({
        code: 'SUCCESS',
        message: 'ok',
        data: {
          attachment_uuid: 'uuid',
          media_type: 'image',
          mime_type: 'image/jpeg',
          file_name: 'photo.jpg',
          file_size: 1024,
          thumbnail_url: '',
          expires_at: '2099-01-01',
        },
      });
      xhrInstance.onload?.();
      await promise;
    });

    it('HTTP 错误应 reject', async () => {
      const file = new File(['test'], 'photo.jpg', { type: 'image/jpeg' });
      const promise = uploadMedia(file);

      xhrInstance.status = 400;
      xhrInstance.responseText = JSON.stringify({
        message: 'INVALID_FILE_TYPE',
      });
      xhrInstance.onload?.();

      await expect(promise).rejects.toThrow('INVALID_FILE_TYPE');
    });

    it('网络错误应 reject', async () => {
      const file = new File(['test'], 'photo.jpg', { type: 'image/jpeg' });
      const promise = uploadMedia(file);

      xhrInstance.onerror?.();

      await expect(promise).rejects.toThrow('网络错误');
    });

    it('超时应 reject', async () => {
      const file = new File(['test'], 'photo.jpg', { type: 'image/jpeg' });
      const promise = uploadMedia(file);

      xhrInstance.ontimeout?.();

      await expect(promise).rejects.toThrow('上传超时');
    });

    it('响应解析失败应 reject', async () => {
      const file = new File(['test'], 'photo.jpg', { type: 'image/jpeg' });
      const promise = uploadMedia(file);

      xhrInstance.status = 200;
      xhrInstance.responseText = 'not json';
      xhrInstance.onload?.();

      await expect(promise).rejects.toThrow('解析响应失败');
    });
  });
});

// ============ ttsApi 测试 ============

describe('ttsApi', () => {
  const originalFetch = global.fetch;
  let mockFetch: jest.Mock;

  beforeEach(() => {
    mockFetch = jest.fn();
    global.fetch = mockFetch;
  });

  afterAll(() => {
    global.fetch = originalFetch;
  });

  describe('synthesizeTTS', () => {
    it('成功时应返回 audio Blob', async () => {
      const mockBlob = new Blob(['audio-data'], { type: 'audio/mpeg' });
      mockFetch.mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
      });

      const result = await synthesizeTTS('msg-uuid');
      expect(result).toBe(mockBlob);
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/chat/tts/'),
        expect.objectContaining({
          method: 'POST',
          credentials: 'include',
          body: expect.stringContaining('msg-uuid'),
        })
      );
    });

    it('应使用默认 voice 参数', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(new Blob()),
      });

      await synthesizeTTS('msg-uuid');

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.voice).toBe('default');
    });

    it('应传递自定义 voice 参数', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(new Blob()),
      });

      await synthesizeTTS('msg-uuid', 'female');

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.voice).toBe('female');
    });

    it('HTTP 404 应抛出 TTSError', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 404,
        json: () =>
          Promise.resolve({
            code: 'TTS_MODEL_NOT_FOUND',
            message: '模型不存在',
          }),
      });

      try {
        await synthesizeTTS('msg-uuid');
        fail('应抛出异常');
      } catch (e) {
        expect(e).toBeInstanceOf(TTSError);
        expect((e as TTSError).code).toBe('TTS_MODEL_NOT_FOUND');
        expect((e as TTSError).statusCode).toBe(404);
      }
    });

    it('503 TTS_MODEL_SWITCHING 应包含 retry_after', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 503,
        json: () =>
          Promise.resolve({
            code: 'TTS_MODEL_SWITCHING',
            message: '模型切换中',
            data: { retry_after: 30, estimated_wait_seconds: 30 },
          }),
      });

      try {
        await synthesizeTTS('msg-uuid');
        fail('应抛出异常');
      } catch (e) {
        const err = e as TTSError;
        expect(err.code).toBe('TTS_MODEL_SWITCHING');
        expect(err.statusCode).toBe(503);
        expect(err.data?.retry_after).toBe(30);
        expect(err.data?.estimated_wait_seconds).toBe(30);
      }
    });

    it('503 无 retry_after 应为 TTS_SERVICE_UNAVAILABLE', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 503,
        json: () =>
          Promise.resolve({
            code: 'TTS_SERVICE_UNAVAILABLE',
            message: '服务不可用',
          }),
      });

      try {
        await synthesizeTTS('msg-uuid');
        fail('应抛出异常');
      } catch (e) {
        const err = e as TTSError;
        expect(err.code).toBe('TTS_SERVICE_UNAVAILABLE');
        expect(err.data).toBeUndefined();
      }
    });

    it('非 JSON 错误响应应抛出通用 TTSError', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.reject(new Error('not json')),
      });

      try {
        await synthesizeTTS('msg-uuid');
        fail('应抛出异常');
      } catch (e) {
        const err = e as TTSError;
        expect(err.code).toBe('TTS_ERROR');
        expect(err.statusCode).toBe(500);
        expect(err.message).toBe('语音合成失败');
      }
    });
  });
});
