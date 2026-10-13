"""定时开关：判断当前是否处于允许推送的时间窗口内。

时间窗口按「小时:分钟」(HH:MM) 配置，支持多条窗口、支持跨午夜
（例如 22:00 - 06:00）。时区默认 Asia/Shanghai，依赖 zoneinfo + tzdata；
若运行环境没有时区数据则回退到服务器本地时间（并尽量提示）。

语义：
- schedule.enabled = False  → 不限制，视为常开
- enabled = True 但 windows 为空 → 也视为常开（避免把推送整体锁死）
- 否则：当前时间落在任一窗口内才允许推送
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _HAVE_ZONEINFO = True
except Exception:  # pragma: no cover
    _HAVE_ZONEINFO = False


def _parse_hhmm(value: Any) -> Optional[Tuple[int, int]]:
    s = (value or "").strip()
    if not s or ":" not in s:
        return None
    try:
        h_str, m_str = s.split(":", 1)
        h, m = int(h_str), int(m_str)
        if 0 <= h < 24 and 0 <= m < 60:
            return (h, m)
    except (ValueError, TypeError):
        return None
    return None


def _now_in_tz(timezone: str) -> datetime:
    """返回指定时区的当前时间；拿不到时区数据则回退本地时间。"""
    if _HAVE_ZONEINFO and timezone:
        try:
            return datetime.now(ZoneInfo(timezone))
        except Exception:
            pass
    return datetime.now()


def _minutes_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _normalized(schedule: Dict[str, Any]) -> Dict[str, Any]:
    if not schedule or not isinstance(schedule, dict):
        return {}
    return schedule


def is_active_now(schedule: Dict[str, Any]) -> bool:
    """当前是否处于允许推送的窗口内。"""
    sched = _normalized(schedule)
    if not sched.get("enabled"):
        return True
    windows = [w for w in (sched.get("windows") or []) if isinstance(w, dict)]
    if not windows:
        return True
    tz = sched.get("timezone") or ""
    now = _now_in_tz(tz)
    cur = _minutes_of_day(now)
    for w in windows:
        s = _parse_hhmm(w.get("start"))
        e = _parse_hhmm(w.get("end"))
        if not s or not e:
            continue
        sm, em = s[0] * 60 + s[1], e[0] * 60 + e[1]
        if sm == em:
            continue
        if sm < em:
            if sm <= cur < em:
                return True
        else:  # 跨午夜，例如 22:00 - 06:00
            if cur >= sm or cur < em:
                return True
    return False


def phase_info(schedule: Dict[str, Any]) -> Dict[str, Any]:
    """给 UI 用的相位信息：当前是否在窗口内、下次切换时间与切换方向。

    仅供展示，计算精度到分钟即可。
    """
    sched = _normalized(schedule)
    active = is_active_now(sched)
    if not sched.get("enabled"):
        return {"scheduled": False, "active": True, "next_change": None, "will_active": None}
    windows = [w for w in (sched.get("windows") or []) if isinstance(w, dict)]
    if not windows:
        return {"scheduled": True, "active": True, "next_change": None, "will_active": None}

    tz = sched.get("timezone") or ""
    now = _now_in_tz(tz)
    cur = _minutes_of_day(now)

    # 收集所有窗口边界，标注入(on)/出(off)，跨午夜的 off 边界顺延到次日
    edges: List[Tuple[int, str]] = []
    for w in windows:
        s = _parse_hhmm(w.get("start"))
        e = _parse_hhmm(w.get("end"))
        if not s or not e or s[0] * 60 + s[1] == e[0] * 60 + e[1]:
            continue
        sm, em = s[0] * 60 + s[1], e[0] * 60 + e[1]
        if sm < em:
            edges.append((sm, "on"))
            edges.append((em, "off"))
        else:
            edges.append((sm, "on"))
            edges.append((em, "off"))
            edges.append((em + 1440, "off"))

    if not edges:
        return {"scheduled": True, "active": True, "next_change": None, "will_active": None}

    extended = edges + [(m + 1440, t) for (m, t) in edges]
    extended.sort()

    nxt: Optional[Tuple[int, str]] = None
    for m, t in extended:
        if m > cur:
            nxt = (m, t)
            break
    if nxt is None:
        nxt = extended[0]

    delta = nxt[0] - cur
    change_at = now + timedelta(minutes=delta)
    return {
        "scheduled": True,
        "active": active,
        "next_change": change_at.strftime("%Y-%m-%d %H:%M"),
        "will_active": nxt[1] == "on",
    }


def seconds_to_next_change(schedule: Dict[str, Any]) -> Optional[int]:
    """距下一次相位切换还有多少秒（用于定时循环精确唤醒）。

    未启用定时 / 无有效窗口 → None。
    """
    sched = _normalized(schedule)
    if not sched.get("enabled"):
        return None
    windows = [w for w in (sched.get("windows") or []) if isinstance(w, dict)]
    if not windows:
        return None

    tz = sched.get("timezone") or ""
    now = _now_in_tz(tz)
    cur = _minutes_of_day(now)

    edges: List[int] = []
    for w in windows:
        s = _parse_hhmm(w.get("start"))
        e = _parse_hhmm(w.get("end"))
        if not s or not e:
            continue
        sm, em = s[0] * 60 + s[1], e[0] * 60 + e[1]
        if sm == em:
            continue
        edges.append(sm)
        edges.append(em)
        if sm > em:  # 跨午夜
            edges.append(sm + 1440)
            edges.append(em + 1440)

    if not edges:
        return None

    soonest = None
    for m in edges:
        if m > cur:
            soonest = m - cur
            break
    if soonest is None:
        soonest = min(edges) + 1440 - cur
    return int(soonest * 60 - now.second)
