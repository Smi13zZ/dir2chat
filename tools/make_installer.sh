#!/usr/bin/env bash
#
# dir2chat 离线安装包生成脚本（在【已构建的机器】上运行，通常是 NAS 或开发机）
#
# 用法：
#   bash tools/make_installer.sh              # 用 VERSION 文件的版本号
#   bash tools/make_installer.sh 26.10.15     # 显式指定版本
#
# 前提：
#   1) 本机 Docker 里已存在镜像  dir2chat:<VERSION>（docker compose build 出来）
#   2) 源码目录 SRC（默认脚本上一级的上一级）里的 app/ 已是最新
#   3) ⚠️ 打包前务必先跑一遍 tools/sync_check.py —— 它比对本地与 NAS 部署目录的 md5，
#      揪出「本地改了但没上传」的文件。26.10.14 就是因为漏传 CHANGELOG.md 与 .env.example，
#      打出来的包里是旧版内容（CHANGELOG 缺当版条目）。同类问题已发生两次。
#
# 产出：
#   OUT/dir2chat-installer-<VERSION>.tar.gz
#     解压后是 dir2chat-<VERSION>/
#       ├── install.sh / docker-compose.yml / Dockerfile / VERSION / requirements.txt
#       ├── app/  README.md  CHANGELOG.md  教程.html
#       └── dir2chat-<VERSION>-image.tar.gz   # 预置镜像，docker load 后即可删除
#
# 说明：
#   - 排除 config/（SQLite 库与 rules 配置）和 .env（含密钥），避免污染新机器
#   - ⚠️ 不要在这里用 --exclude='*.png'，会连带排掉 Web 的图标；只需排 png 时请写具体文件名
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

V="${1:-$(cat "$REPO_DIR/VERSION" | tr -d '[:space:]')}"
SRC="${SRC:-$REPO_DIR}"
OUT="${OUT:-/vol1/1000/development/dir2chat}"

echo ""
echo "========================================"
echo "  dir2chat 安装包生成  (v$V)"
echo "========================================"
echo "  源码目录 : $SRC"
echo "  输出目录 : $OUT"
echo ""

if [ -z "$V" ]; then echo "错误：无法取得版本号"; exit 1; fi
if ! docker image inspect "dir2chat:$V" >/dev/null 2>&1; then
  echo "错误：本机不存在镜像 dir2chat:$V"
  echo "      请先跑 docker compose build"
  docker images | grep dir2chat || true
  exit 1
fi

TMP="/tmp/_pkg_$V"
rm -rf "$TMP"
mkdir -p "$TMP/dir2chat-$V"

cd "$SRC"
tar \
  --exclude='./config' \
  --exclude='./.env' \
  --exclude='./tools' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -cf - . | tar -xf - -C "$TMP/dir2chat-$V"
rm -rf "$TMP/dir2chat-$V/app/__pycache__" "$TMP/dir2chat-$V/app/channels/__pycache__"

echo "--- 源码清单 ---"
ls -la "$TMP/dir2chat-$V"
echo ""

echo "--- 导出镜像 dir2chat:$V ---"
docker save "dir2chat:$V" | gzip > "$TMP/dir2chat-$V/dir2chat-$V-image.tar.gz"
ls -la "$TMP/dir2chat-$V/dir2chat-$V-image.tar.gz"
echo ""

cd "$TMP"
tar czf "$OUT/dir2chat-installer-$V.tar.gz" "dir2chat-$V"
chmod 644 "$OUT/dir2chat-installer-$V.tar.gz"
rm -rf "$TMP"

echo "--- 完成 ---"
ls -la "$OUT/dir2chat-installer-$V.tar.gz"
