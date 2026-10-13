"""发送调度：串行队列 + 每通道限流 + 失败重试 + 结果落库。

所有发送都走一个全局串行 worker，天然满足企业微信 20 条/分钟、
QQ 单群 20 qpm 这类频控；每通道额外加 channel_interval_seconds 的间隔。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from typing import Any, Dict, List, Optional

from . import media, store
from .channels import SendItem, build_channel
from .config import Config, new_id


class Dispatcher:
    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._channels: Dict[str, Any] = {}
        self._sigs: Dict[str, str] = {}
        self._last_send: Dict[str, float] = {}
        self._worker: Optional[asyncio.Task] = None
        self._running = False
        self.inflight: List[str] = []

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._running = True
        self.sync()
        self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        for ch in self._channels.values():
            try:
                await ch.stop()
            except Exception:
                pass
        if self._worker:
            self._worker.cancel()

    def sync(self) -> None:
        """按当前配置建立 / 重建 / 停用通道实例。"""
        alive = set()
        for cfg in Config.channels():
            cid = cfg.get("id")
            if not cid:
                continue
            alive.add(cid)
            sig = json.dumps({
                "type": cfg.get("type"),
                "options": cfg.get("options"),
                "enabled": cfg.get("enabled"),
            }, sort_keys=True)
            if self._sigs.get(cid) == sig and cid in self._channels:
                continue
            old = self._channels.pop(cid, None)
            if old is not None:
                try:
                    asyncio.get_running_loop().create_task(old.stop())
                except RuntimeError:
                    pass
            if not cfg.get("enabled"):
                self._sigs.pop(cid, None)
                continue
            try:
                channel = build_channel(cfg)
            except Exception as exc:
                store.log("error", f"通道 {cfg.get('name')} 初始化失败: {exc}")
                continue
            self._channels[cid] = channel
            self._sigs[cid] = sig
            if cfg.get("enabled"):
                try:
                    asyncio.get_running_loop().create_task(channel.start())
                except RuntimeError:
                    pass
        for cid in list(self._channels):
            if cid not in alive:
                old = self._channels.pop(cid, None)
                self._sigs.pop(cid, None)
                if old is not None:
                    try:
                        asyncio.get_running_loop().create_task(old.stop())
                    except RuntimeError:
                        pass

    def channel(self, channel_id: str):
        return self._channels.get(channel_id)

    def channel_states(self) -> List[Dict[str, Any]]:
        states = []
        for cfg in Config.channels():
            states.append({
                "id": cfg.get("id"),
                "name": cfg.get("name"),
                "type": cfg.get("type"),
                "enabled": bool(cfg.get("enabled")),
                "live": cfg.get("id") in self._channels,
            })
        return states

    # ---------- 入队 ----------

    async def submit(self, jobs: List[Dict[str, Any]]) -> int:
        count = 0
        for job in jobs:
            await self._queue.put(job)
            count += 1
        return count

    async def _run(self) -> None:
        while self._running:
            job = await self._queue.get()
            try:
                await self._send(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                store.log("error", f"发送任务异常: {exc}")
            finally:
                self._queue.task_done()

    # ---------- 单条发送 ----------

    def _build_item(self, job: Dict[str, Any]) -> SendItem:
        path = job["path"]
        kind = job["kind"]
        item = SendItem(
            kind=kind,
            path=path,
            filename=os.path.basename(path),
            caption=job.get("caption", ""),
            markdown_mode=job.get("markdown_mode", "auto"),
        )
        if kind == "markdown":
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    item.text = fh.read()
            except OSError:
                item.text = ""
        else:
            item.data = media.read_bytes(path)
        return item

    async def _send(self, job: Dict[str, Any]) -> None:
        channel_id = job["channel_id"]
        channel = self._channels.get(channel_id)
        path = job["path"]
        if channel is None:
            store.record(job["rule_id"], channel_id, path, job["sha256"],
                         job["size"], job["kind"], "skipped", "通道未启用或未配置")
            return

        limits = Config.get().get("limits", {})
        max_retry = int(limits.get("max_retry", 3))
        backoff = int(limits.get("retry_backoff_seconds", 5))
        interval = float(limits.get("channel_interval_seconds", 1.0))

        wait = interval - (time.time() - self._last_send.get(channel_id, 0))
        if wait > 0:
            await asyncio.sleep(wait)

        item = self._build_item(job)
        last_error = ""
        for attempt in range(1, max_retry + 1):
            try:
                await channel.deliver(item)
                self._last_send[channel_id] = time.time()
                store.record(job["rule_id"], channel_id, path, job["sha256"],
                             job["size"], job["kind"], "ok")
                store.log("info", f"已发送 {os.path.basename(path)} → {channel.name}")
                return
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._last_error_touch(channel, last_error)
                if attempt < max_retry:
                    await asyncio.sleep(backoff * attempt)
                else:
                    store.log("error", f"发送失败 {os.path.basename(path)} → "
                                       f"{channel.name}: {last_error}")

        store.record(job["rule_id"], channel_id, path, job["sha256"],
                     job["size"], job["kind"], "failed", last_error)

    @staticmethod
    def _last_error_touch(channel, message: str) -> None:
        try:
            channel._last_error = message[:300]
        except Exception:
            pass

    # ---------- 手动操作 ----------

    async def test_channel(self, channel_id: str) -> Dict[str, Any]:
        cfg = Config.get_channel(channel_id)
        if not cfg:
            raise RuntimeError("通道不存在")
        channel = self._channels.get(channel_id)
        if channel is None:
            channel = build_channel(cfg)
            try:
                await channel.start()
            except Exception:
                pass
        try:
            detail = await channel.test()
            return {"ok": True, "detail": detail or "已发送测试消息"}
        finally:
            if channel_id not in self._channels:
                await channel.stop()

    async def health(self, channel_id: str) -> Dict[str, Any]:
        channel = self._channels.get(channel_id)
        if channel is None:
            return {"ready": False, "detail": "通道未启用", "last_error": None}
        try:
            return await channel.health()
        except Exception as exc:
            return {"ready": False, "detail": str(exc), "last_error": str(exc)}

    def recent_chats(self, channel_id: str) -> List[Dict[str, Any]]:
        channel = self._channels.get(channel_id)
        return getattr(channel, "recent_chats", []) if channel else []

    def queue_size(self) -> int:
        return self._queue.qsize()
