"""QQ 官方机器人（QQ 开放平台 OpenAPI v2）。

流程：
  getAppAccessToken → （图片/文件）POST /v2/{groups|users}/{openid}/files 换
  file_info → POST 同场景 /messages 发消息（msg_type=7 富媒体）

要点：
  - 认证头是 `Authorization: QQBot <access_token>`
  - 群聊与单聊的上传端点相互独立，file_info 不能跨场景使用
  - file_info 有 TTL，不缓存，每次现传
  - file_type: 1 图片 / 2 视频 / 3 语音 / 4 文件；file_type=4 必须带 file_name
  - 群聊主动推送需群主开启「机器人主动在群聊内发言」；
    频控：机器人维度 60 qpm（未认证 30），单群 20 qpm
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Dict, Tuple

import httpx

from .base import BaseChannel

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_PROD = "https://api.sgroup.qq.com"
API_SANDBOX = "https://sandbox.api.sgroup.qq.com"

_TOKEN_CACHE: Dict[str, Tuple[str, float]] = {}
_TOKEN_LOCK = asyncio.Lock()

FILE_TYPE = {
    ".png": 1, ".jpg": 1, ".jpeg": 1, ".webp": 1, ".bmp": 1, ".gif": 1,
    ".mp4": 2, ".avi": 2, ".mov": 2,
    ".amr": 3, ".silk": 3, ".mp3": 3,
}


class QQBotChannel(BaseChannel):
    type = "qq_bot"

    def require_configured(self) -> None:
        self.require("app_id", "client_secret", "target_openid")

    @property
    def _base(self) -> str:
        return API_SANDBOX if self.opt("sandbox") else API_PROD

    @property
    def _scope(self) -> str:
        return "groups" if self.opt("target_type", "group") == "group" else "users"

    @property
    def _target(self) -> str:
        return str(self.opt("target_openid"))

    # ---------- token ----------

    async def _token(self) -> str:
        key = f"{self.opt('app_id')}|{self.opt('client_secret')}"
        now = time.time()
        cached = _TOKEN_CACHE.get(key)
        if cached and cached[1] - now > 300:
            return cached[0]
        async with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(key)
            if cached and cached[1] - now > 300:
                return cached[0]
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(TOKEN_URL, json={
                    "appId": str(self.opt("app_id")),
                    "clientSecret": str(self.opt("client_secret")),
                })
            body = resp.json()
            token = body.get("access_token")
            if not token:
                raise RuntimeError(f"获取 QQ access_token 失败: {str(body)[:200]}")
            expires = int(body.get("expires_in", 7200))
            _TOKEN_CACHE[key] = (token, now + expires)
            return token

    def _headers(self, token: str) -> Dict[str, str]:
        return {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}

    async def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        token = await self._token()
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=self._headers(token))
        if resp.status_code >= 400:
            raise RuntimeError(f"QQ API {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except Exception:
            return {}

    @property
    def _messages_url(self) -> str:
        return f"/v2/{self._scope}/{self._target}/messages"

    @property
    def _files_url(self) -> str:
        return f"/v2/{self._scope}/{self._target}/files"

    # ---------- 发送 ----------

    async def _send_text(self, text: str) -> str:
        await self._post(self._messages_url, {
            "content": text[:4000], "msg_type": 0, "msg_seq": 1,
        })
        return "ok"

    async def _send_markdown(self, text: str) -> str:
        try:
            await self._post(self._messages_url, {
                "msg_type": 2, "markdown": {"content": text}, "msg_seq": 1,
            })
            return "ok"
        except RuntimeError as exc:
            # 自定义 markdown 对语法有要求，失败时降级为纯文本，保证消息送达
            await self._post(self._messages_url, {
                "content": text[:4000], "msg_type": 0, "msg_seq": 2,
            })
            return f"markdown 被拒（{exc}），已降级为纯文本"

    async def _send_image(self, data: bytes, filename: str) -> str:
        return await self._send_media(data, filename, 1)

    async def _send_file(self, path: str, data: bytes, filename: str) -> str:
        import os
        ftype = FILE_TYPE.get(os.path.splitext(filename)[1].lower(), 4)
        return await self._send_media(data, filename, ftype)

    async def _send_media(self, data: bytes, filename: str, file_type: int) -> str:
        payload: Dict[str, Any] = {
            "file_type": file_type,
            "file_data": base64.b64encode(data).decode("ascii"),
            "srv_send_msg": False,
        }
        if file_type == 4:
            payload["file_name"] = filename
        uploaded = await self._post(self._files_url, payload)
        file_info = uploaded.get("file_info")
        if not file_info:
            raise RuntimeError(f"QQ 上传未返回 file_info: {str(uploaded)[:200]}")
        await self._post(self._messages_url, {
            "msg_type": 7, "media": {"file_info": file_info}, "msg_seq": 1,
        })
        return "ok"
