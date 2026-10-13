"""通道抽象层。

每个通道只需实现 _send_text / _send_markdown / _send_image / _send_file 中
自己支持的那几个，其余由基类按 caps 做降级：
  - markdown 超限 → 有 file 能力就发文件，否则按段落切片
  - 不支持图片 → 走 _fallback_image（默认发一条文件名提示）
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import media
from ..config import CHANNEL_SPECS


@dataclass
class SendItem:
    kind: str                       # image | markdown | file
    path: str
    filename: str = ""
    data: bytes = b""
    text: str = ""
    caption: str = ""
    # auto: 短的直接发 markdown，超限转文件；file: 总是当文件发；split: 总是切片
    markdown_mode: str = "auto"

    def __post_init__(self) -> None:
        if not self.filename:
            self.filename = os.path.basename(self.path)


class BaseChannel(ABC):
    type: str = "base"

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.id = cfg.get("id", "")
        self.name = cfg.get("name", "")
        self.options: Dict[str, Any] = cfg.get("options", {}) or {}
        spec = CHANNEL_SPECS.get(self.type, {})
        self.caps: Dict[str, bool] = spec.get("caps", {})
        self._last_error: Optional[str] = None

    # ---------- 生命周期（长连接类通道覆写） ----------

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def health(self) -> Dict[str, Any]:
        return {"ready": True, "detail": "", "last_error": self._last_error}

    # ---------- 选项助手 ----------

    def opt(self, key: str, default: Any = None) -> Any:
        value = self.options.get(key, default)
        return default if value in (None, "") else value

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if not str(self.options.get(k, "")).strip()]
        if missing:
            raise RuntimeError(f"通道 {self.name} 缺少必填配置: {', '.join(missing)}")

    # ---------- 统一入口 ----------

    async def deliver(self, item: SendItem) -> str:
        if item.kind == "markdown":
            return await self._deliver_markdown(item)
        if item.kind == "image":
            return await self._deliver_image(item)
        return await self._deliver_file(item)

    async def deliver_text(self, text: str) -> str:
        return await self._send_text(text)

    async def _deliver_markdown(self, item: SendItem) -> str:
        limit = int(self.opt("markdown_max_bytes", 3800))
        body = item.text or ""
        if item.caption and not body.lstrip().startswith(item.caption[:20]):
            body = f"{item.caption}\n\n{body}" if body.strip() else item.caption
        mode = item.markdown_mode or "auto"

        if mode == "file" and self.caps.get("file"):
            return await self._send_file(item.path, body.encode("utf-8"), item.filename)
        if mode == "split":
            parts = media.split_markdown(body, limit)
            for part in parts:
                await self._send_markdown(part)
            return f"markdown 分 {len(parts)} 段发送"
        if self.caps.get("markdown") and media.byte_len(body) <= limit:
            return await self._send_markdown(body)
        if self.caps.get("file"):
            return await self._send_file(item.path, body.encode("utf-8"), item.filename)
        parts = media.split_markdown(body, limit)
        for part in parts:
            await self._send_markdown(part)
        return f"markdown 超长，分 {len(parts)} 段发送"

    async def _deliver_image(self, item: SendItem) -> str:
        if not self.caps.get("image"):
            return await self._fallback_image(item)
        data = item.data
        limit = int(self.opt("image_max_bytes", 2 * 1024 * 1024))
        if limit and len(data) > limit:
            data, suffix = media.compress_image_to_limit(data, limit)
            filename = os.path.splitext(item.filename)[0] + suffix
        else:
            filename = item.filename
        res = await self._send_image(data, filename)
        if item.caption and self.caps.get("text"):
            await self._send_text(item.caption)
        return res

    async def _deliver_file(self, item: SendItem) -> str:
        if not self.caps.get("file"):
            return f"通道 {self.name} 不支持文件，已跳过 {item.filename}"
        res = await self._send_file(item.path, item.data or media.read_bytes(item.path),
                                    item.filename)
        if item.caption and self.caps.get("text"):
            await self._send_text(item.caption)
        return res

    async def _fallback_image(self, item: SendItem) -> str:
        mode = self.opt("image_fallback", "split")
        if mode == "skip":
            return f"通道 {self.name} 不支持图片，已跳过 {item.filename}"
        return await self._send_markdown(
            f"`{item.filename}`（{media.human_size(len(item.data))}）\n\n"
            f"> 该通道不支持图片消息，请到源目录查看：{item.path}"
        )

    # ---------- 子类实现 ----------

    async def _send_text(self, text: str) -> str:
        raise NotImplementedError

    async def _send_markdown(self, text: str) -> str:
        return await self._send_text(text)

    async def _send_image(self, data: bytes, filename: str) -> str:
        raise NotImplementedError

    async def _send_file(self, path: str, data: bytes, filename: str) -> str:
        raise NotImplementedError

    # ---------- 测试 ----------

    async def test(self) -> str:
        self.require_configured()
        return await self._send_text(f"dir2chat 通道测试：{self.name}")

    def require_configured(self) -> None:
        pass


def build_channel(cfg: Dict[str, Any]) -> BaseChannel:
    from .qq_bot import QQBotChannel
    from .wecom_app import WecomAppChannel
    from .wecom_bot import WecomBotChannel
    from .wecom_webhook import WecomWebhookChannel

    registry = {
        "wecom_webhook": WecomWebhookChannel,
        "wecom_bot": WecomBotChannel,
        "wecom_app": WecomAppChannel,
        "qq_bot": QQBotChannel,
    }
    cls = registry.get(cfg.get("type", ""))
    if not cls:
        raise ValueError(f"未知通道类型: {cfg.get('type')}")
    return cls(cfg)


def channel_labels() -> List[Dict[str, Any]]:
    return [{"type": k, **v} for k, v in CHANNEL_SPECS.items()]
