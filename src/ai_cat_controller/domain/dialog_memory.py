"""Deterministic extraction rules for bounded, structured dialog memory."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_LONG_TERM_FACTS_PER_MESSAGE = 6
MAX_LONG_TERM_FACT_CHARS = 120

_CLAUSE_SPLIT = re.compile(r"[，,。！？!?；;\n]+")
_NAME_PATTERN = re.compile(
    r"(?:我叫|我的名字是|请叫我|可以叫我)"
    r"(?P<value>[A-Za-z0-9\u4e00-\u9fff·_-]{1,20})"
)
_PREFERENCE_PATTERN = re.compile(
    r"我(?P<polarity>最喜欢|喜欢|不喜欢|讨厌)(?P<value>.{1,60})"
)
_HABIT_PATTERNS = (
    re.compile(r"(?:我的习惯是|我习惯于|我习惯)(?P<value>.{1,80})"),
    re.compile(
        r"我(?P<frequency>每天|每周|每月|经常|通常|总是)"
        r"(?P<value>.{1,80})"
    ),
)
_IMPORTANT_PATTERNS = (
    (re.compile(r"(?:我的生日是|我生日是)(?P<value>.{1,80})"), "生日是"),
    (re.compile(r"(?:我的纪念日是|我纪念日是)(?P<value>.{1,80})"), "纪念日是"),
    (
        re.compile(r"(?:重要的事情是|对我很重要的是)(?P<value>.{1,100})"),
        "",
    ),
    (re.compile(r"(?:请记住|记住)(?P<value>.{1,100})"), ""),
)
_BLOCKED_FRAGMENTS = (
    "忽略系统",
    "系统提示",
    "提示词",
    "开发者指令",
    "执行命令",
    "绕过规则",
    "越狱",
)


@dataclass(frozen=True, slots=True)
class LongTermMemoryCandidate:
    category: str
    memory_key: str
    content: str
    confidence_milli: int


def _clean_value(value: str) -> str | None:
    cleaned = " ".join(value.split()).strip(" ：:‘’'\"“”()（）[]【】")
    cleaned = re.sub(r"^(?:的是|就是)", "", cleaned).strip()
    if not cleaned or any(fragment in cleaned for fragment in _BLOCKED_FRAGMENTS):
        return None
    return cleaned[:MAX_LONG_TERM_FACT_CHARS]


def _memory_key(category: str, value: str) -> str:
    normalized = re.sub(r"\s+", "", value).casefold()
    digest = hashlib.sha256(f"{category}:{normalized}".encode("utf-8")).hexdigest()
    return digest[:20]


def extract_long_term_memories(content: str) -> tuple[LongTermMemoryCandidate, ...]:
    """Extract explicit first-person facts without treating dialog as instructions."""

    source = str(content)[:500]
    candidates: list[LongTermMemoryCandidate] = []
    seen: set[tuple[str, str]] = set()

    def add(category: str, value: str, summary: str, confidence: int) -> None:
        cleaned_value = _clean_value(value)
        cleaned_summary = _clean_value(summary)
        if cleaned_value is None or cleaned_summary is None:
            return
        key = "user_name" if category == "name" else _memory_key(
            category,
            cleaned_value,
        )
        identity = (category, key)
        if identity in seen:
            return
        seen.add(identity)
        candidates.append(
            LongTermMemoryCandidate(
                category=category,
                memory_key=key,
                content=cleaned_summary,
                confidence_milli=confidence,
            )
        )

    for clause in _CLAUSE_SPLIT.split(source):
        clause = " ".join(clause.split()).strip()
        if not clause or any(fragment in clause for fragment in _BLOCKED_FRAGMENTS):
            continue

        if match := _NAME_PATTERN.search(clause):
            value = match.group("value")
            add("name", value, value, 980)

        if match := _PREFERENCE_PATTERN.search(clause):
            value = match.group("value")
            polarity = match.group("polarity")
            normalized_polarity = "不喜欢" if polarity in {"不喜欢", "讨厌"} else "喜欢"
            add(
                "preference",
                value,
                f"{normalized_polarity}{value}",
                920,
            )

        for pattern in _HABIT_PATTERNS:
            if not (match := pattern.search(clause)):
                continue
            value = match.group("value")
            frequency = match.groupdict().get("frequency") or "习惯"
            add("habit", value, f"{frequency}{value}", 880)
            break

        for pattern, prefix in _IMPORTANT_PATTERNS:
            if not (match := pattern.search(clause)):
                continue
            value = match.group("value")
            add("important_event", value, f"{prefix}{value}", 850)
            break

        if len(candidates) >= MAX_LONG_TERM_FACTS_PER_MESSAGE:
            break

    return tuple(candidates[:MAX_LONG_TERM_FACTS_PER_MESSAGE])
