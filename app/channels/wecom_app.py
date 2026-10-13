"""企业微信自建应用 · 应用消息 API。

corpid + corpsecret + agentid 三件套，可以主动发给指定成员 / 部门 / 标签，
是「发到指定的人」唯一完全官方的通道。

流程：
  gettoken → （图片/文件先）media/upload 换 media_id → message/send
素材限制：image ≤ 2MB（JPG/PNG），file ≤ 20MB；临时素材 3 天过期，
所以这里不缓存 media_id，每次现传。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from .base import BaseChannel

API = "https://qyapi.weixin.qq.com/cgi-bin"
MAX_FILE_BYTES = 20 * 1024 * 1024

_TOKEN_CACHE: Dict[str, Tuple[str, float]] = {}
_TOKEN_LOCK = asyncio.Lock()


class WecomAppChannel(BaseChannel):
    type = "wecom_app"

    def require_configured(self) -> None:
        self.require("corpid", "corpsecret", "agentid")

    # ---------- token ----------

    async def _token(self) -> str:
        key = f"{self.opt('corpid')}|{self.opt('corpsecret')}"
        now = time.time()
        cached = _TOKEN_CACHE.get(key)
        if cached and cached[1] - now > 300:
            return cached[0]
        async with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(key)
            if cached and cached[1] - now > 300:
                return cached[0]
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{API}/gettoken", params={
                    "corpid": self.opt("corpid"),
                    "corpsecret": self.opt("corpsecret"),
                })
            body = resp.json()
            if body.get("errcode", 0) != 0:
                raise RuntimeError(
                    f"获取 access_token 失败 {body.get('errcode')}: {body.get('errmsg')}")
            _TOKEN_CACHE[key] = (body["access_token"], now + 7200)
            return body["access_token"]

    async def _call(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        token = await self._token()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{API}/{path}",
                                     params={"access_token": token},
                                     json=payload)
        body = resp.json()
        if body.get("errcode", 0) != 0:
            raise RuntimeError(
                f"企业微信错误 {body.get('errcode')}: {body.get('errmsg')}")
        return body

    async def _upload(self, data: bytes, filename: str, mtype: str) -> str:
        token = await self._token()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API}/media/upload",
                params={"access_token": token, "type": mtype},
                files={"media": (filename, data, "application/octet-stream")},
            )
        body = resp.json()
        if body.get("errcode", 0) != 0:
            raise RuntimeError(
                f"上传素材失败 {body.get('errcode')}: {body.get('errmsg')}")
        return body["media_id"]

    def _audience(self) -> Dict[str, Any]:
        touser = str(self.opt("touser", "@all"))
        return {"touser": touser, "agentid": str(self.opt("agentid"))}

    # ---------- 发送 ----------

    async def _send_text(self, text: str) -> str:
        await self._call("message/send", {
            **self._audience(), "msgtype": "text", "text": {"content": text[:2048]},
        })
        return "ok"

    async def _send_markdown(self, text: str) -> str:
        await self._call("message/send", {
            **self._audience(), "msgtype": "markdown",
            "markdown": {"content": text},
        })
        return "ok"

    async def _send_image(self, data: bytes, filename: str) -> str:
        media_id = await self._upload(data, filename, "image")
        await self._call("message/send", {
            **self._audience(), "msgtype": "image", "image": {"media_id": media_id},
        })
        return "ok"

    async def _send_file(self, path: str, data: bytes, filename: str) -> str:
        if len(data) > MAX_FILE_BYTES:
            raise RuntimeError(f"文件 {filename} 超过 20MB 限制")
        media_id = await self._upload(data, filename, "file")
        await self._call("message/send", {
            **self._audience(), "msgtype": "file", "file": {"media_id": media_id},
        })
        return "ok"
