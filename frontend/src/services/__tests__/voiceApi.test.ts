/**
 * voiceApi 服务单元测试
 *
 * 测试内容:
 * - 声纹管理 API（getSpeakerProfile / enrollSpeaker / deleteSpeaker）
 * - 设备管理 API（getDevices / registerDevice / updateDevice / deleteDevice）
 * - 语音设置 API（getVoiceSettings / updateVoiceSettings）
 * - 参数传递和请求格式
 */

// ========== Mock @/services/api ==========

// 使用 jest.mock 工厂函数内部创建 mock，避免变量提升问题
// 通过 require 在测试中获取实际的 mock 引用
jest.mock('@/services/api', () => {
  const mockApiClient = {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  };

  return {
    __esModule: true,
    default: mockApiClient,
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    del: jest.fn(),
  };
});

// 在 mock 之后导入
import {
  getSpeakerProfile,
  enrollSpeaker,
  deleteSpeaker,
  getDevices,
  registerDevice,
  updateDevice,
  deleteDevice,
  getVoiceSettings,
  updateVoiceSettings,
} from '@/services/voiceApi';

// 获取 mock 引用
import apiClient, { get, post, put, del } from '@/services/api';

// 类型断言获取 jest.Mock
const mockGet = get as jest.Mock;
const mockPost = post as jest.Mock;
const mockPut = put as jest.Mock;
const mockDel = del as jest.Mock;
const mockApiClient = apiClient as jest.Mocked<typeof apiClient>;

// ─── 每次测试前重置 ───

beforeEach(() => {
  jest.clearAllMocks();
});

// ========== 测试用例 ==========

describe('voiceApi', () => {
  // ─── 声纹管理 ───

  describe('声纹管理', () => {
    describe('getSpeakerProfile()', () => {
      it('应调用 GET /voice/speakers/', async () => {
        const mockResponse = {
          code: '200',
          message: 'ok',
          data: {
            id: 1,
            gatewaySpeakerId: 'spk-001',
            name: '安琳',
            qualityScore: 0.95,
            enrolledAt: '2026-02-24T10:00:00Z',
          },
        };
        mockGet.mockResolvedValueOnce(mockResponse);

        const result = await getSpeakerProfile();

        expect(mockGet).toHaveBeenCalledWith('/voice/speakers/');
        expect(result).toEqual(mockResponse);
      });
    });

    describe('enrollSpeaker()', () => {
      it('应使用 FormData POST /voice/speakers/', async () => {
        const mockResponse = {
          data: {
            code: '200',
            message: 'ok',
            data: {
              id: 1,
              gatewaySpeakerId: 'spk-001',
              name: '安琳',
              qualityScore: 0.85,
              enrolledAt: '2026-02-24T10:00:00Z',
            },
          },
        };
        mockApiClient.post.mockResolvedValueOnce(mockResponse);

        const audioBlob = new Blob(['fake-audio'], { type: 'audio/wav' });
        const result = await enrollSpeaker('安琳', audioBlob);

        // 验证 apiClient.post 被调用
        expect(mockApiClient.post).toHaveBeenCalledTimes(1);

        // 验证请求路径
        const [url, formData] = mockApiClient.post.mock.calls[0];
        expect(url).toBe('/voice/speakers/');

        // 验证 FormData 内容
        expect(formData).toBeInstanceOf(FormData);
        expect(formData.get('name')).toBe('安琳');

        // 验证 audio 字段
        const audioFile = formData.get('audio');
        expect(audioFile).toBeTruthy();

        // 验证返回值是 response.data
        expect(result).toEqual(mockResponse.data);
      });

      it('应将 audioBlob 以 recording.wav 文件名附加', async () => {
        mockApiClient.post.mockResolvedValueOnce({
          data: { code: '200', message: 'ok', data: {} },
        });

        const audioBlob = new Blob(['test'], { type: 'audio/wav' });
        await enrollSpeaker('测试', audioBlob);

        const formData = mockApiClient.post.mock.calls[0][1] as FormData;
        const audioFile = formData.get('audio') as File;

        // FormData.append 第三个参数是文件名
        expect(audioFile).toBeTruthy();
        if (audioFile instanceof File) {
          expect(audioFile.name).toBe('recording.wav');
        }
      });
    });

    describe('deleteSpeaker()', () => {
      it('应调用 DELETE /voice/speakers/delete/', async () => {
        const mockResponse = { code: '200', message: 'ok', data: null };
        mockDel.mockResolvedValueOnce(mockResponse);

        const result = await deleteSpeaker();

        expect(mockDel).toHaveBeenCalledWith('/voice/speakers/delete/');
        expect(result).toEqual(mockResponse);
      });
    });
  });

  // ─── 设备管理 ───

  describe('设备管理', () => {
    describe('getDevices()', () => {
      it('应调用 GET /voice/devices/', async () => {
        const mockResponse = {
          code: '200',
          message: 'ok',
          data: [
            {
              deviceUuid: 'dev-001',
              name: '我的手机',
              isActive: true,
              createdAt: '2026-02-24T10:00:00Z',
              lastActiveAt: null,
            },
          ],
        };
        mockGet.mockResolvedValueOnce(mockResponse);

        const result = await getDevices();

        expect(mockGet).toHaveBeenCalledWith('/voice/devices/');
        expect(result).toEqual(mockResponse);
      });
    });

    describe('registerDevice()', () => {
      it('应调用 POST /voice/devices/ 并传递设备名称', async () => {
        const mockResponse = {
          code: '200',
          message: 'ok',
          data: {
            deviceUuid: 'dev-002',
            name: '测试设备',
            apiToken: 'token-abc',
          },
        };
        mockPost.mockResolvedValueOnce(mockResponse);

        const result = await registerDevice({ name: '测试设备' });

        expect(mockPost).toHaveBeenCalledWith('/voice/devices/', { name: '测试设备' });
        expect(result).toEqual(mockResponse);
      });
    });

    describe('updateDevice()', () => {
      it('应调用 PUT /voice/devices/{uuid}/ 并传递更新数据', async () => {
        const mockResponse = {
          code: '200',
          message: 'ok',
          data: {
            deviceUuid: 'dev-001',
            name: '新名称',
            isActive: true,
            createdAt: '2026-02-24T10:00:00Z',
            lastActiveAt: null,
          },
        };
        mockPut.mockResolvedValueOnce(mockResponse);

        const result = await updateDevice('dev-001', { name: '新名称' });

        expect(mockPut).toHaveBeenCalledWith('/voice/devices/dev-001/', { name: '新名称' });
        expect(result).toEqual(mockResponse);
      });

      it('应使用正确的 UUID 构建请求路径', async () => {
        mockPut.mockResolvedValueOnce({ code: '200', message: 'ok', data: {} });

        await updateDevice('uuid-abc-123', { name: '设备A' });

        expect(mockPut).toHaveBeenCalledWith(
          '/voice/devices/uuid-abc-123/',
          { name: '设备A' },
        );
      });
    });

    describe('deleteDevice()', () => {
      it('应调用 DELETE /voice/devices/{uuid}/', async () => {
        const mockResponse = { code: '200', message: 'ok', data: null };
        mockDel.mockResolvedValueOnce(mockResponse);

        const result = await deleteDevice('dev-001');

        expect(mockDel).toHaveBeenCalledWith('/voice/devices/dev-001/');
        expect(result).toEqual(mockResponse);
      });

      it('应使用正确的 UUID 构建请求路径', async () => {
        mockDel.mockResolvedValueOnce({ code: '200', message: 'ok', data: null });

        await deleteDevice('uuid-xyz-789');

        expect(mockDel).toHaveBeenCalledWith('/voice/devices/uuid-xyz-789/');
      });
    });
  });

  // ─── 语音设置 ───

  describe('语音设置', () => {
    describe('getVoiceSettings()', () => {
      it('应调用 GET /voice/settings/', async () => {
        const mockResponse = {
          code: '200',
          message: 'ok',
          data: {
            wakeWords: ['你好小助手'],
            recordingMode: 'toggle',
            vadSensitivity: 0.5,
          },
        };
        mockGet.mockResolvedValueOnce(mockResponse);

        const result = await getVoiceSettings();

        expect(mockGet).toHaveBeenCalledWith('/voice/settings/');
        expect(result).toEqual(mockResponse);
      });
    });

    describe('updateVoiceSettings()', () => {
      it('应调用 PUT /voice/settings/ 并传递设置数据', async () => {
        const mockResponse = {
          code: '200',
          message: 'ok',
          data: {
            wakeWords: ['小助手'],
            recordingMode: 'hold',
            vadSensitivity: 0.8,
          },
        };
        mockPut.mockResolvedValueOnce(mockResponse);

        const updateData = {
          wake_words: ['小助手'],
          recording_mode: 'hold' as const,
          vad_sensitivity: 0.8,
        };
        const result = await updateVoiceSettings(updateData);

        expect(mockPut).toHaveBeenCalledWith('/voice/settings/', updateData);
        expect(result).toEqual(mockResponse);
      });

      it('应支持部分更新（仅传递部分字段）', async () => {
        mockPut.mockResolvedValueOnce({
          code: '200',
          message: 'ok',
          data: {
            wakeWords: ['你好'],
            recordingMode: 'toggle',
            vadSensitivity: 0.3,
          },
        });

        await updateVoiceSettings({ vad_sensitivity: 0.3 });

        expect(mockPut).toHaveBeenCalledWith('/voice/settings/', {
          vad_sensitivity: 0.3,
        });
      });
    });
  });

  // ─── 错误处理 ───

  describe('错误处理', () => {
    it('API 请求失败时应抛出错误', async () => {
      const networkError = new Error('Network Error');
      mockGet.mockRejectedValueOnce(networkError);

      await expect(getSpeakerProfile()).rejects.toThrow('Network Error');
    });

    it('enrollSpeaker 请求失败时应抛出错误', async () => {
      const apiError = new Error('Server Error');
      mockApiClient.post.mockRejectedValueOnce(apiError);

      const audioBlob = new Blob(['test'], { type: 'audio/wav' });
      await expect(enrollSpeaker('test', audioBlob)).rejects.toThrow('Server Error');
    });

    it('deleteDevice 请求失败时应抛出错误', async () => {
      const notFoundError = new Error('Not Found');
      mockDel.mockRejectedValueOnce(notFoundError);

      await expect(deleteDevice('invalid-uuid')).rejects.toThrow('Not Found');
    });
  });

  // ─── 路径拼接验证 ───

  describe('路径拼接', () => {
    it('所有 API 路径应以 /voice/ 开头', async () => {
      // 为所有 mock 设置返回值
      mockGet.mockResolvedValue({ code: '200', message: 'ok', data: null });
      mockPost.mockResolvedValue({ code: '200', message: 'ok', data: null });
      mockPut.mockResolvedValue({ code: '200', message: 'ok', data: null });
      mockDel.mockResolvedValue({ code: '200', message: 'ok', data: null });

      await getSpeakerProfile();
      await getDevices();
      await getVoiceSettings();

      // 验证所有 GET 请求路径
      for (const call of mockGet.mock.calls) {
        expect(call[0]).toMatch(/^\/voice\//);
      }
    });

    it('设备操作路径应包含 UUID', async () => {
      mockPut.mockResolvedValue({ code: '200', message: 'ok', data: {} });
      mockDel.mockResolvedValue({ code: '200', message: 'ok', data: null });

      const uuid = 'test-device-uuid';

      await updateDevice(uuid, { name: '新名称' });
      await deleteDevice(uuid);

      expect(mockPut).toHaveBeenCalledWith(
        expect.stringContaining(uuid),
        expect.anything(),
      );
      expect(mockDel).toHaveBeenCalledWith(expect.stringContaining(uuid));
    });
  });
});
