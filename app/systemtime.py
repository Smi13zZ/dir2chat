"""系统时间管理：时区设置、手动校时、NTP 时间服务器自动同步。

容器里没有 systemd-timesyncd，所以自己实现一份轻量 SNTP 客户端：

- 时区：写入 /etc/localtime（软链到 zoneinfo）并设置 TZ 环境变量。
  容器内 /usr/share/zoneinfo 由 python:3.11-slim + tzdata 提供。
- 手动设置时间：需要 CAP_SYS_TIME，调用 clock_settime。
  容器里通过 date -s 实现；权限不足时返回明确错误而不是静默失败。
- NTP 自动同步：用标准 SNTP 报文（UDP 123）向时间服务器取时，
  再 clock_settime。不依赖 ntpd/chrony。

所有能力失败都是「降级」而不是崩溃：返回 ok=False + 可读的 reason，
前端据此提示用户（比如需要加 cap_add: SYS_TIME）。
"""

from __future__ import annotations

import os
import shutil
import socket
import struct
import subprocess
import time
from datetime import datetime, timezone as _tz
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 时区

# 精简但覆盖常用地区的时区清单（值用 IANA 名称）
TIMEZONES: List[Dict[str, str]] = [
    {"value": "Asia/Shanghai",     "label": "(UTC+08:00) 北京，重庆，中国香港特别行政区，乌鲁木齐"},
    {"value": "Asia/Chongqing",    "label": "(UTC+08:00) 重庆"},
    {"value": "Asia/Harbin",       "label": "(UTC+08:00) 哈尔滨"},
    {"value": "Asia/Urumqi",       "label": "(UTC+06:00) 乌鲁木齐"},
    {"value": "Asia/Hong_Kong",    "label": "(UTC+08:00) 中国香港"},
    {"value": "Asia/Macau",        "label": "(UTC+08:00) 中国澳门"},
    {"value": "Asia/Taipei",       "label": "(UTC+08:00) 中国台湾"},
    {"value": "Asia/Tokyo",        "label": "(UTC+09:00) 东京"},
    {"value": "Asia/Seoul",        "label": "(UTC+09:00) 首尔"},
    {"value": "Asia/Singapore",    "label": "(UTC+08:00) 新加坡"},
    {"value": "Asia/Bangkok",      "label": "(UTC+07:00) 曼谷"},
    {"value": "Asia/Kolkata",      "label": "(UTC+05:30) 加尔各答"},
    {"value": "Asia/Dubai",        "label": "(UTC+04:00) 迪拜"},
    {"value": "Europe/London",     "label": "(UTC+00:00) 伦敦"},
    {"value": "Europe/Paris",      "label": "(UTC+01:00) 巴黎"},
    {"value": "Europe/Berlin",     "label": "(UTC+01:00) 柏林"},
    {"value": "Europe/Moscow",     "label": "(UTC+03:00) 莫斯科"},
    {"value": "America/New_York",  "label": "(UTC-05:00) 纽约"},
    {"value": "America/Chicago",   "label": "(UTC-06:00) 芝加哥"},
    {"value": "America/Denver",    "label": "(UTC-07:00) 丹佛"},
    {"value": "America/Los_Angeles", "label": "(UTC-08:00) 洛杉矶"},
    {"value": "America/Sao_Paulo", "label": "(UTC-03:00) 圣保罗"},
    {"value": "Australia/Sydney",  "label": "(UTC+10:00) 悉尼"},
    {"value": "Pacific/Auckland",  "label": "(UTC+12:00) 奥克兰"},
    {"value": "UTC",               "label": "(UTC+00:00) UTC 协调世界时"},
]

# 常用 NTP 服务器
NTP_SERVERS: List[Dict[str, str]] = [
    {"value": "ntp.tencent.com",     "label": "ntp.tencent.com（腾讯）"},
    {"value": "ntp.aliyun.com",      "label": "ntp.aliyun.com（阿里云）"},
    {"value": "ntp1.aliyun.com",     "label": "ntp1.aliyun.com（阿里云）"},
    {"value": "cn.pool.ntp.org",     "label": "cn.pool.ntp.org（NTP 池 · 中国）"},
    {"value": "time.cloudflare.com", "label": "time.cloudflare.com（Cloudflare）"},
    {"value": "time.windows.com",    "label": "time.windows.com（微软）"},
    {"value": "time.apple.com",      "label": "time.apple.com（苹果）"},
    {"value": "pool.ntp.org",        "label": "pool.ntp.org（全球池）"},
]

DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_NTP = "ntp.tencent.com"

# 自定义服务器主机名的合法字符（域名 / IPv4 / IPv6 / 局域网主机名）
import re as _re

_HOST_RE = _re.compile(r"^[A-Za-z0-9._:\-\[\]]+$")


def normalize_server(server: str) -> str:
    """清洗并校验一个 NTP 服务器地址（域名、IPv4、IPv6、局域网主机名）。

    返回规范化的 host（去掉协议前缀、路径与首尾空白）。非法则抛 ValueError。
    允许可选的 :port 后缀（如 192.168.1.5:123）。
    """
    s = (server or "").strip()
    if not s:
        raise ValueError("服务器地址不能为空")
    # 容忍用户误粘贴的协议前缀 / 路径
    for pref in ("ntp://", "udp://", "http://", "https://"):
        if s.lower().startswith(pref):
            s = s[len(pref):]
    s = s.split("/", 1)[0].strip()
    if not s:
        raise ValueError("服务器地址不能为空")
    if len(s) > 255:
        raise ValueError("服务器地址过长")
    if not _HOST_RE.match(s):
        raise ValueError("服务器地址含非法字符（只允许字母、数字、. : - [ ] _）")
    # 端口合法性
    host = s
    if ":" in s and not s.startswith("["):
        h, _, p = s.rpartition(":")
        if p.isdigit():
            host, port = h, int(p)
            if not (1 <= port <= 65535):
                raise ValueError("端口超出范围（1-65535）")
    return s


def server_options(custom: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """内置服务器 + 用户自定义服务器，去重后返回给 UI。"""
    out: List[Dict[str, str]] = []
    seen = set()
    for item in NTP_SERVERS:
        out.append(item)
        seen.add(item["value"])
    for item in custom or []:
        if not isinstance(item, dict):
            continue
        val = (item.get("value") or "").strip()
        if not val or val in seen:
            continue
        label = (item.get("label") or "").strip() or val
        out.append({"value": val, "label": label, "custom": True})
        seen.add(val)
    return out

_ZONEINFO_DIRS = ("/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo")


def zoneinfo_root() -> Optional[str]:
    for d in _ZONEINFO_DIRS:
        if os.path.isdir(d):
            return d
    return None


def tz_available(name: str) -> bool:
    root = zoneinfo_root()
    if not root:
        return False
    return os.path.isfile(os.path.join(root, name))


def list_timezones() -> List[Dict[str, str]]:
    """返回可用时区清单：内置常用清单 + zoneinfo 里真实存在的其它地区。

    若运行环境没有 /usr/share/zoneinfo（容器里一定会有，因为装了 tzdata），
    则退化为「只返回内置常用清单」，保证 UI 不会变成空列表。
    """
    root = zoneinfo_root()
    out: List[Dict[str, str]] = []
    seen = set()
    for item in TIMEZONES:
        # 没有 zoneinfo 时不做存在性校验（zoneinfo 模块本身仍能用 tzdata 包）
        if root is None or item["value"] == "UTC" or tz_available(item["value"]):
            out.append(item)
            seen.add(item["value"])
    # 补充 zoneinfo 里存在、但不在常用清单里的（限若干主要区域，避免上千条）
    if root:
        for area in ("Asia", "Europe", "America", "Africa", "Australia", "Pacific"):
            d = os.path.join(root, area)
            if not os.path.isdir(d):
                continue
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                if n in ("posixrules",) or "." in n:
                    continue
                full = f"{area}/{n}"
                if full in seen or not os.path.isfile(os.path.join(d, n)):
                    continue
                out.append({"value": full, "label": full})
                seen.add(full)
    return out


def current_timezone() -> str:
    """当前时区：优先 TZ 环境变量，其次读 /etc/localtime 软链。"""
    tz = os.environ.get("TZ")
    if tz and tz_available(tz):
        return tz
    try:
        link = os.path.realpath("/etc/localtime")
        for root in _ZONEINFO_DIRS:
            if link.startswith(root + "/"):
                name = link[len(root) + 1:]
                if name:
                    return name
    except Exception:
        pass
    return tz or DEFAULT_TZ


def tz_offset_info(name: str) -> Dict[str, Any]:
    """返回该时区的 UTC 偏移描述与当前时间，供 UI 显示。"""
    info: Dict[str, Any] = {"timezone": name, "offset": None, "abbr": None, "now": None}
    try:
        from zoneinfo import ZoneInfo
        z = ZoneInfo(name)
        now = datetime.now(z)
        off = now.utcoffset()
        total = int(off.total_seconds()) if off else 0
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        info["offset"] = f"UTC{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"
        info["abbr"] = now.tzname()
        info["now"] = now.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def set_timezone(name: str) -> Dict[str, Any]:
    """切换时区：更新 /etc/localtime 软链 + TZ 环境变量。"""
    if name not in ("UTC",) and not tz_available(name):
        return {"ok": False, "reason": f"时区不存在: {name}"}
    root = zoneinfo_root()
    src = os.path.join(root, name) if root else None
    if src and not os.path.isfile(src) and name != "UTC":
        return {"ok": False, "reason": f"时区文件缺失: {src}"}

    errors: List[str] = []
    try:
        if src and os.path.isfile(src):
            try:
                os.remove("/etc/localtime")
            except OSError:
                pass
            try:
                os.symlink(src, "/etc/localtime")
            except OSError:
                # 某些容器里 /etc/localtime 是普通文件而非软链，直接覆盖
                shutil.copyfile(src, "/etc/localtime")
    except Exception as exc:
        errors.append(f"/etc/localtime: {exc}")

    os.environ["TZ"] = name
    try:
        time.tzset()
    except Exception:
        pass
    return {"ok": not errors, "timezone": name, "warnings": errors}


# ---------------------------------------------------------------- NTP

_NTP_EPOCH_DELTA = 2208988800  # 1900-01-01 → 1970-01-01 的秒数


def _sntp_query(server: str, timeout: float = 4.0, port: int = 123) -> Tuple[float, float]:
    """向 NTP 服务器查询时间，返回 (unix_时间戳, 往返延迟秒)。"""
    pkt = b"\x1b" + 47 * b"\0"  # LI=0 VN=3 Mode=3(client)
    addr = (server, port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        t1 = time.time()
        sock.sendto(pkt, addr)
        data, _ = sock.recvfrom(512)
        t4 = time.time()
    finally:
        sock.close()
    if len(data) < 48:
        raise ValueError("NTP 响应过短")
    # 服务器 stratum 为 0 视为「kiss-o'-death」，拒绝
    stratum = data[1] & 0x7f
    if stratum == 0 or stratum > 15:
        raise ValueError(f"NTP 服务器拒绝（stratum={data[1]}）")
    # 取 Transmit Timestamp（字节 40..47）
    secs, frac = struct.unpack("!II", data[40:48])
    if secs == 0:
        raise ValueError("NTP 响应无时间戳")
    remote = secs - _NTP_EPOCH_DELTA + frac / 2 ** 32
    return remote + (t4 - t1) / 2, (t4 - t1)


def ntp_check(server: str, timeout: float = 4.0) -> Dict[str, Any]:
    """只查询、不改时间。用于 UI 上「测试」与展示本机/服务器时间差。"""
    try:
        remote, rtt = _sntp_query(server, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "server": server, "reason": f"{type(exc).__name__}: {exc}"}
    local = time.time()
    return {
        "ok": True,
        "server": server,
        "remote_time": datetime.fromtimestamp(remote, _tz.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "local_time": datetime.fromtimestamp(local, _tz.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "offset_seconds": round(remote - local, 3),
        "rtt_seconds": round(rtt, 3),
    }


def _clock_settime(epoch: float) -> Tuple[bool, str]:
    """把系统时钟设为给定时间。需要 CAP_SYS_TIME。"""
    # 优先用 Python 直接调用（需要权限）
    try:
        import ctypes

        class Timespec(ctypes.Structure):
            _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]

        sec = int(epoch)
        nsec = int((epoch - sec) * 1e9)
        ts = Timespec(sec, nsec)
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        CLOCK_REALTIME = 0
        if libc.clock_settime(CLOCK_REALTIME, ctypes.byref(ts)) == 0:
            return True, ""
        errno = ctypes.get_errno()
        msg = os.strerror(errno)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"

    # 回退到 date -s
    if shutil.which("date"):
        try:
            stamp = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
            p = subprocess.run(["date", "-s", stamp], capture_output=True, text=True, timeout=10)
            if p.returncode == 0:
                return True, ""
            msg = (p.stderr or p.stdout or "").strip() or f"date 退出码 {p.returncode}"
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
    return False, msg or "未知错误"


def set_manual_time(value: str) -> Dict[str, Any]:
    """手动设置系统时间。value 支持 'YYYY-MM-DD HH:MM:SS' 或 ISO8601。"""
    raw = (value or "").strip()
    if not raw:
        return {"ok": False, "reason": "时间不能为空"}
    parsed: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return {"ok": False, "reason": f"无法解析时间: {raw}"}

    # 按当前时区解释用户输入的本地时间
    tzname = current_timezone()
    try:
        from zoneinfo import ZoneInfo
        aware = parsed.replace(tzinfo=ZoneInfo(tzname))
        epoch = aware.timestamp()
    except Exception:
        epoch = parsed.timestamp()

    ok, err = _clock_settime(epoch)
    if not ok:
        return {
            "ok": False,
            "reason": "设置系统时间被拒绝，容器缺少 CAP_SYS_TIME 权限",
            "detail": err,
            "hint": "在 docker-compose.yml 的 dir2chat 服务下加 cap_add: [SYS_TIME]，再重建容器",
        }
    return {"ok": True, "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "epoch": epoch}


def sync_ntp(server: str, timeout: float = 5.0) -> Dict[str, Any]:
    """向 NTP 服务器取时并写入系统时钟。"""
    try:
        remote, rtt = _sntp_query(server, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "server": server,
                "reason": f"连接时间服务器失败: {type(exc).__name__}: {exc}"}

    before = time.time()
    ok, err = _clock_settime(remote)
    if not ok:
        return {
            "ok": False,
            "server": server,
            "reason": "已取到标准时间，但写入系统时钟被拒绝（容器缺少 CAP_SYS_TIME 权限）",
            "detail": err,
            "hint": "在 docker-compose.yml 的 dir2chat 服务下加 cap_add: [SYS_TIME]，再重建容器",
            "ntp_time": datetime.fromtimestamp(remote, _tz.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
    return {
        "ok": True,
        "server": server,
        "adjusted_seconds": round(remote - before, 3),
        "rtt_seconds": round(rtt, 3),
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def status() -> Dict[str, Any]:
    """给前端的状态：当前时区、本机时间、能力探测。"""
    tzname = current_timezone()
    now = datetime.now()
    info = tz_offset_info(tzname)
    return {
        "timezone": tzname,
        "offset": info.get("offset"),
        "abbr": info.get("abbr"),
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": int(now.timestamp()),
        "can_set_time": _can_set_time(),
        "zoneinfo_root": zoneinfo_root(),
        "has_date_cmd": bool(shutil.which("date")),
    }


def _can_set_time() -> bool:
    """粗测是否有 CAP_SYS_TIME：尝试把时间设成「现在」（无副作用）。"""
    try:
        import ctypes

        class Timespec(ctypes.Structure):
            _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]

        now = time.time()
        sec = int(now)
        ts = Timespec(sec, int((now - sec) * 1e9))
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.clock_settime(0, ctypes.byref(ts)) == 0:
            return True
    except Exception:
        pass
    # 退一步：看进程权限（非 POSIX 平台没有 geteuid，直接视为不可设置）
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False
