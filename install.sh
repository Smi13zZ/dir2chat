#!/usr/bin/env bash
#
# dir2chat 一键安装脚本
#
# 用法（在【新机器】上）：
#   bash install.sh                                # 装到默认目录 /vol1/1000/docker/dir2chat
#   bash install.sh /vol1/1000/docker/dir2chat     # 指定安装目录
#   DIR2CHAT_PORT=8091 bash install.sh             # 8899 被占用时换个端口
#   WATCH_DIR=/srv/report bash install.sh          # 指定监听目录（会写回 .env，覆盖 .env.example 默认值）
#   FORCE=1 bash install.sh                        # 目录已部署过时，覆盖程序文件但保留数据(config/ 与 .env)
#   sudo bash install.sh                           # 当前用户没有 Docker 权限时（未加入 docker 组）用 root 运行
#
# 流程：解析目录/端口/监听目录 → 部署文件 → 检查 Docker → 生成 .env → 校验监听目录
#       → 创建 config → 拉取镜像(必要时从源码构建) → 启动 → 等待健康
#
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="26.10.15"
# 官方镜像（Docker Hub）。自建私有仓库 / 内网镜像源可用 DIR2CHAT_IMAGE 覆盖，例如：
#   DIR2CHAT_IMAGE=harbor.local/library/dir2chat bash install.sh
IMAGE="${DIR2CHAT_IMAGE:-smi1ezz/dir2chat}"

# ---------- 0. 解析参数 ----------
INSTALL_DIR="${1:-/vol1/1000/docker/dir2chat}"
DIR2CHAT_PORT="${DIR2CHAT_PORT:-8899}"
FORCE="${FORCE:-0}"
# 命令行传入的监听目录（`WATCH_DIR=/xx bash install.sh`），非空则写回 .env 覆盖默认值。
# ⚠️ 必须在这里先存下来：下面第 4 步会用同名变量去读 .env，届时环境变量已被覆盖。
WATCH_DIR_ENV="${WATCH_DIR:-}"

echo ""
echo "========================================"
echo "  dir2chat 安装程序  (v$VERSION)"
echo "========================================"
echo ""
echo "  安装目录 : $INSTALL_DIR"
echo "  对外端口 : $DIR2CHAT_PORT"
if [ -n "$WATCH_DIR_ENV" ]; then
  echo "  监听目录 : $WATCH_DIR_ENV   （命令行指定，将写入 .env）"
fi
echo "  FORCE    : $FORCE"
echo ""

# ---------- 1. 部署文件到目标目录 ----------
# 默认是「就地运行」（解压目录 == 安装目录）。
# 如果指定了别的目录，**只把跑起来真正需要的文件**拷过去 ——
# 文档、开发工具、打包产物一律留在包里，安装目录保持干净。
DEPLOY_DIR="$PKG_DIR"

# 拷贝白名单（安装目录所需的最小集合）：
#   app/               程序本体（运行时依赖它，源码构建也依赖）
#   docker-compose.yml 编排文件
#   Dockerfile         源码构建时需要
#   requirements.txt   源码构建时需要
#   VERSION            版本号（构建时打进镜像，运行时读）
#   .env.example       首次安装用来生成 .env
#   install.sh         留在安装目录，日后可直接 ./install.sh 升级
# 明确【不】拷贝：
#   README.md / 教程.html / CHANGELOG.md   要看去仓库里看，跑服务用不到
#   .gitignore / .git / tools/             开发用
#   __pycache__ / *.pyc                    字节码缓存，与运行环境版本无关
COPY_ITEMS="app docker-compose.yml Dockerfile requirements.txt VERSION .env.example install.sh"

if [ "$(realpath "$INSTALL_DIR")" != "$(realpath "$PKG_DIR")" ]; then
  if [ -e "$INSTALL_DIR" ]; then
    if [ "$FORCE" != "1" ]; then
      echo "✗ 目标目录已存在：$INSTALL_DIR"
      echo "  若要覆盖程序文件但保留数据(config/ 与 .env)，请加 FORCE=1 重跑："
      echo "    FORCE=1 bash install.sh $INSTALL_DIR"
      exit 1
    fi
    echo "→ FORCE 模式：覆盖程序文件，保留 config/ 与 .env（数据不丢）"
  else
    mkdir -p "$INSTALL_DIR"
    echo "→ 已创建安装目录：$INSTALL_DIR"
  fi

  echo "→ 拷贝程序文件到 $INSTALL_DIR（只拷运行所需项）："
  for item in $COPY_ITEMS; do
    [ -e "$PKG_DIR/$item" ] || continue
    rm -rf "${INSTALL_DIR:?}/${item}"
    cp -a "$PKG_DIR/$item" "$INSTALL_DIR/"
    echo "    + $item"
  done
  # 清掉源码里可能夹带的字节码缓存（Py 3.13 的 .pyc 在 3.11 镜像里也没用）
  find "$INSTALL_DIR/app" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find "$INSTALL_DIR/app" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
  echo "    （README/教程/CHANGELOG/tools/预置镜像等留在包目录 $PKG_DIR，不进安装目录）"
  DEPLOY_DIR="$INSTALL_DIR"
fi
cd "$DEPLOY_DIR"

# ---------- 2. 检查 Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  echo "✗ 未检测到 docker，请先安装 Docker Engine："
  echo "  https://docs.docker.com/engine/install/"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "✗ 未检测到 docker compose v2 插件，请安装："
  echo "  https://docs.docker.com/compose/install/"
  exit 1
fi
echo "✓ Docker 就绪： $(docker --version | head -1)"

# Docker 守护进程连通性 / 权限
# （装了 docker 但当前用户不在 docker 组时，后面所有 docker 命令都会 permission denied）
if ! docker info >/dev/null 2>&1; then
  if [ "$(id -u)" != "0" ]; then
    echo "✗ 无法连接 Docker 守护进程。若报的是「permission denied ... docker.sock」，"
    echo "  说明当前用户没有 Docker 权限，请改用 root 运行："
    echo "    sudo bash install.sh"
    echo "  或把当前用户加入 docker 组后重新登录："
    echo "    sudo usermod -aG docker \$USER && newgrp docker"
  else
    echo "✗ 无法连接 Docker 守护进程，请确认 Docker 服务已启动： systemctl start docker"
  fi
  exit 1
fi

# ---------- 3. 准备 .env ----------
# 写 .env 的通用函数：KEY 存在就整行替换，不存在才追加。
# ⚠️ 用 awk 重写整个文件，而不是 `echo >> .env` 追加 ——
#    直接追加时，如果 .env 最后一行没有换行符（手工编辑过很常见），
#    新行会跟最后一行**粘成一行**，例如
#      WATCH_DIR=/vol1/1000/report/daily-stock-analysisDIR2CHAT_PORT=8899
#    这样两个配置项就都失效了。awk 重写保证「一行一条」。
env_set() {
  local key="$1" val="$2"
  awk -v k="$key" -v v="$val" '
    $0 ~ "^" k "=" { print k "=" v; seen = 1; next }
    { print }
    END { if (!seen) print k "=" v }
  ' .env > .env.tmp && mv .env.tmp .env
}

if [ ! -f .env ]; then
  cp .env.example .env
  echo "✓ 已根据 .env.example 生成 .env（请确认 WATCH_DIR 是否正确）"
else
  echo "• .env 已存在，跳过生成"
fi

# 端口：写回 .env，让 docker compose 每次都用同一个端口
env_set DIR2CHAT_PORT "$DIR2CHAT_PORT"

# 监听目录：命令行传了 WATCH_DIR=... 就写回 .env（覆盖 .env.example 的默认值）
if [ -n "$WATCH_DIR_ENV" ]; then
  env_set WATCH_DIR "$WATCH_DIR_ENV"
  echo "✓ 已把监听目录写入 .env： WATCH_DIR=$WATCH_DIR_ENV"
fi

if [ ! -f .env ]; then
  echo "✗ .env 仍缺失，安装中止"
  exit 1
fi

# ---------- 4. 校验 WATCH_DIR ----------
WATCH_DIR="$(grep -E '^WATCH_DIR=' .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*$//')"
if [ -z "$WATCH_DIR" ] || [[ "$WATCH_DIR" == *"/path/to/"* ]]; then
  echo ""
  echo "✗ 请在 .env 中把 WATCH_DIR 改成你真实的监听目录，例如："
  echo "    WATCH_DIR=/srv/daily-stock-analysis/share"
  echo "  修改后重新运行 ./install.sh"
  exit 1
fi
if [ ! -d "$WATCH_DIR" ]; then
  echo "⚠ 警告：WATCH_DIR=$WATCH_DIR 不存在，Docker 可能无法挂载该目录。"
  echo "  请确认路径正确（支持 NFS / 外部卷，需宿主机本身可见该路径）。"
  echo "  继续安装，但服务启动后扫描可能读不到文件。"
fi

# ---------- 5. 准备 config 目录（数据，FORCE 时不覆盖） ----------
mkdir -p config
echo "✓ config/ 目录就绪（数据持久化于此处）"

# ---------- 6. 准备镜像（优先从 Docker Hub 拉取） ----------
# 三种来源，按顺序尝试：
#   ① 本地已有 $IMAGE:$VERSION    → 直接用
#   ② docker pull $IMAGE:$VERSION → 联网拉取（镜像为 amd64 / arm64 双架构，自动匹配本机）
#   ③ 前两者都不行                → 从源码构建（需联网拉基础镜像 python:3.11-slim）
LOCAL_IMAGE="${IMAGE}:${VERSION}"
IMAGE_READY=0

if docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
  echo "• 本地已有镜像 $LOCAL_IMAGE，跳过拉取"
  IMAGE_READY=1
else
  echo "→ 正在拉取镜像 $LOCAL_IMAGE ..."
  if docker pull "$LOCAL_IMAGE"; then
    IMAGE_READY=1
    echo "✓ 镜像拉取完成"
  else
    echo "⚠ 镜像拉取失败（网络不通 / Docker Hub 不可达 / 该版本不存在）"
    echo "  将尝试从源码构建（需联网拉取基础镜像 python:3.11-slim）..."
  fi
fi

if [ "$IMAGE_READY" != "1" ]; then
  if ! docker image inspect python:3.11-slim >/dev/null 2>&1; then
    docker pull python:3.11-slim >/dev/null 2>&1 || true
  fi
  if docker image inspect python:3.11-slim >/dev/null 2>&1; then
    echo "→ 正在从源码构建镜像 $LOCAL_IMAGE ..."
    if ! docker build -t "$LOCAL_IMAGE" .; then
      echo "✗ 镜像构建失败，安装中止"
      exit 1
    fi
    IMAGE_READY=1
  else
    echo "✗ 无法获取基础镜像 python:3.11-slim，构建无法进行，安装中止。"
    echo "  可选：① 检查到 Docker Hub 的网络，或给 Docker 配置镜像加速器后重跑；"
    echo "        ② 在能联网的机器上 docker save 后拷过来 docker load。"
    exit 1
  fi
fi

# 主机架构（uname -m 归一化，与 docker 的 Architecture 字段对齐）
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  x86_64|amd64) HOST_ARCH=amd64 ;;
  aarch64|arm64) HOST_ARCH=arm64 ;;
esac

# 读某个镜像的架构。⚠️ 必须容错：
# Docker 28 的 CLI 在对象不存在时会先往 stdout 写一个空行、再以非零码退出，
# 所以绝不能写成「docker inspect ... || echo $HOST_ARCH」这种带兜底值的写法 ——
# 那样会得到「带前导空行」的值，和预期值一比就误判成架构不符。
# 这里只取第一行非空内容并去掉首尾空白；对象不存在时返回空串。
img_arch_of() {
  docker image inspect -f '{{.Architecture}}' "$1" 2>/dev/null | head -n1 | tr -d '[:space:]' || true
}

IMG_ARCH="$(img_arch_of "$LOCAL_IMAGE")"
if [ -n "$IMG_ARCH" ] && [ "$IMG_ARCH" != "$HOST_ARCH" ]; then
  echo "⚠ 镜像架构($IMG_ARCH)与主机($HOST_ARCH)不一致，容器可能无法运行。"
  echo "  官方镜像支持 amd64 / arm64；其它架构请自行 docker build 构建。"
else
  echo "✓ 镜像就绪：$LOCAL_IMAGE（架构 ${IMG_ARCH:-$HOST_ARCH}）"
fi

# ---------- 7. 启动 ----------
echo ""
echo "→ 启动 dir2chat 容器（端口 $DIR2CHAT_PORT）..."
# 镜像已就绪：显式禁止 compose 触发 build（避免意外走耗时的源码构建）
docker compose up -d --no-build

# ---------- 8. 等待健康 ----------
echo "→ 等待服务就绪（最多 60s）..."
READY=0
for i in $(seq 1 30); do
  STATUS="$(docker inspect -f '{{.State.Health.Status}}' dir2chat 2>/dev/null || echo starting)"
  if [ "$STATUS" = "healthy" ]; then READY=1; break; fi
  sleep 2
done

echo ""
echo "========================================"
if [ "$READY" = "1" ]; then
  echo "  ✓ 安装完成！dir2chat 已启动"
else
  echo "  ⚠ 容器已启动，但健康检查尚未通过，请查看日志："
  echo "    docker compose logs -f"
fi
echo "========================================"
echo ""
echo "  打开管理界面： http://<本机IP>:$DIR2CHAT_PORT"
echo "  在「通道」里添加企业微信 / QQ（记得起个名称），在「规则」里绑定 /data 目录即可。"
echo "  需要定时只在某些时段推送？在「规则」里打开该规则的「⏰ 定时开关」配置时间窗口。"
echo "  轮询间隔默认 60 秒，可在「概览 → 扫描设置」里改；每条规则也可单独设「独立轮询间隔」。"
echo "  迁移/备份？「概览」页可「备份配置 / 导入配置」。"
echo ""
echo "  常用命令（当前用户若不在 docker 组，需要 sudo；下面按需要 sudo 写）："
echo "    停止：    sudo docker compose down"
echo "    重启：    sudo docker compose restart"
echo "    查看日志： sudo docker compose logs -f"
echo "    升级：    sudo docker compose pull && sudo docker compose up -d"
echo "    换端口：  sudo DIR2CHAT_PORT=8091 bash install.sh   # 自动改 .env 并重建容器"
echo "    换监听目录：sudo WATCH_DIR=/srv/report bash install.sh   # 自动改 .env 并重建容器"
echo "    卸载：    sudo docker compose down && rm -rf config .env"
echo "    改 .env： sudo nano .env        # sudo 装的 .env 归 root，普通用户改不了"
echo ""
echo "  ⚠ 如果你是从【源码构建】的（改过源码 / Dockerfile / requirements.txt）："
echo "    改完必须重建镜像，且建议先清构建缓存，否则会一直复用旧层、改动不生效："
echo "      sudo docker compose build --no-cache     # 忽略缓存、从头构建镜像"
echo "      sudo docker compose up -d --force-recreate   # 用新镜像重建容器"
echo "    空间不够或想彻底清干净时，再清缓存 / 悬空镜像："
echo "      sudo docker builder prune                # 清理 BuildKit 构建缓存（会交互确认）"
echo "      sudo docker builder prune -af            # 同上，直接全清、不确认"
echo "      sudo docker image prune -f               # 清理悬空镜像（<none> 的旧构建层）"
echo "      sudo docker system df                    # 先看看各类占用多少，再决定清哪个"
echo ""
