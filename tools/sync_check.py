#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比对【本地源码目录】与【NAS 部署目录】的文件，揪出「改了但没上传」的文件。

起因：26.10.14 打包时漏传了 CHANGELOG.md 与 .env.example，导致打出来的包里是旧版
      （肉眼没看出来，是 md5 比对才发现的）。同类问题此前也发生过一次。
      打包前跑一遍本脚本，可以避免第三次。

用法（在本机跑）：
  python tools/sync_check.py                      # 只列出差异
  python tools/sync_check.py --upload             # 列出差异并自动上传（本地较新的）
  python tools/sync_check.py --remote-dir /x/y    # 指定远端目录
  python tools/sync_check.py --remote-script X    # 指定 remote.py 路径

依赖：本机 managed venv 已装 paramiko（remote.py 需要）。
约定：与打包脚本 make_installer.sh 的排除范围保持一致 ——
      config/（含 SQLite 与 rules）、.env（含密钥）、__pycache__、*.pyc 不参与比对，
      因为它们本来就不该进包/不该传播。tools/ 参与比对（make_installer.sh 要在 NAS 上跑）。
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
REPO_DIR = HERE.parent

DEFAULT_REMOTE_DIR = "/vol1/1000/docker/dir2chat"
DEFAULT_REMOTE_SCRIPT = Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "Scripts" / "python.exe"
REMOTE_PY_CANDIDATES = [
    Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "Scripts" / "python.exe",
    Path(sys.executable),
]

EXCLUDE_DIRS = {"__pycache__", ".git", "config", "_dist", "_probe", "node_modules", ".idea"}
EXCLUDE_FILES = {".env", ".DS_Store"}
EXCLUDE_SUFFIX = {".pyc"}


def find_remote_script(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        sys.exit(f"错误：找不到 --remote-script 指定的文件 {p}")
    # 从本仓库向上找 <workspace>/.workbuddy/scripts/remote.py，通用且不依赖盘符
    cands = [
        REPO_DIR.parent / ".workbuddy" / "scripts" / "remote.py",
        Path.home() / "Workspace WorkBuddy" / ".workbuddy" / "scripts" / "remote.py",
        Path("D:/Workspace WorkBuddy/.workbuddy/scripts/remote.py"),
    ]
    for p in cands:
        if p.is_file():
            return p
    sys.exit("错误：找不到 remote.py，请用 --remote-script 指定路径")


def run_remote(remote_script: Path, cmd: str, sudo: bool = False) -> str:
    args = [sys.executable, str(remote_script)]
    if sudo:
        args.append("--sudo")
    args.append(cmd)
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"远端命令失败：{cmd}\n{r.stdout}\n{r.stderr}")
    # remote.py 的 sudo 分支会先喷一行 Could not chdir，去掉
    lines = [ln for ln in (r.stdout or "").splitlines()
             if "Could not chdir" not in ln]
    return "\n".join(lines)


def iter_local_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        parts = rel.parts
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        if p.name in EXCLUDE_FILES or p.suffix in EXCLUDE_SUFFIX:
            continue
        yield rel.as_posix()


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def remote_md5_map(remote_script: Path, remote_dir: str) -> dict:
    cmd = (
        f"cd {remote_dir} && find . -type f "
        f"-not -path './config/*' -not -path '*/__pycache__/*' -not -name '*.pyc' "
        f"-exec md5sum {{}} +"
    )
    out = run_remote(remote_script, cmd, sudo=True)
    result = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, raw = parts
        if digest == "Could":
            continue
        # ⚠️ 不能用 lstrip("./")：它按【字符集】剥，'.env.example' 会被剥成 'env.example'
        p = raw[2:] if raw.startswith("./") else raw
        result[p] = digest
    return result


def main():
    ap = argparse.ArgumentParser(description="比对本地源码与 NAS 部署目录")
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    ap.add_argument("--remote-script", default=None)
    ap.add_argument("--upload", action="store_true", help="自动上传本地有差异/新增的文件")
    args = ap.parse_args()

    remote_script = find_remote_script(args.remote_script)
    print(f"\n本地目录 : {REPO_DIR}")
    print(f"远端目录 : {args.remote_dir}")
    print(f"通道脚本 : {remote_script}\n")

    local_files = {rel: md5_of(REPO_DIR / rel) for rel in iter_local_files(REPO_DIR)}
    remote_files = remote_md5_map(remote_script, args.remote_dir)

    diff, local_only, remote_only, same = [], [], [], []
    for rel, digest in sorted(local_files.items()):
        if rel not in remote_files:
            local_only.append(rel)
        elif remote_files[rel] != digest:
            diff.append(rel)
        else:
            same.append(rel)
    for rel in sorted(remote_files):
        if rel not in local_files:
            remote_only.append(rel)

    if not (diff or local_only or remote_only):
        print(f"✅ 完全一致 — 共 {len(same)} 个文件，远端没有落后项。\n")
        return

    if diff:
        print(f"⚠️  内容不一致（本地改了没上传）：{len(diff)} 个")
        for rel in diff:
            print(f"    {rel}")
    if local_only:
        print(f"➕ 仅本地有（远端缺失）：{len(local_only)} 个")
        for rel in local_only:
            print(f"    {rel}")
    if remote_only:
        print(f"➖ 仅远端有（本地已删或远端遗留）：{len(remote_only)} 个")
        for rel in remote_only:
            print(f"    {rel}")
    print()

    if not args.upload:
        print("提示：加 --upload 可把上述「不一致 + 仅本地有」的文件传上去。\n")
        return

    targets = diff + local_only
    if not targets:
        print("没有需要上传的文件。\n")
        return
    print(f"开始上传 {len(targets)} 个文件：")
    ok = fail = 0
    for rel in targets:
        local_path = REPO_DIR / rel
        remote_path = f"{args.remote_dir}/{rel}"
        r = subprocess.run(
            [sys.executable, str(remote_script), "--putlf", str(local_path), remote_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            ok += 1
            print(f"  ✓ {rel}")
        else:
            fail += 1
            print(f"  ✗ {rel}  {(r.stdout or '').strip()} {(r.stderr or '').strip()}")
    print(f"\n上传完成：成功 {ok} 个，失败 {fail} 个。\n")


if __name__ == "__main__":
    main()
