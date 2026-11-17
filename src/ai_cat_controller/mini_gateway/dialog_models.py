"""Public dialog read-only views. No upstream identifiers or control affordance."""

from datetime import datetime
import re
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

State = Literal["starting", "connecting", "connected", "ready", "waiting_wake", "wake_detected", "listening", "processing_audio", "thinking", "answering", "followup_preparing", "followup_listening", "waiting_follow_up", "interrupted", "echo_guard", "recovering", "waking", "interrupting", "submitting_text", "proactive_queued", "proactive_speaking", "proactive_finishing", "stopping", "offline", "unavailable"]


class View(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Status(View):
    state: State
    sessionActive: bool
    audioPlaying: bool | None
    stale: bool
    updatedAt: str | None
    sequence: int = Field(ge=0, le=9_007_199_254_740_991)
    followUpDeadlineAt: str | None
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    sampledAt: str
    readOnly: Literal[True]


class Summary(View):
    conversationId: str = Field(pattern=r"^conv_[0-9a-f]{48}$")
    status: Literal["complete", "waiting_assistant", "assistant_only"]
    source: Literal["device", "mock"]
    startedAt: str
    updatedAt: str
    durationSeconds: int = Field(ge=0, le=1_000_000_000)
    messageCount: int = Field(ge=1, le=1_000_000_000)
    userMessageCount: int = Field(ge=0, le=1_000_000_000)
    assistantMessageCount: int = Field(ge=0, le=1_000_000_000)
    preview: str = Field(min_length=1, max_length=160)
    assistantPreview: str | None = Field(min_length=1, max_length=160)


class Message(View):
    messageId: str = Field(pattern=r"^msg_[0-9a-f]{48}$")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8192)
    createdAt: str


class ListView(View):
    items: list[Summary] = Field(max_length=30)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    limit: Literal[30]
    scope: Literal["persisted_only"]
    unchanged: bool
    sampledAt: str


class Detail(View):
    conversation: Summary
    messages: list[Message] = Field(max_length=100)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    limit: Literal[100]
    windowMessageCount: int = Field(ge=1, le=100)
    truncated: bool
    scope: Literal["persisted_only"]
    unchanged: bool
    sampledAt: str


def validate_view(model, value):
    result = model.model_validate(value).model_dump()
    def visit(node, key=""):
        if isinstance(node, dict):
            for name, child in node.items():
                visit(child, name)
        elif isinstance(node, list):
            for child in node:
                visit(child, key)
        elif isinstance(node, str):
            if any((ord(c) < 32 and c not in "\n\t") or 127 <= ord(c) <= 159
                   or 0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069 for c in node):
                raise ValueError("Invalid dialog text")
            if key.endswith("At"):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", node) is None:
                    raise ValueError("Invalid dialog time")
                datetime.fromisoformat(node[:-1] + "+00:00")
    visit(result)
    summaries = result.get("items", []) + ([result["conversation"]] if "conversation" in result else [])
    if len({item["conversationId"] for item in summaries}) != len(summaries):
        raise ValueError("Duplicate dialog")
    for item in summaries:
        if item["userMessageCount"] + item["assistantMessageCount"] != item["messageCount"]:
            raise ValueError("Invalid dialog counts")
        if datetime.fromisoformat(item["startedAt"].replace("Z", "+00:00")) > datetime.fromisoformat(item["updatedAt"].replace("Z", "+00:00")):
            raise ValueError("Invalid dialog order")
    if "messages" in result:
        messages = result["messages"]
        if len({item["messageId"] for item in messages}) != len(messages):
            raise ValueError("Duplicate message")
        if result["windowMessageCount"] > result["conversation"]["messageCount"]:
            raise ValueError("Invalid dialog window count")
        if result["truncated"] != (result["conversation"]["messageCount"] > result["windowMessageCount"]):
            raise ValueError("Invalid dialog window")
        if result["unchanged"] and messages:
            raise ValueError("Invalid unchanged detail")
        if not result["unchanged"] and len(messages) != result["windowMessageCount"]:
            raise ValueError("Incomplete dialog window")
        if sum(len(item["content"].encode("utf-8")) for item in messages) > 192 * 1024:
            raise ValueError("Dialog content window too large")
        moments = [datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00")) for item in messages]
        if moments != sorted(moments):
            raise ValueError("Invalid message order")
    if "items" in result and result["unchanged"] and result["items"]:
        raise ValueError("Invalid unchanged list")
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 256 * 1024:
        raise ValueError("Dialog view too large")
    return result
