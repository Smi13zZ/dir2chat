#!/usr/bin/env python3
"""一次性工具：连 QQ 开放平台 WebSocket，抓出 group_openid / user_openid。

为什么需要它
------------
dir2chat 的「QQ · 官方机器人」通道要求填一个 target_openid（群填 group_openid，
单聊填 user_openid）。官方 OpenAPI v2 **没有**「列出机器人所在群」这类接口，
openid 只能从事件回调里拿。本脚本就是那个"临时接一下事件"的一次性接收端。

跑起来之后：
  * 想拿群 openid  -> 把机器人拉进目标群（触发 GROUP_ADD_ROBOT），
                      或在群里 @机器人说一句话（触发 GROUP_AT_MESSAGE_CREATE）
  * 想拿用户 openid -> 用那个 QQ 号私聊机器人发一句话（触发 C2C_MESSAGE_CREATE）
终端会把 openid 打出来，复制回 dir2chat 即可，之后本脚本可以关掉。

依赖
----
    pip install httpx websockets

在本机跑就行，**不需要**装进 dir2chat 容器。

用法
----
    python tools/get_qq_openid.py --app-id 1020xxxx --secret xxxxxxxx
    python tools/get_qq_openid.py --app-id 1020xxxx --secret xxxxxxxx --sandbox
    python tools/get_qq_openid.py --app-id 1020xxxx --secret xxxxxxxx --timeout 120

--sandbox 与正式环境的 openid 不通用，调试用哪个环境就带哪个参数。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # websockets < 13
    from websockets.client import connect as ws_connect  # type: ignore

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
INTENT_GROUP_AND_C2C = 1 << 25  # 33554432，群聊 + 单聊事件

FOUND: dict[str, str] = {}


def api_base(sandbox: bool) -> str:
    return "https://sandbox.api.sgroup.qq.com" if sandbox else "https://api.sgroup.qq.com"


def get_token(app_id: str, secret: str) -> str:
    resp = httpx.post(
        TOKEN_URL,
        json={"appId": str(app_id), "clientSecret": str(secret)},
        timeout=20,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("access_token")
    if not token:
        sys.exit(f"[失败] 取 access_token 失败：{body}\n"
                 f"       常见原因：appId / clientSecret 不属于同一个机器人，或机器人被封禁。")
    return token


def get_gateway(token: str, sandbox: bool) -> str:
    resp = httpx.get(
        f"{api_base(sandbox)}/gateway",
        headers={"Authorization": f"QQBot {token}"},
        timeout=20,
    )
    resp.raise_for_status()
    url = resp.json().get("url")
    if not url:
        sys.exit(f"[失败] 取 gateway 失败：{resp.text[:300]}")
    return url


def harvest(event: str, data: object) -> None:
    """从事件体里翻 openid，能翻多少翻多少。"""
    if not isinstance(data, dict):
        return
    pairs: list[tuple[str, str]] = []
    for key in ("group_openid", "user_openid"):
        val = data.get(key)
        if isinstance(val, str) and val:
            pairs.append((key, val))
    author = data.get("author")
    if isinstance(author, dict):
        val = author.get("user_openid") or author.get("id")
        if isinstance(val, str) and val:
            pairs.append(("user_openid", val))
    for kind, val in pairs:
        if val in FOUND:
            continue
        FOUND[val] = kind
        label = "QQ 群" if kind == "group_openid" else "单聊用户"
        print(f"    >>> 拿到 {kind} = {val}    (dir2chat 目标类型选「{label}」)", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description="抓取 QQ 机器人的 group_openid / user_openid")
    ap.add_argument("--app-id", required=True, help="开放平台机器人详情页的 AppID")
    ap.add_argument("--secret", required=True, help="机器人详情页的 AppSecret")
    ap.add_argument("--sandbox", action="store_true", help="走沙箱环境（不受频控、便于调试）")
    ap.add_argument("--timeout", type=int, default=300, help="等待事件的最长秒数，默认 300")
    args = ap.parse_args()

    token = get_token(args.app_id, args.secret)
    print(f"[ok] access_token 已获取（{token[:8]}...，有效期 7200s）")
    gateway = get_gateway(token, args.sandbox)
    env = "沙箱" if args.sandbox else "正式"
    print(f"[ok] {env}环境 gateway: {gateway}")
    print()
    print("现在去做下面任意一件事（脚本会一直等）：")
    print("  · 把机器人拉进目标群          -> 拿 group_openid")
    print("  · 在群里 @机器人 发一句话      -> 拿 group_openid")
    print("  · 用某个 QQ 号私聊机器人一句   -> 拿 user_openid")
    print("拿到后 Ctrl+C 退出即可。\n")

    async with ws_connect(gateway) as ws:
        hello = json.loads(await ws.recv())
        interval = hello.get("d", {}).get("heartbeat_interval", 45000) / 1000
        await ws.send(json.dumps({
            "op": 2,
            "d": {
                "token": f"QQBot {token}",
                "intents": INTENT_GROUP_AND_C2C,
                "shard": [0, 1],
                "properties": {"$os": "linux", "$browser": "dir2chat", "$device": "dir2chat"},
            },
        }))
        print("[ok] 已鉴权（intents=33554432 群聊+单聊），等待事件...\n", flush=True)

        seq = None
        stop = asyncio.Event()

        async def heartbeat() -> None:
            while not stop.is_set():
                await asyncio.sleep(interval)
                try:
                    await ws.send(json.dumps({"op": 1, "d": seq}))
                except Exception:
                    return

        beater = asyncio.create_task(heartbeat())
        deadline = time.time() + args.timeout
        try:
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                op = msg.get("op")
                if op == 0:
                    seq = msg.get("s")
                    event = msg.get("t", "")
                    print(f"[事件] {event}", flush=True)
                    harvest(event, msg.get("d"))
                elif op == 11:
                    pass
                elif op in (7, 9):
                    print("[警告] 网关要求重连/断连，请重跑本脚本", flush=True)
                    break
                elif op == 10:
                    pass
                else:
                    print(f"[op={op}] {str(msg)[:200]}", flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            beater.cancel()

    print("\n=== 汇总 ===")
    if not FOUND:
        print("一个 openid 都没拿到。检查：")
        print("  1) 机器人是否选的「公域机器人」（私域机器人调群接口会报 11255）")
        print("  2) 后台「开发 → 事件订阅」是否勾了群聊 / 单聊事件")
        print("  3) 你的 QQ 号是否在「测试人员管理」里")
        print("  4) 是否真的把机器人拉进群 / 群里 @了它 / 私聊发过消息")
        return
    for val, kind in FOUND.items():
        label = "QQ 群" if kind == "group_openid" else "单聊用户"
        print(f"  {kind:<14} {val}    -> dir2chat 目标类型：{label}")


if __name__ == "__main__":
    asyncio.run(main())
