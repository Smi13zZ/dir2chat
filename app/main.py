"""dir2chat —— 目录监听 + 多通道路由 + Web 配置界面。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .channels import channel_labels
from .config import (CHANNEL_SPECS, Config, default_channel, default_rule,
                     default_schedule, new_id)
from .dispatcher import Dispatcher
from .scanner import Scanner
from . import schedule as schedule_mod
from . import systemtime

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_version() -> str:
    try:
        with open(os.path.join(_APP_ROOT, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip() or "dev"
    except Exception:
        return "dev"


APP_VERSION = _read_version()

app = FastAPI(title="dir2chat", version=APP_VERSION)
scanner = Scanner()
dispatcher = Dispatcher()
_started_at = time.time()


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# --------------------------------------------------------------------------
# 生命周期
# --------------------------------------------------------------------------

_MIN_INTERVAL = 5            # 最小轮询间隔（秒）
_rule_next_due: Dict[str, float] = {}   # 规则 id → 下次可扫描的时间戳


def _rule_interval(rule: Dict[str, Any]) -> int:
    """规则的有效轮询间隔：规则自带 > 0 用规则自己的，否则用全局。"""
    global_interval = int(Config.get().get("scan", {}).get("interval_seconds", 60) or 60)
    try:
        own = int(rule.get("interval_seconds") or 0)
    except (TypeError, ValueError):
        own = 0
    return max(_MIN_INTERVAL, own if own > 0 else global_interval)


async def _ticker() -> None:
    """扫描 → 入队，循环执行。

    - 定时是「每条规则独立控制」的：进入某规则的时间窗口才扫该规则。
    - 轮询间隔也支持规则级覆盖：每条规则各自记录「下次到期时间」，
      到期才扫。全局间隔只是默认值。
    - 若有规则启用了定时，下一次唤醒还会取「距现在最近的一次相位切换」，
      避免窗口边界多等一个轮询周期。
    """
    _prev_active: Dict[str, bool] = {}
    while True:
        try:
            cfg = Config.get()
            scan_cfg = cfg.get("scan", {})
            if scan_cfg.get("enabled", True):
                now = time.time()
                due_rules = []
                for rule in cfg.get("rules", []):
                    sched = rule.get("schedule") or {}
                    rid = rule.get("id")
                    active = schedule_mod.is_active_now(sched)
                    prev = _prev_active.get(rid, True)
                    if active and not prev:
                        store.log("info", f"规则「{rule.get('name')}」回到定时窗口，恢复推送")
                    elif not active and prev:
                        phase = schedule_mod.phase_info(sched)
                        when = phase.get("next_change")
                        msg = f"规则「{rule.get('name')}」窗口外，暂停推送"
                        if when:
                            msg += f"，将于 {when} 恢复"
                        store.log("info", msg)
                    _prev_active[rid] = active

                    if not (active and rule.get("enabled", True) and rule.get("channels")):
                        continue
                    # 到点才扫这条规则
                    due_at = _rule_next_due.get(rid)
                    if due_at is None or now >= due_at:
                        _rule_next_due[rid] = now + _rule_interval(rule)
                        due_rules.append(rid)
                if due_rules:
                    jobs = await scanner.scan_once(rule_ids=set(due_rules))
                    if jobs:
                        await dispatcher.submit(jobs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            store.log("error", f"扫描循环异常: {type(exc).__name__}: {exc}")
        await asyncio.sleep(_next_wake_delay())


def _next_wake_delay() -> float:
    """距下一次需要唤醒的秒数。

    取三者最小：
      1. 所有启用规则里，最早的一条「距其下次到期」的剩余秒数
      2. 最近的定时相位切换
      3. 全局间隔（兜底，防止规则被删/禁用后无唤醒源）
    """
    cfg = Config.get()
    scan_cfg = cfg.get("scan", {})
    global_interval = max(_MIN_INTERVAL, int(scan_cfg.get("interval_seconds", 60) or 60))
    now = time.time()
    delay = float(global_interval)

    for rule in cfg.get("rules", []):
        sched = rule.get("schedule") or {}
        rid = rule.get("id")
        # 该规则的下次到期
        if (rule.get("enabled", True) and rule.get("channels")
                and schedule_mod.is_active_now(sched)):
            due_at = _rule_next_due.get(rid)
            if due_at is not None:
                delay = min(delay, max(1.0, due_at - now))
            else:
                delay = min(delay, 1.0)     # 还没排过，尽快来一轮
        # 定时相位切换
        if sched.get("enabled"):
            win = schedule_mod.seconds_to_next_change(sched)
            if win is not None:
                delay = min(delay, max(1.0, win + 1.0))
    return max(1.0, delay)


@app.on_event("startup")
async def _startup() -> None:
    Config.load()
    await dispatcher.start()
    asyncio.create_task(_ticker())
    asyncio.create_task(_time_sync_loop())
    store.log("info", "dir2chat 已启动")


async def _time_sync_loop() -> None:
    """按 time.auto_sync_hours 周期自动与 NTP 服务器对时（mode=auto 时）。

    首次启动延迟 20 秒，避免容器刚起来网络还没就绪就报错。
    """
    await asyncio.sleep(20)
    while True:
        try:
            cfg = Config.get()
            tcfg = cfg.get("time", {}) or {}
            hours = float(tcfg.get("auto_sync_hours") or 6)
            if tcfg.get("mode", "auto") == "auto":
                server = (tcfg.get("ntp_server") or systemtime.DEFAULT_NTP).strip()
                res = systemtime.sync_ntp(server, timeout=5.0)
                if res.get("ok"):
                    tcfg["last_sync_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    tcfg["last_sync_result"] = f"成功（调整 {res.get('adjusted_seconds')}s）"
                    Config.save()
                else:
                    tcfg["last_sync_result"] = f"失败：{res.get('reason')}"
                    Config.save()
            delay = max(600.0, hours * 3600)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            store.log("error", f"自动对时循环异常: {type(exc).__name__}: {exc}")
            delay = 3600.0
        await asyncio.sleep(delay)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await dispatcher.stop()


# --------------------------------------------------------------------------
# 状态
# --------------------------------------------------------------------------

@app.get("/api/status")
async def api_status() -> Dict[str, Any]:
    cfg = Config.get()
    stats = store.stats()
    scan_cfg = cfg.get("scan", {})
    ch_map = {c.get("id"): c.get("name") for c in cfg.get("channels", [])}
    rules_out = []
    for r in cfg.get("rules", []):
        sched = r.get("schedule") or {}
        rules_out.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "enabled": r.get("enabled", True),
            "schedule": {
                "enabled": bool(sched.get("enabled", False)),
                "active": schedule_mod.is_active_now(sched),
            },
            "interval_seconds": _rule_interval(r),
            "own_interval_seconds": int(r.get("interval_seconds") or 0),
            "next_due_in": (max(0, int(_rule_next_due[r["id"]] - time.time()))
                            if r.get("id") in _rule_next_due else None),
            "channels": [
                {"id": cid, "name": ch_map.get(cid, cid)}
                for cid in (r.get("channels") or []) if cid
            ],
        })
    return {
        "version": APP_VERSION,
        "uptime_seconds": int(time.time() - _started_at),
        "scan": {
            "enabled": scan_cfg.get("enabled", True),
            "interval_seconds": scan_cfg.get("interval_seconds", 60),
            "last_scan_at": scanner.last_scan_at,
            "last_found": scanner.last_found,
        },
        "queue": dispatcher.queue_size(),
        "stats": stats,
        "channels": dispatcher.channel_states(),
        "rules": rules_out,
    }


@app.get("/api/channel-types")
async def api_channel_types() -> List[Dict[str, Any]]:
    return channel_labels()


@app.get("/api/history")
async def api_history(limit: int = 100, only_fail: bool = False):
    return store.recent(limit=min(limit, 500), only_fail=only_fail)


@app.get("/api/events")
async def api_events(limit: int = 100):
    return store.recent_events(limit=min(limit, 500))


@app.post("/api/history/purge")
async def api_purge_history():
    """清空全部发送记录，下一轮扫描会重新推送目录里所有文件。"""
    deleted = store.clear_sent()
    return {"ok": True, "deleted": deleted}


@app.post("/api/events/purge")
async def api_purge_events():
    """清空全部事件日志。"""
    deleted = store.clear_events()
    return {"ok": True, "deleted": deleted}


# --------------------------------------------------------------------------
# 配置整体读写
# --------------------------------------------------------------------------

@app.get("/api/config")
async def api_get_config() -> Dict[str, Any]:
    return Config.get()


class ConfigPayload(BaseModel):
    scan: Optional[Dict[str, Any]] = None
    limits: Optional[Dict[str, Any]] = None


@app.put("/api/config")
async def api_put_config(payload: ConfigPayload) -> Dict[str, Any]:
    cfg = Config.get()
    if payload.scan is not None:
        cfg["scan"].update(payload.scan)
    if payload.limits is not None:
        cfg["limits"].update(payload.limits)
    Config.save()
    return {"ok": True, "config": cfg}


@app.get("/api/config/export")
async def api_export_config():
    """导出完整配置为 JSON 下载（通道凭据、规则、定时、扫描参数）。"""
    cfg = Config.get()
    payload = json.dumps(cfg, ensure_ascii=False, indent=2)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"dir2chat-config-{stamp}.json"
    return JSONResponse(
        content=cfg,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ImportPayload(BaseModel):
    config: Dict[str, Any]


@app.post("/api/config/import")
async def api_import_config(payload: ImportPayload):
    """导入配置：整体替换（保留默认值兜底），并同步通道实例。"""
    data = payload.config or {}
    if not isinstance(data, dict) or not any(
            k in data for k in ("channels", "rules", "scan", "limits")):
        raise HTTPException(400, "配置内容不合法：缺少 channels / rules / scan 等字段")
    Config.replace(data)
    dispatcher.sync()
    store.log("info", "已导入配置")
    return {"ok": True, "config": Config.get()}


# --------------------------------------------------------------------------
# 通道
# --------------------------------------------------------------------------

class ChannelPayload(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    enabled: Optional[bool] = None
    options: Optional[Dict[str, Any]] = None


@app.post("/api/channels")
async def api_create_channel(payload: ChannelPayload):
    if not payload.type or payload.type not in CHANNEL_SPECS:
        raise HTTPException(400, f"通道类型必须是 {list(CHANNEL_SPECS)} 之一")
    ch = default_channel(payload.type)
    if payload.name:
        ch["name"] = payload.name
    if payload.options:
        ch["options"].update(payload.options)
    if payload.enabled is not None:
        ch["enabled"] = payload.enabled
    Config.get()["channels"].append(ch)
    Config.save()
    dispatcher.sync()
    return ch


@app.put("/api/channels/{channel_id}")
async def api_update_channel(channel_id: str, payload: ChannelPayload):
    ch = Config.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "通道不存在")
    if payload.name is not None:
        ch["name"] = payload.name
    if payload.enabled is not None:
        ch["enabled"] = payload.enabled
    if payload.options is not None:
        ch["options"].update(payload.options)
    Config.save()
    dispatcher.sync()
    return ch


@app.delete("/api/channels/{channel_id}")
async def api_delete_channel(channel_id: str):
    cfg = Config.get()
    before = len(cfg["channels"])
    cfg["channels"] = [c for c in cfg["channels"] if c.get("id") != channel_id]
    for rule in cfg["rules"]:
        rule["channels"] = [c for c in rule.get("channels", []) if c != channel_id]
    Config.save()
    dispatcher.sync()
    return {"ok": len(cfg["channels"]) < before}


@app.post("/api/channels/{channel_id}/test")
async def api_test_channel(channel_id: str):
    try:
        return await dispatcher.test_channel(channel_id)
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/channels/{channel_id}/health")
async def api_channel_health(channel_id: str):
    return await dispatcher.health(channel_id)


@app.get("/api/channels/{channel_id}/chats")
async def api_channel_chats(channel_id: str):
    """企业微信智能机器人会从回调里收集会话 ID，方便直接选用。"""
    return dispatcher.recent_chats(channel_id)


# --------------------------------------------------------------------------
# 规则
# --------------------------------------------------------------------------

class RulePayload(BaseModel):
    name: Optional[str] = None
    watch_dir: Optional[str] = None
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    recursive: Optional[bool] = None
    channels: Optional[List[str]] = None
    caption_template: Optional[str] = None
    markdown_mode: Optional[str] = None
    max_file_bytes: Optional[int] = None
    enabled: Optional[bool] = None
    schedule: Optional[Dict[str, Any]] = None
    interval_seconds: Optional[int] = None


@app.post("/api/rules")
async def api_create_rule(payload: RulePayload):
    rule = default_rule()
    for key, value in payload.dict(exclude_none=True).items():
        rule[key] = value
    Config.get()["rules"].append(rule)
    Config.save()
    return rule


@app.put("/api/rules/{rule_id}")
async def api_update_rule(rule_id: str, payload: RulePayload):
    rule = Config.get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "规则不存在")
    data = payload.dict(exclude_none=True)
    sched_in = data.pop("schedule", None)
    for key, value in data.items():
        rule[key] = value
    if sched_in is not None:
        merged = rule.get("schedule") or default_schedule()
        for k, v in sched_in.items():
            merged[k] = v
        rule["schedule"] = merged
    Config.save()
    return rule


@app.delete("/api/rules/{rule_id}")
async def api_delete_rule(rule_id: str):
    cfg = Config.get()
    before = len(cfg["rules"])
    cfg["rules"] = [r for r in cfg["rules"] if r.get("id") != rule_id]
    Config.save()
    return {"ok": len(cfg["rules"]) < before}


@app.post("/api/rules/{rule_id}/purge")
async def api_purge_rule(rule_id: str):
    """清空该规则的发送记录，下一轮会重新发送目录里所有文件。"""
    deleted = store.purge_rule(rule_id)
    return {"ok": True, "deleted": deleted}


# --------------------------------------------------------------------------
# 手动触发
# --------------------------------------------------------------------------

@app.post("/api/scan/now")
async def api_scan_now():
    jobs = await scanner.scan_once()
    n = await dispatcher.submit(jobs)
    return {"found": len(jobs), "queued": n}


class SendPathPayload(BaseModel):
    path: str
    channels: List[str]
    caption: Optional[str] = ""


@app.post("/api/send/path")
async def api_send_path(payload: SendPathPayload):
    if not os.path.isfile(payload.path):
        raise HTTPException(400, f"文件不存在: {payload.path}")
    from . import media as _media
    n = 0
    for channel_id in payload.channels:
        await dispatcher.submit([{
            "rule_id": "manual",
            "channel_id": channel_id,
            "path": payload.path,
            "sha256": _media.sha256_file(payload.path),
            "size": os.path.getsize(payload.path),
            "kind": _media.file_kind(payload.path),
            "caption": payload.caption or os.path.basename(payload.path),
            "markdown_mode": "auto",
        }])
        n += 1
    return {"queued": n}


# --------------------------------------------------------------------------
# 系统时间 / 时区 / NTP
# --------------------------------------------------------------------------

@app.get("/api/time")
async def api_time_status() -> Dict[str, Any]:
    """当前时区、本机时间与校时能力探测。"""
    return systemtime.status()


@app.get("/api/time/zones")
async def api_time_zones() -> Dict[str, Any]:
    """可选时区与时间服务器清单（供「时区与时间」下拉使用）。"""
    cfg = Config.get()
    tcfg = cfg.get("time", {}) or {}
    cur = tcfg.get("timezone") or systemtime.current_timezone()
    return {
        "current": cur,
        "zones": systemtime.list_timezones(),
        "servers": systemtime.server_options(tcfg.get("custom_servers")),
    }


class TimezonePayload(BaseModel):
    timezone: str


@app.put("/api/time/timezone")
async def api_set_timezone(payload: TimezonePayload) -> Dict[str, Any]:
    result = systemtime.set_timezone(payload.timezone)
    if not result.get("ok"):
        raise HTTPException(400, result.get("reason") or "切换时区失败")
    cfg = Config.get()
    cfg.setdefault("time", {})["timezone"] = payload.timezone
    Config.save()
    store.log("info", f"时区已切换为 {payload.timezone}")
    return {"ok": True, "status": systemtime.status()}


class ManualTimePayload(BaseModel):
    time: str
    timezone: Optional[str] = None


@app.post("/api/time/manual")
async def api_set_manual_time(payload: ManualTimePayload) -> Dict[str, Any]:
    if payload.timezone:
        tzres = systemtime.set_timezone(payload.timezone)
        if not tzres.get("ok"):
            raise HTTPException(400, tzres.get("reason") or "时区设置失败")
        cfg = Config.get()
        cfg.setdefault("time", {})["timezone"] = payload.timezone
        Config.save()
    result = systemtime.set_manual_time(payload.time)
    if not result.get("ok"):
        store.log("error", f"手动校时失败: {result.get('reason')}")
    else:
        store.log("info", f"手动校时成功: {result.get('now')}")
        cfg = Config.get()
        cfg.setdefault("time", {})["mode"] = "manual"
        Config.save()
    return {**result, "status": systemtime.status()}


class NtpPayload(BaseModel):
    server: Optional[str] = None
    timeout: Optional[float] = None


def _resolve_ntp_server(raw: Optional[str], tcfg: Dict[str, Any]) -> str:
    """校验并返回可用于查询的 NTP 服务器地址。

    自定义地址（局域网 IP / 主机名）与内置项一视同仁；
    非法输入直接 400，避免把脏字符串塞进 socket。
    """
    candidate = (raw or tcfg.get("ntp_server") or systemtime.DEFAULT_NTP).strip()
    try:
        return systemtime.normalize_server(candidate)
    except ValueError as exc:
        raise HTTPException(400, f"时间服务器地址无效：{exc}")


@app.post("/api/time/ntp/sync")
async def api_ntp_sync(payload: NtpPayload) -> Dict[str, Any]:
    cfg = Config.get()
    tcfg = cfg.setdefault("time", {})
    server = _resolve_ntp_server(payload.server, tcfg)
    if payload.server:
        tcfg["ntp_server"] = server
    result = systemtime.sync_ntp(server, timeout=payload.timeout or 5.0)
    if result.get("ok"):
        tcfg["mode"] = "auto"
        tcfg["last_sync_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        Config.save()
        store.log("info", f"已与 {server} 同步时间（调整 {result.get('adjusted_seconds')}s）")
    else:
        Config.save()
        store.log("error", f"时间同步失败（{server}）: {result.get('reason')}")
    return {**result, "status": systemtime.status()}


@app.post("/api/time/ntp/test")
async def api_ntp_test(payload: NtpPayload) -> Dict[str, Any]:
    cfg = Config.get()
    tcfg = cfg.get("time", {}) or {}
    server = _resolve_ntp_server(payload.server, tcfg)
    return systemtime.ntp_check(server, timeout=payload.timeout or 4.0)


class CustomServerPayload(BaseModel):
    host: str
    label: Optional[str] = None


@app.post("/api/time/servers")
async def api_add_custom_server(payload: CustomServerPayload) -> Dict[str, Any]:
    """把用户自定义的 NTP 服务器（可为局域网地址）加入可选清单。"""
    try:
        host = systemtime.normalize_server(payload.host)
    except ValueError as exc:
        raise HTTPException(400, f"服务器地址无效：{exc}")
    cfg = Config.get()
    tcfg = cfg.setdefault("time", {})
    items = list(tcfg.get("custom_servers") or [])
    if any((it.get("value") == host) for it in items if isinstance(it, dict)):
        return {"ok": True, "duplicated": True, "servers": systemtime.server_options(items)}
    if any(it.get("value") == host for it in systemtime.NTP_SERVERS):
        # 与内置项同名：直接选中它，不再重复添加
        tcfg["ntp_server"] = host
        Config.save()
        return {"ok": True, "builtin": True, "servers": systemtime.server_options(items)}
    label = (payload.label or "").strip() or f"{host}（自定义）"
    items.append({"value": host, "label": label, "custom": True})
    tcfg["custom_servers"] = items
    Config.save()
    store.log("info", f"已添加自定义时间服务器：{host}")
    return {"ok": True, "servers": systemtime.server_options(items)}


class DeleteServerPayload(BaseModel):
    host: str


@app.post("/api/time/servers/delete")
async def api_delete_custom_server(payload: DeleteServerPayload) -> Dict[str, Any]:
    """删除一个自定义时间服务器（内置项不可删）。"""
    host = (payload.host or "").strip()
    cfg = Config.get()
    tcfg = cfg.setdefault("time", {})
    items = [it for it in (tcfg.get("custom_servers") or [])
             if isinstance(it, dict) and it.get("value") != host]
    tcfg["custom_servers"] = items
    if tcfg.get("ntp_server") == host:
        tcfg["ntp_server"] = systemtime.DEFAULT_NTP
    Config.save()
    store.log("info", f"已删除自定义时间服务器：{host}")
    return {"ok": True, "servers": systemtime.server_options(items)}



class TimeConfigPayload(BaseModel):
    mode: Optional[str] = None            # auto | manual
    timezone: Optional[str] = None
    ntp_server: Optional[str] = None


@app.put("/api/time/config")
async def api_put_time_config(payload: TimeConfigPayload) -> Dict[str, Any]:
    cfg = Config.get()
    tcfg = cfg.setdefault("time", {})
    if payload.mode in ("auto", "manual"):
        tcfg["mode"] = payload.mode
    if payload.ntp_server is not None:
        try:
            tcfg["ntp_server"] = systemtime.normalize_server(payload.ntp_server)
        except ValueError as exc:
            raise HTTPException(400, f"时间服务器地址无效：{exc}")
    if payload.timezone:
        res = systemtime.set_timezone(payload.timezone)
        if not res.get("ok"):
            raise HTTPException(400, res.get("reason") or "时区设置失败")
        tcfg["timezone"] = payload.timezone
    Config.save()
    return {"ok": True, "config": tcfg, "status": systemtime.status()}


# --------------------------------------------------------------------------
# 前端
# --------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.get("/healthz")
async def healthz():
    return {"ok": True}
