# 变更日志

---

## 26.10.17（2026-10-14）

**修复 Windows 检出时的换行符问题**：仓库内容本身无变化，仅调整 `.gitattributes` 规则。

### 🩹 修复
- `.gitattributes` 新增首行 **`* text=auto eol=lf`**，让**所有文本文件**（此前只有 `.sh/.py/.yml/.md/.html` 被覆盖）都统一以 LF 存库、以 LF 检出。
- 此前未覆盖的文件（`Dockerfile`、`app/web/app.js`、`app/web/style.css`、`LICENSE`、`VERSION`、`.gitignore`、`.env.example`、`requirements.txt`）在 `core.autocrlf=true` 的 Windows 客户端上会被检出成 CRLF，可能导致从工作区 `docker compose build` 时 `Dockerfile` 的续行被 CR 破坏。现在已全部覆盖。
- 补充 `.yaml/.css/.js/.json/.txt/.example` 的显式 LF 声明，以及 `*.jpg/.jpeg/.gif/.ico/.webp/.gz/.tar/.zip` 的二进制标记。

> 说明：仓库内的 blob 一直是 LF（本次未改动任何文件内容），此修复解决的是**检出侧**行为；`.gitattributes` 的规则优先于客户端的 `core.autocrlf` 设置。

---

## 26.10.16（2026-10-14）

**文档与安装体验打磨：清除私有环境痕迹 + 新增完整英文文档**。
镜像内的服务代码逻辑无变更（仅一处 docstring 注释），主要变化在安装脚本与文档。

### 1. 🔧 安装脚本
- 默认安装目录不再写死具体路径，改为**就地安装**（安装目录 = 脚本所在目录）：`git clone` 后直接 `sudo bash install.sh` 即可，不再往固定路径拷贝。
  - 仍可用 `bash install.sh /opt/dir2chat` 指定安装目录（只拷贝运行所需文件）。
- 路径归一化改用 `realpath -m`：指定一个**尚未创建**的安装目录时不再报错（此前 `realpath` 对不存在的路径会失败并打印错误）。

### 2. 🧹 文档去私有化
- 清除 README / 教程.html / CHANGELOG 中残留的私有环境信息（私有路径、内网 IP、私有项目名及其缩写），统一改为通用占位符与通用表述。
- 安装目录示例统一为 `/opt/dir2chat`；安装后目录树改用 `<安装目录>/` 占位。

### 3. 🌐 新增英文文档
- 新增 **`README_EN.md`**：与中文 README 章节一一对应的完整英文教程（含通道能力对比表、扫描性能调优表、故障排查表）。
- 中英文 README 顶部互加语言切换链接；仓库结构清单同步补充 `README_EN.md`。

### 4. 🛠 开发工具
- `tools/make_installer.sh`：输出目录从私有路径改为仓库内 `dist/`，并标注该脚本已弃用（项目已改为镜像分发，不再发布离线安装包）。
- `tools/sync_check.py`：远端部署目录改为读取 `DIR2CHAT_REMOTE_DIR` 环境变量（默认 `/opt/dir2chat`）。

---

## 26.10.15（2026-10-14）

发布方式改为**镜像分发**，并清理文档中的内部约定。

### 1. 🚀 安装方式：离线安装包 → 镜像拉取
- 镜像发布到 Docker Hub：**`smi1ezz/dir2chat`**，同时提供 `26.10.15` 与 `latest` 两个 tag。
- 安装不再依赖 60MB+ 的离线安装包：`git clone` 仓库后 `sudo bash install.sh` 即可，脚本会自动 `docker pull`。
- `install.sh` 的镜像获取顺序调整为：**本地已有 → `docker pull` → 从源码构建兜底**（源码构建路径保留，供离线或特殊架构使用）。
- `docker-compose.yml` 的 `image:` 指向 Docker Hub 镜像，`build:` 默认注释掉；`docker compose pull && docker compose up -d` 即为升级方式。

### 2. 🍎 双架构镜像（amd64 + arm64）
- 新增 GitHub Actions 工作流：推送 `v*` tag 时自动构建 **linux/amd64 + linux/arm64** 并推送 Docker Hub，arm64 设备（树莓派 / ARM NAS 等）可直接 `docker pull` 运行。
- 镜像里不再需要针对架构做特殊处理，`docker pull` 会自动匹配本机架构。

### 3. 🧹 文档清理
- README / 教程.html 移除版本号规则说明，只保留版本号本身。
- 移除全部离线包安装说明（解压、`docker load`、预置镜像、包内结构等），统一为镜像拉取方式。
- 故障排查表相应更新：原「预置镜像架构不符」「镜像载入慢」改为镜像拉取相关条目。

### 4. 🔧 工具
- `tools/bump_version.py` 同步的镜像名更新为 `smi1ezz/dir2chat`。

---

## 26.10.14（2026-10-12）

扫描性能优化：修复历史文件挤占名额导致「新文件不推送」的风险，并避免每轮把历史文件重读一遍算哈希。

### 1. 🐞 修复：历史文件挤占扫描名额（可能导致新文件永远不推送）
- 旧逻辑先收集满 `max_files_per_round` 个候选、**之后**才查 `already_sent` 去重，且到上限就 `break` 中止遍历。于是当监听目录的文件总数超过该上限（默认仅 **30**）时，靠前的一批**早已推送过的老文件**会占满名额，排在遍历顺序之后的新文件永远轮不到 → 表现为「容器在跑、日志无报错，但新文件就是不推送」。
- 现改为**前置去重**：收集阶段就逐通道过滤，全部通道都已推送过的文件直接丢弃、**不占名额**。该参数语义回归「每轮最多推送多少个新文件」，与目录里累计多少文件彻底无关。

### 2. ⚡ 新增：内容哈希缓存 DigestCache
- `scanner.py` 新增 `DigestCache`（`OrderedDict` LRU）：以 `size + mtime_ns` 作为文件「身份证」，两者都没变就复用上次的 sha256、**不再把文件整份读一遍**，历史文件每轮开销降到一次 stat。
- 纯性能层：命中与否都不改变推送语义，`miss` 顶多重算一次哈希；超限按 LRU 淘汰，并会清理磁盘上已不存在的条目。

### 3. ⚡ 新增：目录级冷却跳过
- 子目录超过 N 天没有任何变动，则整棵子树不再进去扫描，适用于「每天一个日期目录、旧目录永不改动」的场景。

### 4. 🎛 三个参数全部可调（Web 界面 → 扫描设置）

| 参数 | 默认 | 说明 |
|---|---|---|
| `max_files_per_round` | **500**（原 30）| 每轮最多推送多少个新文件；已推送的历史文件不占名额 |
| `digest_cache_max_entries` | **5000** | 内容哈希缓存条数上限，**0 = 关闭缓存** |
| `skip_dirs_idle_days` | **0** | 目录冷却跳过天数，**0 = 关闭**；⚠️ 开启后被跳过目录的失败重试与补推都不再发生 |

- `app/config.py` 的 `DEFAULT_CONFIG` 同步添加上述字段（深合并保证旧配置自动补齐，不影响已有部署）。
- Web「扫描设置」页新增两个输入框、并修正旧默认值（30 → 500）；底层 `PUT /api/config` 可透传任意 `scan` 字段。
- ⚠️ 注意：若旧 `config.json` 里**显式写死过** `max_files_per_round: 30`，深合并会以显式值为准，升级后需手动改为 500（或到「扫描设置」页保存一次）。

### 5. 📖 文档
- README 新增「7.1 扫描性能调优（三个可调参数）」与「7.2 递归与过滤（recursive / include / exclude）」，写明各参数的设置建议、副作用，以及 `exclude` 对目录名生效并整棵子树剪枝的行为。

---

## 26.10.13（2026-10-12）

「发送记录」与「事件日志」新增一键清空功能，方便重新推送 / 补发。

### 1. ✨ 新增「清空」按钮
- 记录页工具栏新增「🗑 清空发送记录 / 清空事件日志」按钮，文案随「发送记录 / 事件日志」视图自动切换。
- 点击二次确认：清空发送记录后，下一轮扫描会重新推送监听目录里所有文件（用于重发 / 补发）；清空事件日志仅清审计记录。

### 2. 🔧 后端接口
- `store.py` 新增 `clear_sent()`（清空 `sent` 表全部）、`clear_events()`（清空 `events` 表全部）。
- `main.py` 新增 `POST /api/history/purge`、`POST /api/events/purge`，返回 `{ok, deleted}`。

---

## 26.10.12（2026-10-12）

修企业微信智能机器人（wecom_bot）长连接「假就绪」与凭据诊断问题。

### 1. 🐞 修长连接「假 ready」（导致收不到 @ 消息却显示已连接）
- 旧 `wecom_bot.py` 在**发出 `aibot_subscribe` 的瞬间**就 `self._ready.set()`，完全没等服务端订阅回执，于是 `health` 恒为 `ready:true`。用户填了错误 bot_id/secret 时，界面显示「已连接」、实际服务端在订阅阶段（握手后第一个回执）就因 `errcode=853000 invalid bot_id or secret` 把连接掐断、`ConnectionClosedError` 无限重连抖动，@ 消息全落在断开窗口、企微不补发 → `chats` 永远空、界面刷不出 chatid。
- 修复：`_run` 改为「建连 → 起 `_read_loop` 协程 → 发订阅(带 req_id) → `await` 该 req_id 的回执(15s 超时) → `errcode!=0` 才 `close(); continue`，**不再提前 set ready**」，必须收到 `errcode=0` 才算就绪。`_read_loop` 区分订阅回执帧（带 errcode/errmsg）与消息回调帧。

### 2. 🔍 订阅诊断与日志清理
- 断线异常处抓取 `exc.rcode`/`exc.reason`；连接后第一帧订阅回执即打印 `errcode/errmsg`，便于第一时间定位凭据问题。
- 调试期临时打印的「每帧 RECV」噪声日志已清理，保留「订阅失败」「连接异常断开」「收到消息回调」三条有意义日志。

### 3. ✅ 实测
- 自建 NAS 实例更正 bot_id/secret 后订阅回执 `errcode=0`、长连接稳定；群内 @ 机器人成功写入 chatid、Web「获取会话」刷出、规则推送已送达目标群。

---

## 26.10.11（2026-10-12）

**修 `.env` 追加时与上一行粘连的 Bug**，新增 `WATCH_DIR=` 前置变量，补 sudo / 清构建缓存的提示。

### 1. 🐞 修 `.env` 被写成一行（严重）

用户实测截图：

```
# 要监听的宿主目录，会被只读挂载到容器 /data
WATCH_DIR=/你真实的目录DIR2CHAT_PORT=8899   ← 两行粘成一行
```

**根因**：`install.sh` 写端口用的是裸追加：

```bash
echo "DIR2CHAT_PORT=$DIR2CHAT_PORT" >> .env
```

如果 `.env` 的**最后一行没有换行符**（手工编辑过很容易这样：改完 `WATCH_DIR` 存盘，
编辑器没补行尾换行），追加就变成「接在最后一行尾巴上」而不是新起一行。
于是 `WATCH_DIR` 变成一个不存在的路径、`DIR2CHAT_PORT` 也完全失效——两个配置同时坏掉，
而且从界面上看不出问题，只有服务起不来或挂载了空目录。

**修法**：新增 `env_set()` 统一写 `.env`，用 **awk 重写整个文件**（而不是追加），
KEY 存在就整行替换、不存在才新增，天然保证「一行一条」：

```bash
env_set() {
  local key="$1" val="$2"
  awk -v k="$key" -v v="$val" '
    $0 ~ "^" k "=" { print k "=" v; seen = 1; next }
    { print }
    END { if (!seen) print k "=" v }
  ' .env > .env.tmp && mv .env.tmp .env
}
```

顺带好处：连「`.env` 最后一行没有换行符」这种情况也一并修正了（awk 输出总会补齐行尾换行）。

### 2. 新增 `WATCH_DIR=` 前置变量

以前只能改完 `.env` 再跑，现在可以一条命令搞定：

```bash
sudo WATCH_DIR=/你真实的目录 bash install.sh
```

和 `DIR2CHAT_PORT` 一样会写回 `.env`。可与端口叠加：

```bash
sudo DIR2CHAT_PORT=8091 WATCH_DIR=/你真实的目录 bash install.sh
```

> 实现细节：环境变量 `WATCH_DIR` 必须在解析参数时就存进 `WATCH_DIR_ENV`，
> 因为第 4 步会用同名变量去读 `.env`，届时环境变量已被覆盖。

### 3. Docker 权限检查：直接告诉用户加 sudo

以前只检查 `command -v docker` 和 `docker compose version`，普通用户（不在 docker 组）
会一路跑到 `docker load` 才报 `permission denied`，信息也不指向解决办法。现在增加守护进程连通性检查：

```
✗ 无法连接 Docker 守护进程。若报的是「permission denied ... docker.sock」，
  说明当前用户没有 Docker 权限，请改用 root 运行：
    sudo bash install.sh
  或把当前用户加入 docker 组后重新登录：
    sudo usermod -aG docker $USER && newgrp docker
```

### 4. 结尾提示补上「手动构建后如何清缓存」

`install.sh` 跑完的输出、README 第 10 节、教程第 17 节都补了：

```bash
docker compose build --no-cache            # 忽略缓存、从头构建
docker compose up -d --force-recreate
docker builder prune            /  -af     # 清 BuildKit 构建缓存
docker image prune -f                      # 清悬空镜像（<none> 旧层）
docker system df                           # 先看占用再决定清什么
docker system prune -af                    # 彻底清（慎用）
```

并说明**清缓存不会动到 `config/` 里的数据**。

### 5. 文档

- README 与教程都明确：**跑 `install.sh` 前要加 `sudo`**（或先加入 docker 组）。
- 教程「配置监听目录」一节补了「**一行一条，不要粘成一行**」的红框警示 + 本次的错误示例。
- README 章节重新编号（新增第 10 节「手动构建 & 清理构建缓存」，故障排查顺延为 11、版本与升级为 12），
  并修掉第 12 节里残留的旧版本号（26.10.3）。

---

## 26.10.10（2026-10-12）

**安装目录只保留「跑起来真正需要的东西」**，顺手修掉包内混入字节码缓存的问题。

### 1. 改法：install.sh 指定安装目录时按白名单拷贝

原先第 1 步是「把包内容整包复制过去」：

```bash
tar -C "$PKG_DIR" --exclude='config' --exclude='.env' --exclude='.git' -cf - . \
  | tar -C "$INSTALL_DIR" -xf -
```

结果是安装目录里会堆上一堆跟运行无关的东西：README、教程.html、CHANGELOG（合计 ~76KB 文档）、
`.gitignore`、`tools/`（开发工具），以及 **~68MB 的预置镜像 tar**（载入 Docker 之后就没用了）。
用 `./install.sh <目录>` 安装一次，安装目录里 98% 的体积是这辈子不会再读的文件。

现在改为**显式白名单**：

```bash
COPY_ITEMS="app docker-compose.yml Dockerfile requirements.txt VERSION .env.example install.sh"
```

- 只有这 7 项会进安装目录；`README.md / 教程.html / CHANGELOG.md / .gitignore / tools/` 一律留在包里。
- `install.sh` 本身保留，方便日后在安装目录直接 `./install.sh` 升级。
- 拷贝后顺手清掉源码里夹带的 `__pycache__` 与 `*.pyc`。
- `FORCE=1` 的语义不变：覆盖程序文件、保留 `config/` 与 `.env`（这两项本来就不在白名单里，天然不会被覆盖）。

### 2. 预置镜像不再拷进安装目录

`dir2chat-<版本>-image.tar.gz` 约 68MB，载入 Docker 后即无用。现在：

- 镜像 tar 的查找顺序为 **安装目录 → 包目录**（在安装目录放一份可覆盖包里的）。
- 因此「装到别的目录」时不会再白白复制 68MB，但离线载入照旧可用。

### 3. 顺手修的 26.10.9 小回归

26.10.9 把「镜像已存在」的判断写成了 `[ -n "$IMAGE_TAR" ] && docker image inspect ...`，
于是**包里没有 tar（或已删除）时，哪怕镜像早就载入过，也会掉进「未找到预置镜像 → 改源码构建」
的分支**，在离线机上直接中止。现改为先判断镜像是否已在本地：

```bash
if docker image inspect "dir2chat:${VERSION}" >/dev/null 2>&1; then   # 先看本地有没有
  IMAGE_READY=1
elif [ -n "$IMAGE_TAR" ]; then                                        # 再决定要不要 load
  ...
```

### 4. 修打包脚本漏排 `__pycache__`

`_mkpkg.sh` 里只写了 `--exclude=./__pycache__ --exclude=./app/__pycache__`，
**漏掉了 `app/channels/__pycache__`**，导致 26.10.9 的包里混进了 6 个
`*.cpython-313.pyc`（本机是 Py3.13，容器是 Py3.11，根本不会被加载，纯垃圾）。
现改为 `--exclude='*__pycache__*' --exclude='*.pyc' --exclude='*.pyo'`，一网打尽。

---

## 26.10.9（2026-10-12）

**修一个会让离线安装在 amd64 机器上「假报架构不符 → 回退联网构建 → 必然失败」的 installer Bug。**

### 1. 现象

在 amd64 机器上跑离线安装包，日志出现：

```
✓ config/ 目录就绪（数据持久化于此处）
⚠ 预置镜像架构(
amd64)与主机(amd64)不符，将改为从源码构建      ← 主机和镜像明明都是 amd64
→ 启动 dir2chat 容器（端口 8899）...
 => ERROR [internal] load metadata for docker.io/library/python:3.11-slim
failed to solve: ... unexpected status from HEAD request to
  https://docker.finmas.com/v2/library/python/manifests/3.11-slim: 401 Unauthorized
```

即：**明明带着预置镜像，却被丢弃，转去「从源码构建」**；源码构建要联网拉
`python:3.11-slim`，而该机镜像仓库不可用（401 / 超时）→ 安装失败。

### 2. 根因

`install.sh` 第 6 步的架构预检写错了对象 —— 它去 inspect **一个此刻还不存在的镜像**：

```bash
# 镜像要到下一步才 docker load，这里 inspect 必然失败
IMG_ARCH="$(docker inspect -f '{{.Architecture}}' "dir2chat:${VERSION}" 2>/dev/null || echo "$HOST_ARCH")"
```

本来 `|| echo "$HOST_ARCH"` 兜底是为了让「取不到就当主机架构」，但踩中了
**Docker 28 CLI 的一个坑**：对象不存在时，它会**先往 stdout 写一个空行**、再以非零码退出。
于是命令替换的实际结果是：

```
"\n"  +  "amd64"   →   IMG_ARCH = "\namd64"
```

`[ "\namd64" != "amd64" ]` → 成立 → 误判「不符」→ 丢镜像、改构建。
截图里那行诡异断行（`预置镜像架构(` 之后换行），就是 `\n` 被原样拼进 echo 造成的。

本机实测复现（Docker 28.5.2, build ecc6942）：

```
$ docker inspect -f '{{.Architecture}}' dir2chat:99.99.99 2>/dev/null | od -c
0000000  \n            ← 就一个空行
$ IMG_ARCH="$(docker inspect -f '{{.Architecture}}' dir2chat:99.99.99 2>/dev/null || echo amd64)"
$ printf '%s' "$IMG_ARCH" | od -c
0000000  \n   a   m   d   6   4      ← "\namd64"
```

### 3. 修法

1. **先 `docker load`，再校验架构。** `docker load` 是纯本地操作、不需要联网，先载入没有副作用，
   也彻底避开「读一个还不存在的镜像」这个逻辑错误。
2. 读架构改为容错写法 —— 只取第一行、去掉全部空白，取不到就是空串：
   ```bash
   docker image inspect -f '{{.Architecture}}' "$1" 2>/dev/null | head -n1 | tr -d '[:space:]' || true
   ```
   **严禁**再写成 `$(... || echo "$HOST_ARCH")` 这种「失败就拼接兜底值」的形式。
3. **架构不一致时只告警，不再回退去联网构建** —— 离线机联网构建必然失败；保留镜像至少能给出
   真实错误，也便于用户改用 binfmt/qemu 跨架构模拟或换对应架构的包。
4. 完全找不到预置镜像时，**先探测本地有没有 `python:3.11-slim`**，没有就直接给出可操作的错误
   并中止，不再傻等一次注定失败的联网构建。
5. 启动时若镜像已在本地，改用 **`docker compose up -d --no-build`**，显式禁止 compose 触发任何
   联网 build；从此不会再出现「预置镜像白带了」的情况。

> 教训同 v0.3.0 那次回归：**脚本里的「兜底 `|| echo`」很容易把异常值悄悄拼进正常值**，
> 凡是「取外部命令输出 + 兜底默认值」，都要按「先归一化/裁剪、再比较」来写。

### 4. 影响面

- 仅改 `install.sh`（+ 文档版本号）。**应用代码与镜像内容无变化**，`dir2chat:26.10.9` 镜像由
  `dir2chat:26.10.8` 派生、只更新 `/app/VERSION`。
- 已装好的实例不受影响；本版只影响「用离线包在新机器上安装」这条路径。

---

## 26.10.8（2026-10-11）

**改用 Docker 系统自带的 `bridge` 网络，不再让 compose 另建 `dir2chat_default`**。

### 1. 背景

之前 compose 没有声明网络，`docker compose up` 就自动创建了一个项目级网络  
`dir2chat_default`（bridge 驱动）。宿主机网络列表里因此多出一条，而本项目  
**只有一个容器、只对外暴露 8899 一个端口，没有任何服务间互相发现的需求**，  
再单独建一个网络没有意义。

### 2. 改法

```yaml
services:
  dir2chat:
    # ...
    # 使用 Docker 自带的 bridge 网络，不再让 compose 另建 dir2chat_default
    network_mode: bridge
```

并在**顶层删除** `networks:` 段。

> ⚠️ **为什么用 `network_mode` 而不是 `networks:`？**
>
> 直觉写法是 `networks: [bridge]` 或
>
> ```yaml
> networks:
>   bridge: { external: true, name: bridge }
> ```
>
> 但这两种写法**都会失败**，报错：
>
> ```
> invalid config for network bridge: invalid endpoint settings:
> network-scoped alias is supported only for containers in user defined networks
> ```
>
> 原因：compose 给容器挂网络时，会**自动注入一个 network-scoped 别名**（默认是项目名）；  
> 而系统自带的 `bridge` 属于 **non-user-defined** 网络，daemon 层面不支持任何别名，  
> 于是直接拒绝。`aliases: []`、显式写 `aliases: [dir2chat]` 也都不行——  
> `[]` 会被 compose 规范化成 `{}` 后仍注入默认别名，显式别名更是直接命中该限制。
>
> **`network_mode: "bridge"` 走的是「容器模式下指定网络」这条路**，  
> compose 完全不参与网络的创建与别名注入，正是为这种场景准备的。

### 3. 端口映射不受影响

端口仍然用 `ports: ["8899:8899"]` 映射即可，  
等价于 `docker run --network bridge -p 8899:8899`（DNAT 由 iptables 处理），  
访问方式 `http://<host>:8899` 不变。

### 4. 迁移注意

从旧版本升级时，旧的 `dir2chat_default` 网络会残留（容器已换到 `bridge`）。  
先确认没有容器占用，再删除：

```sh
docker network inspect dir2chat_default --format '{{range .Containers}}{{.Name}} {{end}}'
docker network rm dir2chat_default
```

新装用户不会产生这个网络，无需处理。

---

## 26.10.7（2026-10-11）

**顶栏 / 标签栏 / 内容区改为居中（上一版是左对齐，按用户反馈改回居中）**。

### 1. 三块内容等宽居中

26.10.6 按截图做成了左对齐，用户确认后要求改回**居中**。改动只在宽度与 margin：

```css
.bar-inner, .page-width {
  width: min(100%, var(--content-w));
  margin-left: auto;
  margin-right: auto;
  padding-left: var(--gutter-l); padding-right: var(--gutter-r);
}
```

### 2. ⚠️ 踩到的坑：`width:calc(100% - x)` + `max-width` 无法居中

最初写成 `width: calc(100% - var(--sbw)); max-width: var(--content-w); margin: auto;`  
结果 `margin-left` 计算值仍是 `0px`，内容贴左、右侧一大片空白。

**原因**：块级元素的 `width` 一旦是百分比 / `calc()`，会**先解析成容器宽**，  
`max-width` 只是把它压小；此时剩余空间**不会**被 `auto` margin 吸收  
（`auto` 只在「宽度已是确定值且没用满容器」时才均分）。  
正确写法是用 `width: min(100%, var(--content-w))` 让宽度**直接算成最终值**，  
`auto` margin 才会把余量平分到两侧。

### 3. 顺带修掉两个隐藏问题

- **`html { scrollbar-gutter: stable }` 必须去掉**。`body` 是 `overflow:hidden` 的，  
  根本不会产生滚动条，`html` 预留的槽纯属浪费；更糟的是它把 `body` 压成「视口 − 10px」，  
  于是 `body` 内的 `main` 再算上自己的滚动条后就只剩「视口 − 20px」，  
  **滚动条被推到距窗口右缘 10px 处**，直接违背「滚动条永远贴窗口最右侧」这条硬约束。  
  改成 `scrollbar-gutter: auto` 后，`main` 的滚动条严丝合缝贴在窗口最右边。
- **`main { scrollbar-gutter: stable }` 同样去掉**（原因同上，会二次预留）。

### 4. ≤1600px 区间的等宽处理

取消宽度上限后，`.page-width` 在 `main` 内（`main` 的 client 宽已是「视口 − 滚动条」），  
直接 `width: 100%` 即可；`.bar-inner` 在 `main` 外、拿到的是完整视口宽，  
需要自己减掉 `--sbw`：

```css
@media (max-width: 1600px) {
  .bar-inner { width: calc(100% - var(--sbw)); }
  .page-width { width: 100%; }
}
```

两边由此严格等宽；此时残留的 5px 水平错位来自「`main` 的滚动条挤在右侧」，  
属预期且不影响观感。

### 5. 验证

本机 + 线上实例（`:8899`）共 10 个断点（1920 / 1800 / 1700 / 1600 / 1400 /  
1280 / 1024 / 900 / 800 / 700 / 600 / 375）全部满足：  
红框与黄框**宽度相等**、**滚动条贴窗口最右**、**logo 正常加载**、**留白点击可翻页**。  
1920px 实测：红框 `[184,1727]`、黄框 `[189,1732]`，均 1543px 宽，左右留白 183.5 / 183.5。

---

## 26.10.6（2026-10-11）

**顶栏/标签栏加宽 30% 并与内容区等宽左对齐 + 项目 logo 与网页图标**。

### 1. 黄框（顶栏 + 标签栏）加宽 30%

原顶栏/标签栏按窗口全宽铺开（`padding: 14px 26px`），内容区却是 1187px 居中，  
两者宽度不一致。现在把宽度收敛到统一的「内容宽度」变量：

```css
:root { --content-w: 1543px; --gutter-l: 26px; --gutter-r: 34px; }
```

`1543px = 1187px（msn.cn 实测内容列宽）× 1.3`。顶栏与标签栏外层改为  
`<div class="bar-inner">`（`max-width: var(--content-w)`），三块内容共用同一套变量。

### 2. 红框（内容区）与黄框等宽，并按截图左对齐

`.page-width` 与 `.bar-inner` 现在是**同一套宽度 + 同左留白**：

```css
.bar-inner, .page-width {
  width: 100%; max-width: var(--content-w);
  margin-left: 0; margin-right: auto;   /* 左对齐，右侧自然留白 */
  padding-left: var(--gutter-l); padding-right: var(--gutter-r);
}
```

上一版是 `margin: auto` 居中，本版按用户截图改为**左对齐**：内容贴左，右侧留大片空白。  
`--content-w` 是唯一真源——以后想让黄框红框一起变宽变窄，只改这一行。

### 3. 项目 logo + 网页图标（favicon）

- 用 **Agnes（agnes-image-2.5-flash）** 生成项目 logo：蓝 → 紫罗兰渐变描边的  
  圆角对话气泡，内置一个向上箭头（「目录文件推送到会话」的意象），白底 2048×2048。
- 用 Pillow 做**边界 flood fill** 去白底（只去掉与画布边缘连通的白色，图形内部的  
  白色高光保留），再按 `getbbox()` 裁切 + 8% 边距正方形化。
- 导出并接入：

| 文件                                     | 用途                 |
| -------------------------------------- | ------------------ |
| `app/web/icon-64.png`                  | 顶栏左侧 logo（26px 显示） |
| `app/web/favicon-32.png`、`icon-16.png` | 浏览器标签页图标           |
| `app/web/apple-touch-icon.png`         | iOS 添加到主屏          |
| `app/web/icon-512.png`、`icon-192.png`  | PWA / 高清场景备用       |

`index.html` 的 `<head>` 增加 `<link rel="icon">` / `<link rel="apple-touch-icon">`，  
顶栏 `.brand` 由「一个 CSS 渐变小方块」换成真实 logo 图片：

```html
<div class="brand">
  <img class="brand-logo" src="/static/icon-64.png" alt="dir2chat" width="26" height="26">
  <span class="brand-name">dir2chat</span><span class="ver" id="ver"></span>
</div>
```

> 按用户选择，采用**纯图标 + 文字标签**形态：图标是主标识，文字保留以便识别。  
> 原先的 `.brand::before` 渐变方块已移除，避免与 logo 重复。

### 4. 滚动条贴最右 + 全屏翻页（回归验证，不得破坏）

这两条是硬约束，本次改动**继续保持**：

- `main` 仍是唯一滚动容器且 `width: 100%` 通栏，`scrollbar-gutter: stable`  
  → 滚动条永远贴窗口最右侧，与内容宽度无关。
- 翻页仍由 `main` 上的点击委托 `bindBlankPaging()` 处理，命中  
  `button/a/input/select/textarea/label/.item/.block/table/...` 才不翻页，  
  其余（含左右留白、滚动条旁的空白）都能翻页。
- 本次 `main` 的内边距保持在纵向（水平内边距归零），水平留白全部交给  
  `.page-width`，因此「通栏可点」的范围没有任何缩减。

### 5. 响应式同步

`.bar-inner` 与 `.page-width` 的断点保持一致（900 / 700 / 380 / 安全区），  
避免窄屏下顶栏比内容区宽一圈。窄屏下顶栏/标签栏恢复贴边内边距。

---

## 26.10.5（2026-10-11）

**内容区收窄到 msn.cn 的实际宽度（滚动条贴最右）+ 去掉重复的「自定义」标记**。

### 1. 内容宽度真正对齐 msn.cn，滚动条回到窗口最右侧

上一版把 `main` 的内边距改成「左 18 / 右 26」，但内容仍是**随窗口无限拉长**的。  
用户期望的是：**内容实际只有 msn.cn 那么宽**（红框范围），  
同时**滚动条在窗口最右侧**，且**翻页仍是全屏翻页**。

做法（三层解耦）：

- `main` 继续**铺满整个可视宽度**并作为唯一滚动容器 → 滚动条永远贴窗口右侧，  
  且点击容器内任意位置（含左右留白、滚动条旁）都能翻页。
- `main` 自身内边距归零，新增内层 `.page-width` 包裹全部面板：  
  `max-width: 1187px` + 左右 `auto` 居中 + 内边距 `14px / 29px`。  
  1187px 正是实测 msn.cn 在同尺寸窗口下的内容列宽度（视口 − 14 左偏移 − 29 右留白）。
- 窗口 ≤ 1240px 时取消上限，退化为全宽 + `18px / 26px` 内边距，避免窄屏被明显压缩。

实测（1280 视口）：`main` 宽 1270/1280（滚动条贴右），`.page-width` 宽 **1187px**、  
左 37 / 右 57（居中），翻页仍可从左右留白触发。

### 2. 修复自定义服务器显示成「…（自定义）· 自定义」

服务端把自定义项的 label 生成为 `192.168.1.1（自定义）`，  
前端渲染下拉时又追加了一次 ` · 自定义` → 界面上重复出现两次。  
本版去掉前端的追加（标记改由服务端 `custom: true` 字段承载），  
并把该字段显式写入配置，供前端列表判断使用。现在下拉里只显示一次。

---

## 26.10.4（2026-10-11）

**对齐 msn.cn 的内容宽度观感 + 自定义时间服务器 + Cloudflare 备注**（承接上一版）。

### 1. 内容宽度改为「外框全宽 + 内层左对齐留白」（对齐 msn.cn）

上一版把 `main` 改成铺满全宽后可滚动，但两侧留白仍是**左右等分**，观感与参考站不同。  
本版实测 `msn.cn` 的真实做法（穿透 shadow DOM 读到 `.content-container`）：

| 项          | msn.cn 实测（1280 视口）          |
| ---------- | --------------------------- |
| 外层 wrapper | `1265px`（≈铺满，背景/顶栏通栏）       |
| 内容列        | `1238px`，`max-width: 100%`  |
| 左偏移 / 右留白  | **14px / 29px**（左小右大，非等分居中） |
| 内部节奏       | `gap: 12px`                 |

据此把 `main` 的内边距由「左右各 26px」改为 **左 18px / 右 26px**（左小右大），  
并统一面板内块级节奏为 12px。实测（1280 视口）：`main` 宽 1270/1280、  
`overflow-y: auto`、左偏移 18px、右留白 26px。  
**翻页仍为全屏空白点击翻页**，未做任何收窄，只调整视觉留白。

### 2. 时间服务器支持自定义（含局域网地址）

- 后端 `systemtime.normalize_server()` 统一校验并清洗地址：  
  支持**域名 / IPv4 / IPv6 / 局域网主机名**，可带端口（如 `192.168.1.5:123`），  
  并自动剥掉误粘贴的 `ntp://`、`http(s)://` 前缀与路径。  
  非法输入（空、超长 >255、含非法字符、端口越界）一律 `400` + 可读原因，  
  不再把脏字符串塞进 socket。
- 新增 `time.custom_servers` 配置项持久化自定义列表；  
  `GET /api/time/zones` 返回内置 + 自定义合并项（自定义项带 `custom: true`）。
- 新增接口：`POST /api/time/servers`（添加）、`POST /api/time/servers/delete`（删除）。  
  与内置项重名时直接选中而不重复添加；删除当前正在用的项会自动回落默认服务器。
- 界面：时区与时间面板新增「自定义服务器」区——输入框 + 添加按钮 + 已添加项 chip 列表  
  （chip 可点 `×` 删除）；下拉里自定义项标注「· 自定义」。  
  `POST /api/time/ntp/sync`、`/test`、`PUT /api/time/config` 均改用同一套校验。

### 3. Cloudflare 时间服务器补备注

- `time.cloudflare.com` 的显示名由 `time.cloudflare.com` 改为  
  **`time.cloudflare.com（Cloudflare）`**，与「（腾讯）/（阿里云）/（微软）」等风格一致。

---

## 26.10.3（2026-10-11）

**全宽翻页 + 系统时间/时区/NTP + 手机端适配**（三处体验升级）。

### 1. 翻页不再受内容宽度限制

- `main` 去掉 `max-width: 1080px` + `margin: 0 auto` 居中收窄，改为**铺满整个可视宽度**滚动。  
  之前在宽屏上两侧会各留约 100px 的"死边距"，那块区域既不显示内容也不参与滚动，  
  看起来就是"翻页时旁边有一条永远不动的空白"。
- 记录页新增 **`#log-blank` 空白翻页区**：表格下方的留白点击即可翻页。
- 在 `<main>` 上做**点击委托**：点到页面任意留白处都能翻页（无下一页时自动翻上一页），  
  点到表格、表单、按钮、标签等交互元素时**不会**误触发。
- 实测（Chromium）：`main` 宽度 1270/1280，`overflow-y: auto`，可滚动；  
  点留白页码 `2 → 3` 成功，点表格单元格页码不变。

### 2. 新增「时区与时间」

- 新增 `app/systemtime.py`：自己实现**轻量 SNTP 客户端**（UDP 123 + 标准报文），  
  不依赖 ntpd/chrony/systemd-timesyncd，容器里直接可用。
- **自动同步 / 手动设置**两种模式（对应参考界面里的单选）：
  - 自动同步：可选时间服务器（腾讯 `ntp.tencent.com`、阿里云、NTP 池、Cloudflare 等），  
    支持「测试服务器」（只查询并显示本机偏差与延迟）与「立即同步」；  
    另有 `time.auto_sync_hours`（默认 6 小时）后台**周期自动对时**。
  - 手动设置：`datetime-local` 选择时间，可一键「填入当前时间」，按所选时区解释后写入。
- **时区可调**：内置 25 个常用时区（含「(UTC+08:00) 北京，重庆，中国香港特别行政区」），  
  并自动补充 zoneinfo 里的其它地区；切换会同时更新 `/etc/localtime` 与 `TZ`。
- 概览页新增**实时时钟**（每秒跳动，按所选时区显示）与时区/偏移徽标。
- 权限说明：改系统时间需要 `CAP_SYS_TIME`，已在 `docker-compose.yml` 加上  
  `cap_add: [SYS_TIME]`；若权限不足，界面会明确提示原因与修法，而不是静默失败。
- 新增接口：`GET /api/time`、`GET /api/time/zones`、`PUT /api/time/timezone`、  
  `POST /api/time/manual`、`POST /api/time/ntp/sync`、`POST /api/time/ntp/test`、  
  `PUT /api/time/config`。

### 3. 手机端适配

- 顶栏在手机上从 **237px 收敛到 77px**：运行状态默认收起，点 📊 图标展开/折叠。
- 触摸目标全部加大：主要按钮 ≥42px、次级 ≥40px、单选/开关标签 ≥40px（原先仅 33–35px）。
- 表单在窄屏改为**单列全宽**、输入框加高到 42px 且字号 15px（避免 iOS 自动放大）。
- 记录页：筛选器全宽堆叠、分页按钮摊开更好点、表格横向滚动更顺滑、行高微调。
- 标签栏在窄屏可**横向滑动**且吸顶，不再挤压变形。
- 新增 `@media (max-width: 900px / 700px / 380px)` 三档断点，  
  以及 `@media (hover: none)` 去除触屏设备上"粘住"的 hover 样式；  
  并适配全面屏安全区 `env(safe-area-inset-bottom)`。
- 实测（390×844）：无横向溢出，`body` 恢复整页滚动，15 行分页与空白翻页均正常。

---

## 26.10.2（2026-10-11）

**记录页改为固定 15 行分页，去掉纵向滚动**。

- 每页固定显示 **15 行**（`PAGE_SIZE` 由 20 改为 15）。
- 表格容器**纵向不再滚动**（`overflow-y: hidden`），彻底消除"既能滚又能翻页"两套机制并存的问题；超出 15 条只靠**翻页**查看。
- 不足 15 行时自动**补空行占位**，因此无论本页有几条记录（15 / 3 / 0 条），表格总高恒定（684px），翻页不会上下跳动。
- 记录为 0 条时，第一行补空位置显示「暂无记录 / 暂无事件」。
- 行高统一固定（真实行与补空行同高），保证两视图、各页高度完全一致。

---

## 26.10.1（2026-10-11）

**布局固定 + 轮询间隔真正可调 + 绑定通道过滤**（三处体验修复）。

**1. 标签栏固定，翻页不再上下滑动**

- 整页改为固定视口布局：`body` 撑满视口高度、纵向 flex，顶栏与「概览/通道/规则/记录」标签栏固定在原位不动，只有内容区（`main`）内部滚动。
- 切换标签时内容区自动回到顶部；标签栏本身永远停在原位，无论内容多长、翻到第几页都不会上下跳动。
- 记录表容器改为**固定高度**（480px，原为 `max-height`），翻页时不论行数多少高度恒定，去掉弹跳。
- 小屏（≤640px）保持整页滚动，恢复移动端自然手感。

**2. 轮询间隔可调（全局 + 规则级）**

- **概览页新增「扫描设置」区块**：可调轮询间隔（秒，最小 5）、文件稳定时间、每轮最多处理文件数、扫描总开关，一键保存到 `scan.*`。
- **每条规则可设「独立轮询间隔」**：填 `0` 或留空 = 跟随全局；填 ≥5 的整数则该规则单独按此间隔扫描（存于 `rules[].interval_seconds`）。
- 调度重构：扫描循环为每条规则维护「下次到期时间」，到点才扫该规则；下次唤醒时间取「最近到期规则 / 最近定时相位切换 / 全局间隔」三者最小，不同规则的独立间隔互不干扰。
- `/api/status` 的 `rules[]` 新增 `interval_seconds`（生效值）、`own_interval_seconds`（规则自设值）、`next_due_in`（距下次扫描秒数）。


**3. 规则绑定通道只显示「已启用」的通道**
- 规则卡片的通道绑定列表默认只列出已启用的通道；未启用的不再出现。
- 已绑定但随后被停用的通道**仍保留显示**（虚线灰显 + 「已停用」标签），避免出现「看不见又解不掉」的死角；其余未绑定的停用通道隐藏，并提示隐藏了几个。

---

## 26.10.0（2026-10-11）

**结构性调整 + 记录页重做 + 配置导入导出**（本版起启用新的 `年.月.序号` 版本规则）。

**1. 定时开关改到「规则内」**（破坏性调整：全局定时移除）
- 定时开关从「概览」页挪到**每条规则内部**，每条规则独立控制自己的推送时段；不同规则可用不同窗口。
- 移除全局 `scan.schedule`；`/api/status` 不再返回全局 `schedule`，改为在 `rules[]` 中返回每条规则的 `schedule`（`enabled` / `active`）。
- 扫描循环改为按规则判定窗口：只扫「当前在窗口内」的规则；规则相位切换时各记一条日志。新增 `seconds_to_next_change()`，有规则启用定时时按「最近一次相位切换」精确唤醒，窗口边界不再多等一个轮询周期。
- 手动「立即扫描」不受任何规则定时限制。
- 旧配置文件的全局 `scan.schedule` 字段会被忽略（不再生效），不报错。

**2. 通道支持自定义名称**
- 新增通道时可填名称；通道卡片内也可随时改名。规则绑定与发送记录均显示自定义名称，避免多个同类通道混淆。

**3. 规则页增强**
- 规则卡片头部显示**绑定的通道名称**。
- 新增「↻ 刷新通道」按钮：新建通道后无需重载页面即可刷新可选通道列表。
- 绑定通道改为**点击式标签（chip）多选**，比原来的 Ctrl 多选更直观。
- **规则内可调轮询间隔**：把扫描间隔从「只读展示」恢复为可编辑（存于 `scan.interval_seconds`）。

**4. 记录页重做**
- 「发送记录」与「事件日志」改为**两个并列按钮**，点哪个显示哪个。
- 表格**固定宽度**、超宽**横向滚动（翻页）**、纵向限高；单元格内容超出**省略号截断**，**悬停显示完整内容**（气泡）。
- 各表支持**筛选**：时间（模糊）、通道、类型、级别；发送记录保留「只看失败/跳过」。
- 新增**分页**（每页 20 条）与**导出按钮**（导出当前筛选结果为 CSV，含 BOM 便于 Excel 打开中文）。

**5. 概览页新增「配置备份」**
- **备份配置**：一键导出完整配置（含通道凭据、规则、定时、扫描参数）为 JSON。
- **导入配置**：选择 JSON 文件整体覆盖当前配置（有二次确认）。
- 新增接口：`GET /api/config/export`（附件下载）、`POST /api/config/import`（整体替换并热更新通道实例）。

**6. 网络**
- 保持 Docker 默认 `bridge` 网络，未做改动（确认项）。

**7. 版本规则变更**
- 启用新版本规则 `年.月.序号`；本版号 `26.10.0`（2026 年 10 月第 1 版）。工具 `tools/bump_version.py` 支持 `--dry-run` / `--set` / `--date`。

---

## 0.4.0（2026-10-11）

**新增「定时开关」功能**（新增配置项 / 交互，minor）。*（该功能在本版已被「规则内定时」取代。）*

- 概览页新增「⏰ 定时开关」区块：可启用定时，并配置多条「时间窗口」（HH:MM 起止，支持跨午夜如 `22:00`-`06:00`）。
- 窗口内才自动扫描推送，窗口外自动暂停扫描（不丢文件，回到窗口后继续推送）。手动「立即扫描」不受定时限制。
- 实时相位指示：顶栏与区块显示「当前在推送窗口内 / 窗口外已暂停」，并给出下次自动切换时间。
- 时间按指定时区解析（默认 `Asia/Shanghai`，依赖 `tzdata`；环境无时区数据时回退服务器本地时间）。
- 状态接口 `/api/status` 增加 `schedule` 字段（`enabled` / `timezone` / `windows` / `active` / `phase`）；`PUT /api/config` 支持整体写入 `schedule`。
- 相位切换时只在切换那一刻记一条日志（进入/离开窗口），不刷屏。
- 部署镜像新增 `tzdata` 依赖。

**离线安装包同步重建**：`dir2chat-installer-v0.4.0.tar.gz`（含 `dir2chat-0.4.0-image.tar.gz` linux/amd64 预置镜像、更新后的 `install.sh` / `README.md` / `教程.html`，内置「定时开关」说明）。旧版 `dir2chat-installer-v0.3.1.tar.gz` 保留不删。

---

## 0.3.1（2026-10-11）

**修复 0.3.0 引入的初始化崩溃**（bug 修复，patch）。

- `app/web/app.js` 的 `init()` 里遗留了两行对已删除的原生 `<select id="new-channel-type">` 的引用，同时引用了未定义的 `s` 变量 → init 抛 TypeError，**`bindTabs()` 未执行，所有标签页点击无法切换**。两行死代码已删除（版本徽标本就由 `refreshStatus()` 负责，类型下拉由 `renderPicker()` 负责）。
- 真实浏览器（Chromium）验证：tab 切换恢复正常，自定义下拉展开态、类型色点、描述均正常。

---

## 0.3.0（2026-10-11）

**Web 界面视觉升级**（交互/布局变化，minor）。

- 「通道」页新增通道选择器重做为**自定义下拉组件**：带通道类型色点（企微三通道蓝/靛/青、QQ 红）、展开显示各类型描述、选中态高亮、Esc/点击外部关闭、旋转箭头动画；替代原生 `<select>`。
- 「新增通道」「新增规则」升级为渐变主按钮；通道列表的类型标签前加同色圆点，与选择器呼应。
- 全局样式翻新：顶栏毛玻璃 + 渐变品牌点 + 状态胶囊；标签页渐变下划线；卡片悬停浮起 + 左侧渐变条；表单聚焦光圈；徽标改软色底；步骤列表改数字圆点；空状态虚线框；表格表头大写吸顶 + 行悬停；自定义滚动条；Toast 加阴影。
- 修复：前端版本号从写死的 `v0.1.0` 改为读取 `/api/status` 的 `version` 字段（后端启动时读 `VERSION` 文件）。
- 移动端：选择器与菜单自适应窄屏。

---

## 0.2.1（2026-10-11）

**部署位置调整 + 文档补充**（无功能变更）。

- 部署工程从「版本归档目录」迁到**独立的运行目录**（`docker/<项目名>/`）。
  按约定：运行目录放实际运行的 compose 工程与数据，归档目录只放版本包 `.tar.gz`。
- README 新增「部署位置约定」小节，明确两个目录的职责与备份方式（整包拷 `docker/` 下目录即可，配置与数据都在里面）。
- 迁移后实测：容器 healthy，`/data` 只读挂载正常，发送历史（SQLite）随目录一并迁移无丢失。

---

## 0.2.0（2026-10-11）

**修正 0.1.0 的一个错误结论**：企业微信智能机器人并非「只能发 markdown」。
按 2026/05/25 更新的官方长连接文档，`aibot_send_msg` 已支持 image / file / voice / video，
前提是先用临时素材三步握手拿到 media_id。0.1.0 把它标成不支持图片是错的。

**企业微信智能机器人通道**
- 新增临时素材上传：`aibot_upload_media_init` → `aibot_upload_media_chunk × N`
  → `aibot_upload_media_finish`，单片 512KB、最多 100 片，带 md5 完整性校验
- 新增图片消息（≤10MB，比群机器人的 2MB 宽松）与文件消息（≤20MB）
- markdown 默认上限从 3800 提到 8000（官方硬上限 20480）
- 去掉不再需要的「图片降级策略」配置项
- `ws_url` 可用通道选项或环境变量 `DIR2CHAT_WECOM_BOT_WS` 覆盖，便于自建代理与联调

**文档**
- 更正智能机器人创建权限：超管 + 被授权成员均可，成员权限默认全员开放
  （上限：单企业 300 个，单成员 20 个）
- 补上群聊说明：智能机器人只进得了他所在的企业内部群，互联群 / 上下游群 / 外部群不行
- 能力表改为按「markdown / 图片 / 文件 / 收件人」对比，并给出按场景选通道的建议
- 补素材上传频率限制（30 次/分钟）及发送间隔调参建议

**验证**
- 用模拟长连接服务端跑通：subscribe、45KB md 转文件消息、1.3MB 图拆 3 片上传
  （服务端重组后大小与 md5 均与原始一致）、markdown 消息

---

## 0.1.0（2026-10-11）

首个可用骨架。

**目录监听**
- 定时轮询扫描（默认 60s），不走 inotify —— Docker 卷 / NFS 上 inotify 常常不工作
- 「文件写完」双重判定：`mtime` 距今 ≥ `stable_seconds`（默认 5s）+ 连续两轮 `size` 不变
- 跳过 `.tmp` / `.part` / `.crdownload` 与点开头的隐藏文件
- 包含 / 排除模式，裸写 `png` 自动规整为 `*.png`，大小写不敏感
- 幂等键 `(规则, 通道, 路径, sha256)`：内容不变只发一次，内容变了再发

**通道**
- 企业微信群机器人 Webhook（客户端 v4.1.41 起叫「消息推送」）：文本 / markdown / 图片 / 文件
- 企业微信智能机器人（长连接 `wss://openws.work.weixin.qq.com`）：`aibot_subscribe` +
  `aibot_send_msg`，30s 心跳，指数退避重连；收集回调里的会话 ID 供界面选用
- 企业微信应用消息：corpid + secret + agentid，可发给指定成员，图片 / 文件走临时素材
- QQ 官方机器人：OpenAPI v2，群 / 单聊，图片与文件走富媒体 `file_info` 两段式上传

**降级策略**
- markdown 超限 → 有文件能力就发 `.md` 文件，否则按段落切片并自动加 `(i/n)` 序号
- 图片超限 → JPEG 降质量 → 缩尺寸循环压缩，保留 PNG 透明通道
- 通道不支持图片 → 发文件名 + 路径提示，或整个跳过
- QQ markdown 被拒 → 自动降级为纯文本重发

**Web 界面**
- 概览（状态、统计、通道健康）/ 通道 / 规则 / 记录 四个页签
- 通道字段按类型动态渲染，改动即存，支持一键发测试消息
- 浅色 / 深色主题自适应，移动端可用

**工程**
- Python 3.11 + FastAPI，串行发送队列 + 每通道间隔限流 + 失败退避重试
- SQLite 落发送记录与事件日志，`config.json` 持久化配置
- Dockerfile（slim 镜像 + 健康检查）+ docker-compose，监听目录只读挂载到 `/data`
