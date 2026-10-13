#!/usr/bin/env python3
"""按「年.月.序号」规则推进版本号。

规则（自 2026-10-11 起，取代旧的 SemVer 三段规则）：
    版本号 = <年份末两位>.<月份>.<该月内序号，从 0 开始>
    - 第 1 段：年份末两位（26 = 2026）
    - 第 2 段：月份 1-12（不补零）
    - 第 3 段：该年该月第几个版本，从 0 开始；每次升级 +1
    - 年份或月份变动时，第 3 段重置为 0

例：26.1.0 = 2026 年 1 月第 1 个版本；26.10.10 = 2026 年 10 月第 11 个版本。

用法：
    python tools/bump_version.py                 # 按当前年月自动 bump，并写回文件
    python tools/bump_version.py --dry-run       # 只打印结果，不写
    python tools/bump_version.py --set 26.10.5   # 手动指定版本号
    python tools/bump_version.py --date 2026-11-02   # 用指定日期判断年月（测试跨月重置）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(ROOT, "VERSION")
COMPOSE_FILE = os.path.join(ROOT, "docker-compose.yml")
IMAGE_NAME = "dir2chat"

_VER_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d+)\s*$")


def parse_version(text: str):
    m = _VER_RE.match(text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bump(current: str, today: _dt.date) -> str:
    """按规则算出下一个版本号。"""
    yy = today.year % 100
    mm = today.month
    parsed = parse_version(current)
    if parsed is None:
        # 现有版本号不符合新格式（例如旧的 0.4.0）→ 从本年月 0 号起步
        return f"{yy}.{mm}.0"
    cyy, cmm, seq = parsed
    if (cyy, cmm) == (yy, mm):
        return f"{yy}.{mm}.{seq + 1}"
    # 跨月 / 跨年 → 序号归零
    return f"{yy}.{mm}.0"


def write_version(version: str) -> None:
    with open(VERSION_FILE, "w", encoding="utf-8", newline="") as fh:
        fh.write(version + "\n")
    # 同步 docker-compose.yml 的镜像 tag（若存在）
    if os.path.exists(COMPOSE_FILE):
        with open(COMPOSE_FILE, "r", encoding="utf-8") as fh:
            content = fh.read()
        new_content = re.sub(
            rf"(image:\s*{re.escape(IMAGE_NAME)}:)\S+",
            rf"\g<1>{version}",
            content,
        )
        if new_content != content:
            with open(COMPOSE_FILE, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_content)


def main() -> int:
    ap = argparse.ArgumentParser(description="按 年.月.序号 规则 bump 版本号")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    ap.add_argument("--set", dest="set_version", help="手动指定版本号（如 26.10.5）")
    ap.add_argument("--date", help="用指定日期判断年月，格式 YYYY-MM-DD")
    args = ap.parse_args()

    current = ""
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "r", encoding="utf-8") as fh:
            current = fh.read().strip()

    today = _dt.date.today()
    if args.date:
        today = _dt.datetime.strptime(args.date, "%Y-%m-%d").date()

    if args.set_version:
        new_version = args.set_version.strip()
        if parse_version(new_version) is None:
            print(f"✗ 版本号格式不合法：{new_version}（应为 年.月.序号，如 26.10.0）")
            return 2
    else:
        new_version = bump(current, today)

    print(f"当前版本: {current or '(无)'}")
    print(f"新版本号: {new_version}")
    if args.dry_run:
        print("（--dry-run，未写入文件）")
        return 0

    write_version(new_version)
    print(f"✓ 已写入 VERSION 与 docker-compose.yml 镜像 tag")
    return 0


if __name__ == "__main__":
    sys.exit(main())
