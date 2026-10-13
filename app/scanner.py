"""定时目录扫描。

用轮询而不是 inotify：这类产物目录往往是「一次性批量写入」，
轮询更稳，也天然跨挂载点（NFS / Docker volume 上 inotify 常常不工作）。

判定「文件写完了」靠两点：
  1. mtime 距今 >= stable_seconds（默认 5s），避免抓到半截文件
  2. 连续两轮 size 不变（记录在 _size_cache），双保险

性能相关的三个可调项都放在配置 scan 段（默认值见 config.DEFAULT_CONFIG）：
  - max_files_per_round      每轮最多推送多少个「待推送」文件；
                             已推送过的历史文件不占这个名额，因此不会挤掉新文件
  - digest_cache_max_entries 内容哈希缓存条数上限（0=关闭缓存）
  - skip_dirs_idle_days      子目录超过 N 天无变动则整棵子树跳过（0=关闭）
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set, Tuple

from . import media, store
from . import schedule as schedule_mod
from .config import Config

DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024

# 以下三个默认值需与 config.DEFAULT_CONFIG["scan"] 保持一致：
# 它们是配置文件缺失对应键时的兜底值，改一处请同步另一处。
DEFAULT_MAX_FILES_PER_ROUND = 500
DEFAULT_DIGEST_CACHE_ENTRIES = 5000
DEFAULT_SKIP_DIRS_IDLE_DAYS = 0


class DigestCache:
    """文件内容哈希缓存：path -> (size, mtime_ns, digest)。

    只要文件的 size 与 mtime（纳秒精度）都没变，就认为内容没变，
    直接复用上次算好的 sha256，避免每轮把历史文件整份读一遍重算。

    ⚠️ 这是纯性能层：命中与否都不改变推送语义。真正的去重判断依然由
    store.already_sent() 用 sha256 完成；缓存 miss 顶多是重算一次哈希。
    """

    def __init__(self, max_entries: int = DEFAULT_DIGEST_CACHE_ENTRIES) -> None:
        self.max_entries = max(0, int(max_entries or 0))
        self._data: "OrderedDict[str, Tuple[int, int, str]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._data)

    def get(self, path: str, st: os.stat_result) -> Optional[str]:
        """命中返回上次的 digest；否则返回 None（调用方需重算）。"""
        if self.max_entries <= 0:
            return None
        item = self._data.get(path)
        if item is None:
            self.misses += 1
            return None
        cached_size, cached_mtime_ns, digest = item
        if cached_size == st.st_size and cached_mtime_ns == st.st_mtime_ns:
            self._data.move_to_end(path)   # 命中 → 移到队尾
            self.hits += 1
            return digest
        self.misses += 1                   # size 或 mtime 变了 → 这张身份证作废
        return None

    def put(self, path: str, st: os.stat_result, digest: str) -> None:
        if self.max_entries <= 0:
            return
        self._data[path] = (st.st_size, st.st_mtime_ns, digest)
        self._data.move_to_end(path)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)  # 超限 → 淘汰最久未用的

    def drop_vanished(self, alive: Set[str]) -> int:
        """剔除磁盘上已不存在的条目，防止文件删除/归档后缓存无限增长。"""
        if not self._data:
            return 0
        gone = [k for k in self._data if k not in alive]
        for key in gone:
            self._data.pop(key, None)
        return len(gone)


class Scanner:
    def __init__(self) -> None:
        self._size_cache: Dict[str, int] = {}
        self._digests = DigestCache()
        self._running = False
        self.last_scan_at: Optional[float] = None
        self.last_found = 0
        self.last_idle_dirs_skipped = 0
        self._task: Optional[asyncio.Task] = None

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            interval = int(Config.get().get("scan", {}).get("interval_seconds", 60))
            try:
                cfg = Config.get()
                scan_cfg = cfg.get("scan", {})
                if scan_cfg.get("enabled", True):
                    # 定时由调用方（main._ticker）按规则判定；这里兜底只按全局开关。
                    await self.scan_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                store.log("error", f"扫描异常: {type(exc).__name__}: {exc}")
            await asyncio.sleep(max(5, interval))

    @staticmethod
    def _rule_active(rule: Dict[str, Any]) -> bool:
        """该规则当前是否在允许推送的时间窗口内。"""
        return schedule_mod.is_active_now(rule.get("schedule") or {})

    # ---------- 扫描 ----------

    @staticmethod
    def _match(name: str, patterns: List[str]) -> bool:
        """大小写不敏感匹配；裸写 `png` 会自动规整为 `*.png`。"""
        if not patterns:
            return False
        low = name.lower()
        for raw in patterns:
            pat = (raw or "").strip().lower()
            if not pat:
                continue
            if "*" not in pat and "." not in pat:
                pat = f"*.{pat}"
            if fnmatch.fnmatch(low, pat):
                return True
        return False

    def _iter_files(self, rule: Dict[str, Any], idle_seconds: float = 0.0):
        root = rule.get("watch_dir", "")
        if not root or not os.path.isdir(root):
            return
        include = rule.get("include") or ["*"]
        exclude = rule.get("exclude") or []
        recursive = bool(rule.get("recursive", True))
        max_bytes = int(rule.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES))
        now_ts = time.time()

        if recursive:
            walker = os.walk(root)
        else:
            walker = [(root, [], os.listdir(root))]

        for dirpath, dirnames, filenames in walker:
            keep: List[str] = []
            for d in dirnames:
                if d.startswith(".") or self._match(d, exclude):
                    continue          # 隐藏目录 / 被 exclude 命中 → 整棵子树不进入
                if idle_seconds > 0:
                    # 目录级「冷却跳过」：太久没有动静的子目录不再进去扫
                    try:
                        d_mtime = os.stat(os.path.join(dirpath, d)).st_mtime
                    except OSError:
                        continue
                    if now_ts - d_mtime > idle_seconds:
                        self.last_idle_dirs_skipped += 1
                        continue
                keep.append(d)
            dirnames[:] = keep

            for name in filenames:
                if name.startswith(".") or name.endswith((".tmp", ".part", ".crdownload")):
                    continue
                if not self._match(name, include) or self._match(name, exclude):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if not st.st_size or st.st_size > max_bytes:
                    continue
                yield path, st

    async def scan_once(self, rule_ids: Optional[set] = None) -> List[Dict[str, Any]]:
        """扫描一轮。

        rule_ids 为 None 时扫描所有启用的规则（手动触发不受规则定时限制）；
        传入集合时只扫描其中的规则（定时循环用，避免窗口外规则被扫到）。
        """
        cfg = Config.get()
        scan_cfg = cfg.get("scan", {})
        stable = int(scan_cfg.get("stable_seconds", 5))
        max_round = int(scan_cfg.get("max_files_per_round", DEFAULT_MAX_FILES_PER_ROUND))
        cache_entries = int(scan_cfg.get("digest_cache_max_entries",
                                         DEFAULT_DIGEST_CACHE_ENTRIES))
        idle_seconds = float(scan_cfg.get("skip_dirs_idle_days",
                                          DEFAULT_SKIP_DIRS_IDLE_DAYS) or 0) * 86400
        self._digests.max_entries = max(0, cache_entries)
        self.last_idle_dirs_skipped = 0
        now = time.time()

        jobs: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for rule in cfg.get("rules", []):
            if not rule.get("enabled", True):
                continue
            if rule_ids is not None and rule.get("id") not in rule_ids:
                continue
            channel_ids = [c for c in rule.get("channels", []) if c]
            if not channel_ids:
                continue
            if not os.path.isdir(rule.get("watch_dir", "")):
                store.log("warning", f"规则「{rule.get('name')}」目录不存在: "
                                     f"{rule.get('watch_dir')}")
                continue

            pending: List[Dict[str, Any]] = []
            for path, st in self._iter_files(rule, idle_seconds):
                if now - st.st_mtime < stable:
                    continue
                if self._size_cache.get(path) not in (None, st.st_size):
                    continue  # 上一轮 size 不同，说明还在写，等下一轮
                self._size_cache[path] = st.st_size
                seen.add(path)

                digest = self._digests.get(path, st)
                if digest is None:
                    try:
                        digest = media.sha256_file(path)
                    except OSError:
                        continue
                    self._digests.put(path, st, digest)

                # 前置去重：只留下还没推过的通道。若全部推过则直接丢弃，
                # 不占 max_files_per_round 名额（否则历史文件会把新文件挤出去）。
                needed = [c for c in channel_ids
                          if not store.already_sent(rule["id"], c, path, digest)]
                if not needed:
                    continue

                kind = media.file_kind(path)
                pending.append({
                    "rule_id": rule["id"],
                    "path": path,
                    "sha256": digest,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "kind": kind,
                    "channel_ids": needed,
                    "caption": self._caption(rule, path),
                    "send_markdown_body": bool(rule.get("send_markdown_body", True)),
                })
                if len(pending) >= max_round:
                    break

            for item in pending:
                for channel_id in item["channel_ids"]:
                    jobs.append({**item, "channel_id": channel_id})

        if rule_ids is None:
            # 只在整轮全规则扫描后才清理，避免误删其它规则刚缓存的条目
            self._digests.drop_vanished(seen)

        self.last_scan_at = now
        self.last_found = len(jobs)
        return jobs

    @staticmethod
    def _caption(rule: Dict[str, Any], path: str) -> str:
        template = rule.get("caption_template") or "{filename}"
        name = os.path.basename(path)
        parent = os.path.basename(os.path.dirname(path))
        try:
            return template.format(
                filename=name,
                name=os.path.splitext(name)[0],
                dirname=parent,
                path=path,
            )
        except Exception:
            return name
