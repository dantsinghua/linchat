"""响应决策服务 (T041)

参考:
- specs/009-voice-interaction/spec.md FR-019~FR-021a
- specs/009-voice-interaction/tasks.md T041

职责：
根据唤醒词检测、活跃对话状态和多因素判断，
智能决定是回复（RESPOND）、仅记录（RECORD_ONLY）还是停止（STOP）。

决策优先级链（短路求值，命中即返回）：
① 紧急命令词白名单 → STOP
② 唤醒词精确匹配 → RESPOND
③ 唤醒词模糊匹配（编辑距离≤1 或拼音相似度≥0.8）→ RESPOND
④ 活跃对话状态（Redis active_conv 存在）→ RESPOND
⑤ 非活跃 + 多 speaker 活跃（recent_speakers ≥ 2）→ RECORD_ONLY
⑥ 非活跃 + 单 speaker + 问句特征 → RESPOND
⑦ 默认 → RECORD_ONLY
"""

import logging
from enum import Enum
from typing import Optional

from pypinyin import lazy_pinyin

from apps.voice.repositories import voice_settings_repo
from apps.voice.services.voice_session_service import voice_session_service
from core.redis import get_redis

logger = logging.getLogger(__name__)

# 紧急命令词白名单（FR-020）
EMERGENCY_STOP_WORDS = {"停", "取消", "闭嘴", "停止", "别说了"}

# 问句特征词集（FR-021）
QUESTION_PARTICLES = {"吗", "呢", "吧", "么"}
QUESTION_WORDS = {"什么", "怎么", "哪", "谁", "为什么", "怎样", "如何", "多少", "几"}


class DecisionResult(str, Enum):
    """响应决策结果"""

    RESPOND = "RESPOND"
    RECORD_ONLY = "RECORD_ONLY"
    STOP = "STOP"


class ResponseDecisionService:
    """响应决策服务"""

    async def decide(
        self,
        transcription_text: str,
        speaker_id: Optional[str],
        user_id: int,
    ) -> tuple[DecisionResult, str]:
        """执行响应决策

        Args:
            transcription_text: STT 转写文本
            speaker_id: 说话人 gateway ID（可选）
            user_id: 设备拥有者的用户 ID（用于加载设置和查询状态）

        Returns:
            (决策结果, 决策原因)
        """
        text = transcription_text.strip()

        if not text:
            return DecisionResult.RECORD_ONLY, "empty_text"

        # ① 紧急命令词 → STOP
        if self._check_emergency_stop(text):
            logger.info(
                "Decision STOP (emergency): user_id=%s, text=%s",
                user_id,
                text[:20],
            )
            return DecisionResult.STOP, "emergency_stop"

        # 加载用户唤醒词
        wake_words = await self._load_wake_words(user_id)

        # ② 唤醒词精确匹配 → RESPOND
        if self._check_exact_wake_word(text, wake_words):
            logger.info(
                "Decision RESPOND (exact_wake): user_id=%s, text=%s",
                user_id,
                text[:20],
            )
            return DecisionResult.RESPOND, "exact_wake_word"

        # ③ 唤醒词模糊匹配 → RESPOND
        if self._check_fuzzy_wake_word(text, wake_words):
            logger.info(
                "Decision RESPOND (fuzzy_wake): user_id=%s, text=%s",
                user_id,
                text[:20],
            )
            return DecisionResult.RESPOND, "fuzzy_wake_word"

        # ④ 活跃对话 → RESPOND（FR-021a）
        is_active = await voice_session_service.is_active_conversation(
            user_id
        )
        if is_active:
            logger.info(
                "Decision RESPOND (active_conv): user_id=%s", user_id
            )
            return DecisionResult.RESPOND, "active_conversation"

        # ⑤ 多 speaker 活跃 → RECORD_ONLY（FR-021）
        recent_count = await self._get_recent_speaker_count(user_id)
        if recent_count >= 2:
            logger.info(
                "Decision RECORD_ONLY (multi_speaker): user_id=%s, "
                "speakers=%d",
                user_id,
                recent_count,
            )
            return DecisionResult.RECORD_ONLY, "multi_speaker"

        # ⑥ 单 speaker + 问句特征 → RESPOND（FR-021）
        if self._check_question_features(text):
            logger.info(
                "Decision RESPOND (question): user_id=%s, text=%s",
                user_id,
                text[:20],
            )
            return DecisionResult.RESPOND, "question_detected"

        # ⑦ 默认 → RECORD_ONLY
        logger.info(
            "Decision RECORD_ONLY (default): user_id=%s, text=%s",
            user_id,
            text[:20],
        )
        return DecisionResult.RECORD_ONLY, "default"

    # ========== 判定方法 ==========

    @staticmethod
    def _check_emergency_stop(text: str) -> bool:
        """检查紧急命令词（FR-020）"""
        # 文本以紧急词开头或完全匹配
        for word in EMERGENCY_STOP_WORDS:
            if text == word or text.startswith(word):
                return True
        return False

    @staticmethod
    def _check_exact_wake_word(
        text: str, wake_words: list[str]
    ) -> bool:
        """唤醒词精确匹配（FR-019）"""
        for word in wake_words:
            if word in text:
                return True
        return False

    @staticmethod
    def _check_fuzzy_wake_word(
        text: str, wake_words: list[str]
    ) -> bool:
        """唤醒词模糊匹配：编辑距离≤1 或拼音相似度≥0.8（FR-019）"""
        # 对文本进行滑动窗口匹配
        for word in wake_words:
            word_len = len(word)
            if word_len == 0:
                continue

            # 在文本中按唤醒词长度滑动
            for i in range(max(1, len(text) - word_len + 2)):
                end = min(i + word_len, len(text))
                if end <= i:
                    continue
                substring = text[i:end]

                # 编辑距离检查
                if _edit_distance(substring, word) <= 1:
                    return True

                # 拼音相似度检查
                if _pinyin_similarity(substring, word) >= 0.8:
                    return True

        return False

    @staticmethod
    def _check_question_features(text: str) -> bool:
        """问句特征检测（FR-021）

        检查：问号、疑问词、语气词
        """
        # 问号
        if "？" in text or "?" in text:
            return True

        # 疑问词
        for word in QUESTION_WORDS:
            if word in text:
                return True

        # 句尾语气词
        if text and text[-1] in QUESTION_PARTICLES:
            return True

        return False

    async def _load_wake_words(self, user_id: int) -> list[str]:
        """加载用户唤醒词列表"""
        try:
            voice_settings, _ = await voice_settings_repo.get_or_create(
                user_id
            )
            words = voice_settings.wake_words
            if isinstance(words, list) and words:
                return words
        except Exception:
            logger.warning(
                "Failed to load wake words: user_id=%s", user_id
            )

        # 回退到默认唤醒词
        from django.conf import settings

        return settings.VOICE_DEFAULT_WAKE_WORDS

    @staticmethod
    async def _get_recent_speaker_count(user_id: int) -> int:
        """获取最近活跃说话人数量"""
        try:
            redis_key = f"voice:recent_speakers:{user_id}"
            redis_client = await get_redis()
            try:
                count = await redis_client.scard(redis_key)
                return count
            finally:
                await redis_client.aclose()
        except Exception:
            return 0


# ========== 工具函数 ==========


def _edit_distance(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离（Levenshtein 距离）"""
    m, n = len(s1), len(s2)
    if m == 0:
        return n
    if n == 0:
        return m

    # 使用滚动数组优化空间
    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,      # 删除
                curr[j - 1] + 1,  # 插入
                prev[j - 1] + cost,  # 替换
            )
        prev, curr = curr, prev

    return prev[n]


def _pinyin_similarity(s1: str, s2: str) -> float:
    """计算两个中文字符串的拼音相似度（0.0~1.0）

    将两个字符串转换为拼音列表后逐音节比较。
    """
    py1 = lazy_pinyin(s1)
    py2 = lazy_pinyin(s2)

    if not py1 or not py2:
        return 0.0

    max_len = max(len(py1), len(py2))
    if max_len == 0:
        return 1.0

    matches = 0
    for a, b in zip(py1, py2):
        if a == b:
            matches += 1

    return matches / max_len


# 全局实例
response_decision_service = ResponseDecisionService()
