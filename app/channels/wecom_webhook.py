"""企业微信群机器人 Webhook（客户端 v4.1.41 起改名为「消息推送」）。

能力：文本 / markdown / 图片 / 文件
硬限制：
  - 20 条/分钟（调用方自己做限流，见 dispatcher）
  - 文本 ≤ 2048 字节，markdown ≤ 4096 字节
  - 图片 ≤ 2MB（JPG/PNG，base64 + md5）
  - 文件 5B ~ 20MB（base64 + md5）；注意 file 消息**无法指定文件名**，
    客户端按内容推断，所以 .md 建议在正文首行自带标题
  - 仅企业内部群可用，含外部联系人的群里没有「消息推送」入口
"""

from __future__ import annotations

import hashlib
import base64
from typing import Any, Dict

import httpx

from .base import BaseChannel

MAX_FILE_BYTES = 20 * 1024 * 1024


class WecomWebhookChannel(BaseChannel):
    type = "wecom_webhook"

    def require_configured(self) -> None:
        self.require("webhook_url")
        url = str(self.opt("webhook_url"))
        if "qyapi.weixin.qq.com" not in url:
            raise RuntimeError("Webhook 地址看起来不对，应包含 qyapi.weixin.qq.com")

    @property
    def _url(self) -> str:
        return str(self.opt("webhook_url"))

    async def _post(self, payload: Dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self._url, json=payload)
        try:
            body = resp.json()
        except Exception:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        errcode = body.get("errcode", -1)
        if errcode != 0:
            raise RuntimeError(self._explain(errcode, body.get("errmsg", "")))
        return "ok"

    @staticmethod
    def _explain(errcode: int, errmsg: str) -> str:
        known = {
            45009: "接口频率超限（每机器人 20 条/分钟），请降低发送频率",
            93000: "Webhook 地址不合法，检查 key 是否正确或是否多了空格",
            93001: "机器人不存在，可能已被删除",
            93002: "机器人已被移出群聊，需要重新添加",
            40013: "无效的 corpid",
        }
        return f"企业微信错误 {errcode}: {known.get(errcode, errmsg)}"

    async def _send_text(self, text: str) -> str:
        return await self._post({
            "msgtype": "text",
            "text": {"content": text[:2048]},
        })

    async def _send_markdown(self, text: str) -> str:
        return await self._post({
            "msgtype": "markdown",
            "markdown": {"content": text},
        })

    async def _send_image(self, data: bytes, filename: str) -> str:
        return await self._post({
            "msgtype": "image",
            "image": {
                "base64": base64.b64encode(data).decode("ascii"),
                "md5": hashlib.md5(data).hexdigest(),
            },
        })

    async def _send_file(self, path: str, data: bytes, filename: str) -> str:
        if not (5 <= len(data) <= MAX_FILE_BYTES):
            raise RuntimeError(
                f"文件 {filename} 大小 {len(data)} 字节，超出企业微信 5B~20MB 限制"
            )
        return await self._post({
            "msgtype": "file",
            "file": {
                "base64": base64.b64encode(data).decode("ascii"),
                "md5": hashlib.md5(data).hexdigest(),
            },
        })
