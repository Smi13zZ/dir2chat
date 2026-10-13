"""配置模型与持久化。

配置保存在 CONFIG_DIR/config.json，启动时若不存在则从 DEFAULT_CONFIG 生成。
所有字段都有默认值，新增字段不会让旧配置文件失效。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from typing import Any, Dict, List

CONFIG_DIR = os.environ.get("DIR2CHAT_CONFIG_DIR", "/config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

_LOCK = threading.RLock()

# 各通道类型的默认选项。UI 与校验都以此为准。
CHANNEL_SPECS: Dict[str, Dict[str, Any]] = {
    "wecom_webhook": {
        "label": "企业微信 · 群机器人 Webhook",
        "desc": "群「消息推送」（原群机器人）。支持文本 / markdown / 图片 / 文件，仅内部群。",
        "caps": {"text": True, "markdown": True, "image": True, "file": True},
        "options": {
            "webhook_url": "",
            "markdown_max_bytes": 3800,
            "image_max_bytes": 2 * 1024 * 1024,
        },
        "fields": [
            {"key": "webhook_url", "label": "Webhook 地址", "type": "text",
             "placeholder": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
             "help": "群设置 → 消息推送 → 添加 → 复制 Webhook 地址"},
            {"key": "markdown_max_bytes", "label": "markdown 直发上限(字节)", "type": "int",
             "help": "超过则退化为发送 .md 文件"},
            {"key": "image_max_bytes", "label": "图片上限(字节)", "type": "int",
             "help": "企业微信硬上限 2MB，超出的图片会自动压缩"},
        ],
    },
    "wecom_bot": {
        "label": "企业微信 · 智能机器人（长连接）",
        "desc": "工作台 → 智能机器人 → API 模式 → 长连接。可进内部群、可单聊，图片与文件走临时素材上传。",
        "caps": {"text": True, "markdown": True, "image": True, "file": True},
        "options": {
            "bot_id": "",
            "secret": "",
            "chatid": "",
            "chat_type": 2,
            "markdown_max_bytes": 8000,
            "image_max_bytes": 10 * 1024 * 1024,
        },
        "fields": [
            {"key": "bot_id", "label": "Bot ID", "type": "text",
             "help": "机器人详情页 API 配置区，选择「使用长连接」后显示"},
            {"key": "secret", "label": "Secret", "type": "secret",
             "help": "长连接专用密钥，只显示一次"},
            {"key": "chatid", "label": "会话 ID", "type": "text",
             "help": "单聊填成员 userid，群聊填群聊 chatid；点上方「会话 ID」按钮可自动取"},
            {"key": "chat_type", "label": "会话类型", "type": "select",
             "choices": [{"value": 1, "label": "单聊(userid)"}, {"value": 2, "label": "群聊(chatid)"}],
             "help": "1=单聊 2=群聊，填错会发送失败，建议显式指定"},
            {"key": "markdown_max_bytes", "label": "markdown 直发上限(字节)", "type": "int",
             "help": "官方上限 20480；超出会自动切成多条"},
            {"key": "image_max_bytes", "label": "图片上限(字节)", "type": "int",
             "help": "官方上限 10MB，比群机器人的 2MB 宽松；超出的图会自动压缩"},
        ],
    },
    "wecom_app": {
        "label": "企业微信 · 应用消息",
        "desc": "自建应用主动发消息给指定成员 / 部门。支持文本 / markdown / 图片 / 文件，可发到个人。",
        "caps": {"text": True, "markdown": True, "image": True, "file": True},
        "options": {
            "corpid": "",
            "corpsecret": "",
            "agentid": "",
            "touser": "@all",
            "markdown_max_bytes": 1800,
        },
        "fields": [
            {"key": "corpid", "label": "CorpID", "type": "text",
             "help": "管理后台 → 我的企业 → 企业信息"},
            {"key": "corpsecret", "label": "Secret", "type": "secret",
             "help": "应用详情页的 Secret"},
            {"key": "agentid", "label": "AgentID", "type": "text", "help": "应用详情页的 AgentId"},
            {"key": "touser", "label": "接收成员", "type": "text",
             "help": "userid 列表，| 分隔；@all 表示全员"},
            {"key": "markdown_max_bytes", "label": "markdown 直发上限(字节)", "type": "int",
             "help": "应用消息 markdown 上限较严，默认 1800，超出发文件"},
        ],
    },
    "qq_bot": {
        "label": "QQ · 官方机器人",
        "desc": "QQ 开放平台机器人。支持单聊 / 群聊 / 频道，图片与文件走富媒体上传。",
        "caps": {"text": True, "markdown": True, "image": True, "file": True},
        "options": {
            "app_id": "",
            "client_secret": "",
            "target_type": "group",
            "target_openid": "",
            "markdown_max_bytes": 4000,
            "sandbox": False,
        },
        "fields": [
            {"key": "app_id", "label": "AppID", "type": "text", "help": "q.qq.com 机器人详情页"},
            {"key": "client_secret", "label": "AppSecret", "type": "secret",
             "help": "机器人详情页 AppSecret"},
            {"key": "target_type", "label": "目标类型", "type": "select",
             "choices": [{"value": "group", "label": "QQ 群"}, {"value": "user", "label": "单聊用户"}],
             "help": "群聊与单聊的 openid 不通用，上传端点也相互独立"},
            {"key": "target_openid", "label": "目标 openid", "type": "text",
             "help": "群填 group_openid，单聊填 user_openid，均从事件回调中获得"},
            {"key": "markdown_max_bytes", "label": "markdown 直发上限(字节)", "type": "int",
             "help": "超出则退化为发送文件"},
            {"key": "sandbox", "label": "沙箱环境", "type": "bool",
             "help": "勾选后走 sandbox.api.sgroup.qq.com，不受频控限制"},
        ],
    },
}

DEFAULT_SCHEDULE: Dict[str, Any] = {
    "enabled": False,
    "timezone": "Asia/Shanghai",
    "windows": [],
}


def default_schedule() -> Dict[str, Any]:
    return deepcopy(DEFAULT_SCHEDULE)


DEFAULT_CONFIG: Dict[str, Any] = {
    "scan": {
        "interval_seconds": 60,
        "stable_seconds": 5,
        # 每轮最多「推送」多少个待推送文件（已推送过的历史文件不占这个名额）
        "max_files_per_round": 500,
        # 内容哈希缓存条数上限（0=关闭缓存）；命中则不再读文件，只比对 size+mtime
        "digest_cache_max_entries": 5000,
        # 目录级跳过：子目录超过 N 天没有任何变动就不再进去扫描（0=关闭）
        "skip_dirs_idle_days": 0,
        "enabled": True,
    },
    "limits": {
        "channel_interval_seconds": 1.0,
        "max_retry": 3,
        "retry_backoff_seconds": 5,
    },
    # 系统时间：mode=auto 走 NTP 自动同步，manual 为手动设置
    "time": {
        "mode": "auto",
        "timezone": "Asia/Shanghai",
        "ntp_server": "ntp.tencent.com",
        "custom_servers": [],
        "last_sync_at": "",
        "auto_sync_hours": 6,
    },
    "channels": [],
    "rules": [],
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """进程内单例，持有一份配置并处理落盘。"""

    _data: Dict[str, Any] = {}

    @classmethod
    def load(cls) -> Dict[str, Any]:
        with _LOCK:
            raw: Dict[str, Any] = {}
            if os.path.exists(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                except Exception:
                    raw = {}
            cls._data = _deep_merge(DEFAULT_CONFIG, raw)
            cls.save()
            return cls._data

    @classmethod
    def get(cls) -> Dict[str, Any]:
        if not cls._data:
            cls.load()
        return cls._data

    @classmethod
    def save(cls) -> None:
        with _LOCK:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cls._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)

    @classmethod
    def replace(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            cls._data = _deep_merge(DEFAULT_CONFIG, data or {})
            cls.save()
            return cls._data

    @classmethod
    def channels(cls) -> List[Dict[str, Any]]:
        return cls.get().get("channels", [])

    @classmethod
    def get_channel(cls, channel_id: str) -> Dict[str, Any] | None:
        for ch in cls.channels():
            if ch.get("id") == channel_id:
                return ch
        return None

    @classmethod
    def rules(cls) -> List[Dict[str, Any]]:
        return cls.get().get("rules", [])

    @classmethod
    def get_rule(cls, rule_id: str) -> Dict[str, Any] | None:
        for r in cls.rules():
            if r.get("id") == rule_id:
                return r
        return None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def default_channel(channel_type: str) -> Dict[str, Any]:
    spec = CHANNEL_SPECS.get(channel_type)
    if not spec:
        raise ValueError(f"unknown channel type: {channel_type}")
    return {
        "id": new_id(channel_type),
        "type": channel_type,
        "name": spec["label"],
        "enabled": False,
        "options": deepcopy(spec["options"]),
    }


def default_rule() -> Dict[str, Any]:
    return {
        "id": new_id("rule"),
        "name": "新规则",
        "watch_dir": "/data",
        "include": ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.md"],
        "exclude": [],
        "recursive": True,
        "channels": [],
        "caption_template": "{filename}",
        "markdown_mode": "auto",
        "max_file_bytes": 50 * 1024 * 1024,
        "enabled": True,
        # 每条规则独立控制定时（enabled=False → 该规则全天推送）
        "schedule": default_schedule(),
        # 该规则独立的轮询间隔（秒）；0 或 null → 跟随全局 scan.interval_seconds
        "interval_seconds": 0,
    }
