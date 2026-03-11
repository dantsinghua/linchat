import json as json_module
import logging
from enum import Enum
from typing import Optional

import httpx
from pypinyin import lazy_pinyin

from apps.voice.repositories import voice_settings_repo
from apps.voice.services.voice_session_service import voice_session_service
from core.redis import get_redis

logger = logging.getLogger(__name__)
EMERGENCY_STOP_WORDS = {"停", "取消", "闭嘴", "停止", "别说了"}
QUESTION_PARTICLES = {"吗", "呢", "吧", "么"}
QUESTION_WORDS = {"什么", "怎么", "哪", "谁", "为什么", "怎样", "如何", "多少", "几"}


class DecisionResult(Enum):
    RESPOND = "RESPOND"
    RECORD_ONLY = "RECORD_ONLY"
    STOP = "STOP"


class ResponseDecisionService:

    async def decide(self, transcription_text: str, speaker_id: Optional[str],
                     user_id: int, mode: str = "ambient") -> tuple[DecisionResult, str]:
        text = transcription_text.strip()
        if not text:
            return DecisionResult.RECORD_ONLY, "empty_text"
        if self._check_emergency_stop(text):
            return DecisionResult.STOP, "emergency_stop"
        wake_words = await self._load_wake_words(user_id)
        if self._check_exact_wake_word(text, wake_words):
            return DecisionResult.RESPOND, "exact_wake_word"
        if self._check_fuzzy_wake_word(text, wake_words):
            return DecisionResult.RESPOND, "fuzzy_wake_word"
        if mode == "ambient":
            from django.conf import settings as django_settings
            if django_settings.VOICE_DECISION_USE_LLM:
                llm_result = await self._classify_intent_llm(text)
                if llm_result is not None:
                    decision, reason, confidence = llm_result
                    if confidence >= django_settings.VOICE_DECISION_LLM_THRESHOLD:
                        return decision, f"llm_{reason}"
        if await voice_session_service.is_active_conversation(user_id):
            return DecisionResult.RESPOND, "active_conversation"
        recent = await self._get_recent_speaker_count(user_id)
        if recent >= 2:
            return DecisionResult.RECORD_ONLY, "multi_speaker"
        if self._check_question_features(text):
            return DecisionResult.RESPOND, "question_detected"
        return DecisionResult.RECORD_ONLY, "default"

    async def _classify_intent_llm(self, text: str) -> Optional[tuple[DecisionResult, str, float]]:
        from django.conf import settings as django_settings
        try:
            from apps.models.services import model_service
            model_config = await model_service.get_active_model("tool")
            if not model_config:
                return None
            prompt = (
                "你是一个智能家居环境中的语音助手判断器。\n"
                "判断以下用户话语是否需要 AI 助手回复。\n\n"
                "需要回复的情况：明确的指令、请求、提问、需要帮助。\n"
                "不需要回复的情况：自言自语、与他人交谈、感叹、无意义的声音。\n\n"
                f"用户话语：{text}\n\n"
                '返回 JSON：{"decision": "RESPOND" 或 "RECORD_ONLY", "confidence": 0.0-1.0, "reason": "简短原因"}'
            )
            async with httpx.AsyncClient(timeout=django_settings.VOICE_DECISION_LLM_TIMEOUT) as client:
                resp = await client.post(
                    f"{model_config.api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {model_config.decrypted_api_key}"},
                    json={"model": model_config.model_name, "messages": [{"role": "user", "content": prompt}],
                          "response_format": {"type": "json_object"}, "temperature": 0.1, "max_tokens": 100})
                resp.raise_for_status()
            result = json_module.loads(resp.json()["choices"][0]["message"]["content"])
            decision = DecisionResult.RESPOND if result.get("decision", "").upper() == "RESPOND" else DecisionResult.RECORD_ONLY
            return decision, result.get("reason", "unknown"), float(result.get("confidence", 0.0))
        except Exception as e:
            if not isinstance(e, httpx.TimeoutException):
                logger.warning("LLM decision error: text=%s", text[:30], exc_info=True)
            return None

    @staticmethod
    def _check_emergency_stop(text: str) -> bool:
        return any(text == w or text.startswith(w) for w in EMERGENCY_STOP_WORDS)

    @staticmethod
    def _check_exact_wake_word(text: str, wake_words: list[str]) -> bool:
        return any(w in text for w in wake_words)

    @staticmethod
    def _check_fuzzy_wake_word(text: str, wake_words: list[str]) -> bool:
        for word in [w for w in wake_words if w]:
            for i in range(max(1, len(text) - len(word) + 2)):
                sub = text[i:min(i + len(word), len(text))]
                if sub and (_edit_distance(sub, word) <= 1 or _pinyin_similarity(sub, word) >= 0.8):
                    return True
        return False

    @staticmethod
    def _check_question_features(text: str) -> bool:
        return "？" in text or "?" in text or any(w in text for w in QUESTION_WORDS) or bool(text and text[-1] in QUESTION_PARTICLES)

    async def _load_wake_words(self, user_id: int) -> list[str]:
        from django.conf import settings
        try:
            vs, _ = await voice_settings_repo.get_or_create(user_id)
            return vs.wake_words if isinstance(vs.wake_words, list) and vs.wake_words else settings.VOICE_DEFAULT_WAKE_WORDS
        except Exception:
            return settings.VOICE_DEFAULT_WAKE_WORDS

    @staticmethod
    async def _get_recent_speaker_count(user_id: int) -> int:
        try:
            r = await get_redis()
            count = await r.scard(f"voice:recent_speakers:{user_id}")
            await r.aclose()
            return count
        except Exception:
            return 0


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
    if not py1 or not py2:
        return 0.0
    mx = max(len(py1), len(py2))
    return sum(1 for a, b in zip(py1, py2) if a == b) / mx if mx else 1.0


response_decision_service = ResponseDecisionService()
