import json as json_module
import logging
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional

import httpx
from pypinyin import lazy_pinyin

from apps.context.loader import render
from apps.voice.repositories import voice_settings_repo
from apps.voice.services.voice_session_service import voice_session_service
from core.redis import get_redis

logger = logging.getLogger(__name__)
EMERGENCY_STOP_WORDS = {"停", "取消", "闭嘴", "停止", "别说了"}
QUESTION_PARTICLES = {"吗", "呢", "吧", "么"}
QUESTION_WORDS = {"什么", "怎么", "哪", "谁", "为什么", "怎样", "如何", "多少", "几"}

_TTS_PLAYING_KEY = "voice:tts_playing:{user_id}"
_TTS_HISTORY_KEY = "voice:tts_history:{user_id}"
_TTS_ECHO_SIMILARITY_THRESHOLD = 0.7


class DecisionResult(Enum):
    RESPOND = "RESPOND"
    RECORD_ONLY = "RECORD_ONLY"
    STOP = "STOP"
    DISCARD = "DISCARD"


class ResponseDecisionService:

    async def decide(self, transcription_text: str, speaker_id: Optional[str],
                     user_id: int, mode: str = "ambient",
                     speaker_identified: bool = False) -> tuple[DecisionResult, str]:
        text = transcription_text.strip()
        if not text:
            return DecisionResult.RECORD_ONLY, "empty_text"
        # Level 0: TTS echo 过滤 — 防止 AI 自身语音被麦克风采集后再次触发响应
        if await self._is_tts_echo(text, user_id):
            return DecisionResult.DISCARD, "tts_echo_detected"
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
                llm_result = await self._classify_intent_llm(text, user_id)
                if llm_result is not None:
                    decision, reason, confidence = llm_result
                    if confidence >= django_settings.VOICE_DECISION_LLM_THRESHOLD:
                        return decision, f"llm_{reason}"
        if await voice_session_service.is_active_conversation(user_id):
            return DecisionResult.RESPOND, "active_conversation"
        if not speaker_identified:
            recent = await self._get_recent_speaker_count(user_id)
            if recent >= 2:
                return DecisionResult.RECORD_ONLY, "multi_speaker"
        if self._check_question_features(text):
            return DecisionResult.RESPOND, "question_detected"
        return DecisionResult.RECORD_ONLY, "default"

    async def _classify_intent_llm(self, text: str, user_id: int = 0) -> Optional[tuple[DecisionResult, str, float]]:
        from django.conf import settings as django_settings
        try:
            from apps.models.services import model_service
            from asgiref.sync import sync_to_async
            model_config = await sync_to_async(model_service.get_active_model)("tool")
            if not model_config:
                return None

            # 获取对话上下文和用户记忆，增强意图分类准确性
            recent_messages: list[dict[str, str]] = []
            memory_summary: Optional[str] = None
            if user_id:
                recent_messages, memory_summary = await self._fetch_intent_context(user_id, text)

            prompt = render("voice_intent_classify.j2", text=text,
                            recent_messages=recent_messages, memory_summary=memory_summary)

            async with httpx.AsyncClient(timeout=django_settings.VOICE_DECISION_LLM_TIMEOUT) as client:
                resp = await client.post(
                    f"{model_config['url']}/chat/completions",
                    headers={"Authorization": f"Bearer {model_config['api_key']}"},
                    json={"model": model_config["name"], "messages": [{"role": "user", "content": prompt}],
                          "response_format": {"type": "json_object"}, "temperature": 0.1, "max_tokens": 100})
                resp.raise_for_status()
            result = json_module.loads(resp.json()["choices"][0]["message"]["content"], strict=False)
            raw_decision = result.get("decision", "")
            decision_str = raw_decision if isinstance(raw_decision, str) else ""
            decision = DecisionResult.RESPOND if decision_str.upper() == "RESPOND" else DecisionResult.RECORD_ONLY
            return decision, result.get("reason", "unknown"), float(result.get("confidence", 0.0))
        except httpx.TimeoutException:
            # 超时时安全降级为 RECORD_ONLY，避免穿透到规则链误触发 RESPOND
            logger.info("LLM intent classify timeout: text=%s", text[:30])
            return DecisionResult.RECORD_ONLY, "llm_timeout", 1.0
        except Exception as e:
            logger.warning("LLM decision error: text=%s", text[:30], exc_info=True)
            return None

    @staticmethod
    async def _fetch_intent_context(user_id: int, text: str) -> tuple[list[dict[str, str]], Optional[str]]:
        """获取最近对话和用户记忆，用于 LLM 意图分类上下文增强。

        Returns:
            (recent_messages, memory_summary) — 任一失败返回空列表/None，不影响分类流程。
        """
        recent_messages: list[dict[str, str]] = []
        memory_summary: Optional[str] = None
        try:
            from apps.chat.repositories import message_repo
            msgs = await message_repo.find_latest_by_user(user_id, limit=5)
            # find_latest_by_user 按 -created_time 排序，需反转为时间正序
            for m in reversed(msgs):
                content = (m.content or "")[:200]
                if content:
                    recent_messages.append({"role": m.role, "content": content})
        except Exception as e:
            logger.debug("Failed to fetch recent messages for intent: user=%s, err=%s", user_id, e)
        try:
            from apps.memory.services import MemoryService
            memory_summary = await MemoryService.retrieve_relevant_memories(user_id, text, limit=3)
        except Exception as e:
            logger.debug("Failed to fetch memories for intent: user=%s, err=%s", user_id, e)
        return recent_messages, memory_summary

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

    async def _is_tts_echo(self, text: str, user_id: int) -> bool:
        """检测转录文本是否为 TTS 自身输出的回声。

        策略 1: Redis voice:tts_playing:{user_id} 存在 → TTS 正在播放 → 立即判定为 echo。
        策略 2: 从 voice:tts_history:{user_id} 取最近 10 条历史，任意一条与 text
                的 SequenceMatcher ratio > 0.7 → 判定为 echo。

        Args:
            text: 已转录的文本。
            user_id: 用户 ID。

        Returns:
            True 表示检测到 TTS echo，应丢弃该转录。
        """
        try:
            r = await get_redis()
            playing_key = _TTS_PLAYING_KEY.format(user_id=user_id)
            if await r.exists(playing_key):
                logger.debug("TTS echo detected (playing): user=%s, text=%s", user_id, text[:30])
                return True
            history_key = _TTS_HISTORY_KEY.format(user_id=user_id)
            history: list[str] = await r.lrange(history_key, 0, 9)
            for tts_text in history:
                ratio = SequenceMatcher(None, text, tts_text).ratio()
                if ratio > _TTS_ECHO_SIMILARITY_THRESHOLD:
                    logger.debug(
                        "TTS echo detected (history): user=%s, ratio=%.2f, text=%s",
                        user_id, ratio, text[:30],
                    )
                    return True
        except Exception:
            logger.debug("TTS echo check failed (ignored): user=%s", user_id, exc_info=True)
        return False


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
