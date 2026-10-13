"""企业微信智能机器人 · 长连接模式。

协议（wss://openws.work.weixin.qq.com）：
  1. 握手后发 aibot_subscribe，带 bot_id + secret
  2. 服务端推 aibot_msg_callback / aibot_event_callback
  3. 主动推送用 aibot_send_msg，body 支持 chatid / chat_type / msgtype
  4. 每 30s 发一次 ping 保活
  5. 图片 / 文件走临时素材三步握手：
     aibot_upload_media_init → aibot_upload_media_chunk × N → aibot_upload_media_finish
     拿到 media_id 后再用 aibot_send_msg 发 image / file 消息

限制：
  - 素材：图片 ≤10MB（png/jpg/jpeg/gif），文件 ≤20MB，语音 ≤2MB，视频 ≤10MB
  - 分片：单片 ≤512KB（base64 前），最多 100 片；素材有效期 3 天
  - 频率：上传单个机器人 30 次/分钟、1000 次/小时；
          消息单会话 30 条/分钟、1000 条/小时
  - aibot_send_msg 没有 text 类型，纯文本也用 markdown 承载
  - 机器人只能进**内部群**，互联群 / 上下游群 / 外部群不支持
  - chatid 只能从回调事件里拿，所以本通道会把收到的会话 ID 记下来，
    方便在 Web 界面直接选用
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import uuid
from typing import Any, Dict, List, Optional

from .. import media as _media
from .base import BaseChannel

try:  # websockets >= 14 把 asyncio 实现挪到了子模块
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover
    from websockets import connect as ws_connect  # type: ignore

WS_URL = "wss://openws.work.weixin.qq.com"
HEARTBEAT_SECONDS = 30

# 分片上限：单片 base64 前 512KB，最多 100 片
CHUNK_SIZE = 512 * 1024
MAX_CHUNKS = 100
DEFAULT_MAX_IMAGE = 10 * 1024 * 1024
DEFAULT_MAX_FILE = 20 * 1024 * 1024

# 分片上限：单片 base64 前 512KB，最多 100 片
CHUNK_SIZE = 512 * 1024
MAX_CHUNKS = 100
DEFAULT_MAX_IMAGE = 10 * 1024 * 1024
DEFAULT_MAX_FILE = 20 * 1024 * 1024


class WecomBotChannel(BaseChannel):
    type = "wecom_bot"

    def __init__(self, cfg: Dict[str, Any]) -> None:
        super().__init__(cfg)
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._hb: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._ready = asyncio.Event()
        self._stopping = False
        self.recent_chats: List[Dict[str, Any]] = []
        self._connected_at: Optional[str] = None

    def require_configured(self) -> None:
        self.require("bot_id", "secret", "chatid")

    @property
    def ws_url(self) -> str:
        """默认连官方；通道级 ws_url 或环境变量可用于自建代理与联调。"""
        return (self.opt("ws_url")
                or os.environ.get("DIR2CHAT_WECOM_BOT_WS")
                or WS_URL)

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._hb, self._task):
            if task:
                task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ready.clear()

    async def _run(self) -> None:
        import datetime

        backoff = 1
        while not self._stopping:
            try:
                async with ws_connect(self.ws_url, ping_interval=None,
                                      open_timeout=15) as ws:
                    self._ws = ws
                    loop = asyncio.get_running_loop()
                    sub_id = uuid.uuid4().hex
                    sub_fut: asyncio.Future = loop.create_future()
                    self._pending[sub_id] = sub_fut
                    read_task = asyncio.create_task(self._read_loop(ws))
                    try:
                        await ws.send(json.dumps({
                            "cmd": "aibot_subscribe",
                            "headers": {"req_id": sub_id},
                            "body": {
                                "bot_id": self.opt("bot_id"),
                                "secret": self.opt("secret"),
                            },
                        }))
                    except Exception:
                        read_task.cancel()
                        await ws.close()
                        continue
                    try:
                        ack = await asyncio.wait_for(sub_fut, timeout=15)
                    except asyncio.TimeoutError:
                        ack = {}
                    self._pending.pop(sub_id, None)
                    ec = ack.get("errcode", 0)
                    if ec != 0:
                        # 凭据无效：服务端会立即断开连接，不要误报 ready
                        self._last_error = (f"订阅被拒 errcode={ec} "
                                            f"{ack.get('errmsg', '')}")[:300]
                        print(f"[wecom_bot DEBUG] 订阅失败 errcode={ec} "
                              f"errmsg={ack.get('errmsg')!r}", flush=True)
                        read_task.cancel()
                        await ws.close()
                        continue
                    self._ready.set()
                    self._connected_at = datetime.datetime.now().isoformat(
                            timespec="seconds")
                    print("[wecom_bot DEBUG] aibot_subscribe 成功，长连接已建立",
                          flush=True)
                    backoff = 1
                    self._hb = asyncio.create_task(self._heartbeat())
                    await read_task
            except asyncio.CancelledError:
                return
            except Exception as exc:
                rc = getattr(exc, "rcode", None)
                reason = getattr(exc, "reason", None)
                self._last_error = f"{type(exc).__name__}: {exc}"[:300]
                try:
                    print(f"[wecom_bot DEBUG] 连接异常断开: "
                          f"{type(exc).__name__}: {exc} | rcode={rc} "
                          f"reason={reason!r}", flush=True)
                except Exception:
                    pass
            self._ready.clear()
            if self._hb:
                self._hb.cancel()
            if self._stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not self._ws:
                continue
            try:
                await self._ws.send(json.dumps({
                    "cmd": "ping", "headers": {"req_id": uuid.uuid4().hex},
                }))
            except Exception:
                return

    async def _read_loop(self, ws) -> None:
        async for raw in ws:
            try:
                frame = json.loads(raw)
            except Exception:
                print(f"[wecom_bot DEBUG] 收到非JSON帧: {raw!r}"[:300],
                      flush=True)
                continue
            cmd = frame.get("cmd", "")
            req_id = (frame.get("headers") or {}).get("req_id", "")
            if cmd in ("aibot_msg_callback", "aibot_event_callback"):
                self._remember_chat(frame.get("body") or {})
                try:
                    print(f"[wecom_bot DEBUG] {cmd}: "
                          f"{json.dumps(frame, ensure_ascii=False)[:800]}",
                          flush=True)
                except Exception:
                    pass
                continue
            if not req_id:
                continue
            fut = self._pending.pop(req_id, None)
            if fut and not fut.done():
                fut.set_result(frame)

    def _remember_chat(self, body: Dict[str, Any]) -> None:
        chatid = body.get("chatid") or (body.get("from") or {}).get("userid")
        if not chatid:
            return
        entry = {
            "chatid": chatid,
            "chattype": body.get("chattype", ""),
            "userid": (body.get("from") or {}).get("userid", ""),
        }
        self.recent_chats = [c for c in self.recent_chats
                             if c["chatid"] != chatid]
        self.recent_chats.insert(0, entry)
        self.recent_chats = self.recent_chats[:20]

    # ---------- 发送 ----------

    async def _await_ready(self, timeout: float = 20.0) -> None:
        if self._ready.is_set():
            return
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    async def _send_raw(self, cmd: str, body: Dict[str, Any]) -> Dict[str, Any]:
        await self._await_ready()
        if not self._ws:
            raise RuntimeError("长连接未建立")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        req_id = uuid.uuid4().hex
        self._pending[req_id] = fut
        try:
            await self._ws.send(json.dumps({
                "cmd": cmd, "headers": {"req_id": req_id}, "body": body,
            }))
            frame = await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError("企业微信长连接响应超时（30s）")
        errcode = frame.get("errcode", 0)
        if errcode != 0:
            raise RuntimeError(f"企业微信错误 {errcode}: {frame.get('errmsg', '')}")
        return frame

    # ---------- 临时素材上传 ----------

    async def _upload_media(self, data: bytes, filename: str, mtype: str) -> str:
        """init → chunk × N → finish，返回 3 天内有效的 media_id。"""
        total = len(data)
        chunks = max(1, math.ceil(total / CHUNK_SIZE))
        if chunks > MAX_CHUNKS:
            raise RuntimeError(
                f"{filename} 需要 {chunks} 个分片，超过上限 {MAX_CHUNKS}")

        init = await self._send_raw("aibot_upload_media_init", {
            "type": mtype,
            "filename": filename[:256],
            "total_size": total,
            "total_chunks": chunks,
            "md5": hashlib.md5(data).hexdigest(),
        })
        upload_id = (init.get("body") or {}).get("upload_id")
        if not upload_id:
            raise RuntimeError("上传初始化未返回 upload_id")

        for index in range(chunks):
            piece = data[index * CHUNK_SIZE:(index + 1) * CHUNK_SIZE]
            await self._send_raw("aibot_upload_media_chunk", {
                "upload_id": upload_id,
                "chunk_index": index,
                "base64_data": base64.b64encode(piece).decode("ascii"),
            })

        done = await self._send_raw("aibot_upload_media_finish",
                                    {"upload_id": upload_id})
        media_id = (done.get("body") or {}).get("media_id")
        if not media_id:
            raise RuntimeError("上传完成未返回 media_id")
        return media_id

    async def _push_media(self, media_id: str, msgtype: str) -> None:
        await self._send_raw("aibot_send_msg", {
            "chatid": self.opt("chatid"),
            "chat_type": int(self.opt("chat_type", 2)),
            "msgtype": msgtype,
            msgtype: {"media_id": media_id},
        })

    # ---------- 发送 ----------

    async def _send_text(self, text: str) -> str:
        # aibot_send_msg 没有 text 类型，用 markdown 承载
        return await self._send_markdown(text)

    async def _send_markdown(self, text: str) -> str:
        await self._send_raw("aibot_send_msg", {
            "chatid": self.opt("chatid"),
            "chat_type": int(self.opt("chat_type", 2)),
            "msgtype": "markdown",
            "markdown": {"content": text},
        })
        return "ok"

    async def _send_image(self, data: bytes, filename: str) -> str:
        limit = int(self.opt("image_max_bytes", DEFAULT_MAX_IMAGE))
        if len(data) > limit:
            data, suffix = _media.compress_image_to_limit(data, limit)
            filename = os.path.splitext(filename)[0] + suffix
        media_id = await self._upload_media(data, filename, "image")
        await self._push_media(media_id, "image")
        return "ok"

    async def _send_file(self, path: str, data: bytes, filename: str) -> str:
        if len(data) > DEFAULT_MAX_FILE:
            raise RuntimeError(
                f"{filename} 大小 {len(data)} 字节，超过 20MB 上限")
        media_id = await self._upload_media(data, filename, "file")
        await self._push_media(media_id, "file")
        return "ok"

    async def health(self) -> Dict[str, Any]:
        return {
            "ready": self._ready.is_set(),
            "detail": f"长连接{'已建立' if self._ready.is_set() else '未建立'}"
                      + (f" · {self._connected_at}" if self._connected_at else ""),
            "last_error": self._last_error,
        }
