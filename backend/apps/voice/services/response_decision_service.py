import logging
from enum import Enum
from typing import Optional
from pypinyin import lazy_pinyin
from apps.voice.repositories import voice_settings_repo
from apps.voice.services.voice_session_service import voice_session_service
from core.redis import get_redis

logger = logging.getLogger(__name__)
EMERGENCY_STOP_WORDS = {"停", "取消", "闭嘴", "停止", "别说了"}
QUESTION_PARTICLES = {"吗", "呢", "吧", "么"}
QUESTION_WORDS = {"什么", "怎么", "哪", "谁", "为什么", "怎样", "如何", "多少", "几"}


class DecisionResult(str, Enum):
    RESPOND = "RESPOND"
    RECORD_ONLY = "RECORD_ONLY"
    STOP = "STOP"


class ResponseDecisionService:

    async def decide(self, transcription_text: str, speaker_id: Optional[str], user_id: int) -> tuple[DecisionResult, str]:
        text = transcription_text.strip()
        if not text: return DecisionResult.RECORD_ONLY, "empty_text"
        if self._check_emergency_stop(text):
            logger.info("Decision STOP (emergency): user=%s, text=%s", user_id, text[:20])
            return DecisionResult.STOP, "emergency_stop"
        wake_words = await self._load_wake_words(user_id)
        if self._check_exact_wake_word(text, wake_words):
            logger.info("Decision RESPOND (exact_wake): user=%s", user_id)
            return DecisionResult.RESPOND, "exact_wake_word"
        if self._check_fuzzy_wake_word(text, wake_words):
            logger.info("Decision RESPOND (fuzzy_wake): user=%s", user_id)
            return DecisionResult.RESPOND, "fuzzy_wake_word"
        if await voice_session_service.is_active_conversation(user_id):
            logger.info("Decision RESPOND (active_conv): user=%s", user_id)
            return DecisionResult.RESPOND, "active_conversation"
        recent = await self._get_recent_speaker_count(user_id)
        if recent >= 2:
            logger.info("Decision RECORD_ONLY (multi_speaker): user=%s, n=%d", user_id, recent)
            return DecisionResult.RECORD_ONLY, "multi_speaker"
        if self._check_question_features(text):
            logger.info("Decision RESPOND (question): user=%s", user_id)
            return DecisionResult.RESPOND, "question_detected"
        logger.info("Decision RECORD_ONLY (default): user=%s", user_id)
        return DecisionResult.RECORD_ONLY, "default"

    @staticmethod
    def _check_emergency_stop(text: str) -> bool:
        return any(text == w or text.startswith(w) for w in EMERGENCY_STOP_WORDS)

    @staticmethod
    def _check_exact_wake_word(text: str, wake_words: list[str]) -> bool:
        return any(w in text for w in wake_words)

    @staticmethod
    def _check_fuzzy_wake_word(text: str, wake_words: list[str]) -> bool:
        for word in wake_words:
            wl = len(word)
            if wl == 0: continue
            for i in range(max(1, len(text) - wl + 2)):
                end = min(i + wl, len(text))
                if end <= i: continue
                sub = text[i:end]
                if _edit_distance(sub, word) <= 1: return True
                if _pinyin_similarity(sub, word) >= 0.8: return True
        return False

    @staticmethod
    def _check_question_features(text: str) -> bool:
        if "？" in text or "?" in text: return True
        if any(w in text for w in QUESTION_WORDS): return True
        return bool(text and text[-1] in QUESTION_PARTICLES)

    async def _load_wake_words(self, user_id: int) -> list[str]:
        try:
            vs, _ = await voice_settings_repo.get_or_create(user_id)
            if isinstance(vs.wake_words, list) and vs.wake_words: return vs.wake_words
        except Exception:
            logger.warning("Failed to load wake words: user=%s", user_id)
        from django.conf import settings
        return settings.VOICE_DEFAULT_WAKE_WORDS

    @staticmethod
    async def _get_recent_speaker_count(user_id: int) -> int:
        try:
            r = await get_redis()
            try: return await r.scard(f"voice:recent_speakers:{user_id}")
            finally: await r.aclose()
        except Exception: return 0


def _edit_distance(s1: str, s2: str) -> int:
    m, n = len(s1), len(s2)
    if m == 0: return n
    if n == 0: return m
    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[n]


def _pinyin_similarity(s1: str, s2: str) -> float:
    py1, py2 = lazy_pinyin(s1), lazy_pinyin(s2)
    if not py1 or not py2: return 0.0
    mx = max(len(py1), len(py2))
    return sum(1 for a, b in zip(py1, py2) if a == b) / mx if mx else 1.0


response_decision_service = ResponseDecisionService()
