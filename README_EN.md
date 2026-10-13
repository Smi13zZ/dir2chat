# dir2chat — Installation & Configuration Guide

English ｜ [**简体中文**](README.md)

> Version: **26.10.16** ｜ In one line: watch a directory and push newly added / changed **images and Markdown files** to WeCom (WeChat Work) or QQ chats on a schedule — with a web UI for configuration and a one-command Docker deploy.

---

## 1. What is this

dir2chat is a long-running Docker service that:

- **Periodically scans** a directory you choose (every 60 seconds by default — adjustable under *Overview → Scan settings*, and each rule can override the interval; it deliberately does **not** use inotify, because Docker volumes / NFS are the common case)
- When it finds a **new file or a content change**, it pushes it to **WeCom** or **QQ** according to the rules you configured
- Automatically adapts content to each channel's limits: oversized images get compressed, over-long Markdown degrades into a file message, and anything longer still gets sliced
- Ships a lightweight web UI (`:8899`) so you can add/remove channels and rules without touching code
- **Per-rule schedules**: each rule can be limited to a time window and pauses itself outside of it
- **Adjustable polling interval**: globally under *Overview → Scan settings*, per rule (set `0` to follow the global value)

The most typical use case: automatically push the report charts and Markdown reports generated every day into a chat group.

### Supported channels and capabilities

| Channel                                    | Text        | Markdown | Image       | File         | Can send to     | Notes                                                                          |
| ------------------------------------------ | ----------- | -------- | ----------- | ------------ | --------------- | ------------------------------------------------------------------------------ |
| WeCom group bot (Webhook)                  | Yes         | Yes      | Yes (≤2MB)  | Yes (5B–20MB) | Internal group  | Simplest — only a URL is needed; file messages carry no filename; 20 msg/min   |
| WeCom smart robot (long connection)        | via Markdown| Yes (≤20KB) | Yes (≤10MB) | Yes (≤20MB)   | Internal group  | Most capable; plain text is carried by markdown; group chat uses `chat_type=2` |
| WeCom app message                          | Yes         | Yes      | Yes         | Yes           | **Named members** | The only channel that can target individuals; needs corpid/secret/agentid    |
| QQ official bot (OpenAPI v2)               | Yes         | Yes      | Yes         | Yes           | Groups / DMs    | Needs appId/secret; proactive group push must be enabled by the group owner     |

> WeChat **personal accounts** have no official outbound sending API and are not supported by this project (that would require running RPA / WeChatFerry on an always-on Windows machine).

---

## 2. Requirements

- **Docker Engine 20.10+** with **docker compose v2** (`docker compose version` produces output)
- **Linux amd64 / arm64** both supported: the official image is multi-arch, so `docker pull` automatically matches your machine
- Access to Docker Hub to pull the image; fully offline machines can build from source instead (see Section 10)
- About **250 MB** of disk space
- LAN-only access: open port **8899** in your firewall (do **not** expose it to the public internet)

---

## 3. Installation

The image is published on Docker Hub: **`smi1ezz/dir2chat`** (supports amd64 / arm64; `:latest` always points to the newest version).

### Method 1: one-click script (recommended)

```bash
git clone https://github.com/Smi13zZ/dir2chat.git
cd dir2chat

# Install (checks Docker, pulls the image, generates config, starts the container)
sudo bash install.sh
```

> **⚠ Always run it with `sudo` (root).** The script touches `/var/run/docker.sock`, pulls the image
> and starts containers; as a normal user you would get
> `permission denied while trying to connect to the Docker daemon socket`.
> If you have added your user to the `docker` group (`sudo usermod -aG docker $USER`, then **log in again**
> or run `newgrp docker`), you can drop the `sudo`.

The script accepts parameterised installs — put the variables before `bash install.sh` (they are all written back into `.env`):

```bash
./install.sh                                  # install in place (default: current directory)
./install.sh /opt/dir2chat                    # explicit install directory (only runtime files are copied)
DIR2CHAT_PORT=8091 ./install.sh               # change the port when 8899 is taken (written to .env, container recreated)
WATCH_DIR=/path/to/watch ./install.sh         # set the watched directory (written to .env, overrides the default)
FORCE=1 ./install.sh                          # directory already deployed: overwrite program files, keep data (config/ and .env)

# They can be combined: set both port and watched directory in one go
sudo DIR2CHAT_PORT=8091 WATCH_DIR=/path/to/watch bash install.sh
```

The script does all of this: parse directory/port/watch dir → deploy files → check Docker → generate `.env` → validate the watched directory → create `config/` → pull the image → start the container → wait for the health check.

> **Windows users**: run `sudo bash install.sh` inside WSL2 or Git Bash; or install Docker Desktop and simply run `docker compose up -d` manually (skipping the script).

### Method 2: manual docker compose (no script)

Put `docker-compose.yml` and `.env.example` from the repo into the same directory, rename `.env.example` to `.env`, fill in `WATCH_DIR`, then:

```bash
sudo docker compose pull      # pull the image (~250MB the first time)
sudo docker compose up -d     # start
```

Or skip compose entirely and start it with a single `docker run`:

```bash
sudo docker run -d --name dir2chat --restart unless-stopped \
  --network bridge -p 8899:8899 \
  -v "$PWD/config:/config" \
  -v /path/to/watch:/data:ro \
  -e DIR2CHAT_CONFIG_DIR=/config -e TZ=Asia/Shanghai \
  smi1ezz/dir2chat:26.10.16
```

### Repository layout

```
dir2chat/
├── install.sh                  # one-click installer (supports directory / port / FORCE)
├── README.md                   # Chinese guide
├── README_EN.md                # this English guide
├── 教程.html                    # illustrated Chinese tutorial (same visual style)
├── CHANGELOG.md                # changelog
├── docker-compose.yml          # service definition (system bridge network, includes CAP_SYS_TIME)
├── Dockerfile                  # image build file (for building from source)
├── requirements.txt            # Python dependencies
├── .env.example                # config sample
├── VERSION                     # version number
├── app/                        # service source code
└── tools/                      # development tools (version bump script)
```

### Installed directory layout (after `./install.sh <dir>`)

The script **only copies the files actually needed at runtime**; documentation and dev tools stay in the
repository directory so the install directory stays clean:

```
<install-dir>/
├── install.sh              # kept so you can run ./install.sh again to upgrade
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── VERSION
├── .env                    # generated on first install (data — never overwritten)
├── app/                    # service source code
└── config/                 # runtime config + SQLite (data — never overwritten)
```

> The image is **not** stored in the install directory — Docker manages it. Upgrading is just
> `docker compose pull`.
> With the default "install in place" mode (`./install.sh` without a directory), the repository directory
> *is* the install directory and no copying happens at all.

---

## 4. Configure the watched directory (required)

Two ways — pick either one.

**Option 1 (recommended): pass the variable at install time**, the script writes it into `.env` for you:

```bash
sudo WATCH_DIR=/path/to/watch bash install.sh
```

**Option 2: edit `.env` by hand after installing**:

```ini
WATCH_DIR=/path/to/watch
DIR2CHAT_PORT=8899
```

> **⚠ Make sure you have root when editing `.env`.** If you installed with `sudo bash install.sh`, both
> `.env` and `config/` are owned by root, and a normal editor will tell you the file is read-only.
> Use `sudo nano .env` (or `sudo vi .env`), then `sudo docker compose up -d`.

> **⚠ One setting per line — never merge two lines.** A very common mistake (it silently disables both settings):
>
> ```
> WATCH_DIR=/path/to/watchDIR2CHAT_PORT=8899
> ```
>
> The line must break after `WATCH_DIR=...`. The install script always writes `.env` one-setting-per-line and
> never appends to the end of the previous line.

This directory is mounted **read-only** into the container at `/data`. dir2chat scans it **recursively**, so
date-based subdirectories such as `share/2026-10-11/` are picked up automatically.

Apply your changes:

```bash
sudo docker compose up -d
```

> Tip: `.env` holds the **host** path; inside the container it is always `/data`. When writing rules, use `/data`.

---

## 5. Web UI configuration (three steps)

Open `http://<machine-ip>:8899` in a browser.

1. **Channels** tab → give it a **name** (e.g. "Investing") → pick a type → "Add channel" → fill in the credentials → click "Test" to confirm connectivity
2. **Rules** tab → "Add rule" → set `watch_dir` to `/data`, `include` to `*.png` + `*.md` → **click** the channel chips below to bind them
3. Back on **Overview**, click "Scan now" and check the "Records" list for successful sends

> Too many similar channels to tell apart? Give each one a custom name — it shows up in the rule bindings and in the send records.

### 5.1 WeCom group bot (Webhook — simplest)

In the WeCom client: **Group settings → Add group bot → copy the Webhook URL**, then paste the whole URL into `webhook_url`.

- Limits: images ≤2MB (dir2chat compresses automatically), files 5B–20MB, **file messages cannot carry a filename**, 20 messages/min
- Internal company groups only; outbound only

### 5.2 WeCom smart robot (long connection — most capable)

Create it under **WeCom admin console → Smart robot** and note the `bot_id` / `secret`. Add the bot to the
target group, then take the `chatid` from the group callback (dir2chat also caches recently seen sessions —
you can pick one under *Channels → Health/Sessions*).

- Group chat: set `chat_type` to `2`
- Capabilities: images ≤10MB, files ≤20MB **with a filename**, Markdown ≤20KB
- **There is no `text` type** — plain text is carried by markdown (an official limitation, not a bug)
- Rate limits: uploads 30/min, messages 30/min per conversation
- Also restricted to **internal company groups**

### 5.3 WeCom app message (can target named members)

Requires a self-built app: `corpid` + `corpsecret` + `agentid`.

- This is the **only channel that can address a specific member** (`touser`)
- Temporary media expires after 3 days

### 5.4 QQ official bot (OpenAPI v2)

Create a bot on the QQ Open Platform and note the `appId` + `clientSecret`.

- Proactive group push requires the **group owner** to enable "let the bot speak in the group proactively" in group settings (fully open since 2026-06-22)
- Group and direct-message upload endpoints are separate; `file_info` is not interchangeable
- Rate limits: 60/min (bot level) when verified, 30 when not; 20/min per group

---

## 6. Per-rule schedules (**controlled independently for each rule**)

Don't want it pushing around the clock? Enable the **⏰ schedule** toggle **inside each rule** — rules never affect each other:

- **When enabled**, the rule only scans and pushes inside its **time window**; **outside the window it pauses automatically** (no files are lost — pushing resumes when the window opens again).
- **Different rules can use different windows**: e.g. a "market charts" rule only from `09:00–15:00`, a "daily report" rule all day.
- **Manual "Scan now" ignores schedules** — you can force a run at any time.
- **Multiple windows** are supported, including ones that **cross midnight**, e.g. `22:00 – 06:00`.
- **Timezone** defaults to `Asia/Shanghai` (requires `tzdata`; falls back to the host local time otherwise).
- Rule cards show their phase live: "✅ currently inside the push window" or "⏸ paused outside the window".
- Stored in `config/config.json` under `rules[].schedule` and preserved across redeploys.

> Note: the old global schedule from 0.4.0 was removed in favour of per-rule schedules, so different
> directories or content types can follow completely different rhythms.

### 6.1 Adjustable polling interval (global + per rule)

- **Global**: *Overview → Scan settings* lets you change the polling interval (seconds, minimum 5), the file stability time, the maximum number of files per round, and a master scan switch. Click "Save scan settings" to apply (stored under `scan.*`).
- **Per rule**: each rule's "own polling interval" — `0` or empty follows the global value; any integer ≥5 makes that rule scan on its own schedule (stored in `rules[].interval_seconds`).
- Different rules can run at different rhythms without interfering. Smaller = more timely, larger = lighter on resources.

### 6.2 Channel binding lists only enabled channels

The binding list on a rule card shows **enabled channels only**. If a channel that was bound later gets
disabled, it stays visible (dashed/grey with an "disabled" tag) so you can unbind it deliberately — no
"invisible but unbindable" dead ends.

### 6.3 Config backup / import

The **Overview** page has **Config backup**:

- **⬇ Backup config**: export all configuration (channel credentials, rules, schedules, scan parameters) as a single JSON file.
- **⬆ Import config**: choose a JSON file to **wholesale replace** the current configuration (with a confirmation step).
- Useful when migrating to a new machine, or restoring after an accidental deletion.

### 6.4 Timezone and time (auto sync / manual set)

The **Overview** page also has a **Timezone & time** section:

- **Live clock** ticking every second in the selected timezone, with an offset badge next to it.
- **Adjustable timezone**: common zones are built in (default `Asia/Shanghai` = UTC+08:00) and any other zoneinfo region is added automatically.
- **Auto sync**: pick a time server (e.g. `ntp.tencent.com`, `time.cloudflare.com`) and click "Sync now"; the backend also re-syncs every `time.auto_sync_hours` (6 by default). "Test server" only queries — it never changes the clock — and shows your offset and network latency.
- **Custom servers**: enter an address under "Custom server" and click "Add" to add it to the dropdown.  
  Domain names / IPv4 / IPv6 / LAN hostnames are all accepted, optionally with a port, e.g. `192.168.1.1`,  
  `ntp.local`, `192.168.1.5:123`, `[fe80::1]:123`. Accidentally pasted `ntp://` or `http://` prefixes are stripped.  
  Added entries appear as chips — click the `×` on a chip to delete it (deleting the active one falls back to the default).
- **Manual set**: switch to "Manual", pick a date and time, then click "Apply time" ("Fill current time" is available first).

> Setting the clock requires `CAP_SYS_TIME`, which is already added in `docker-compose.yml` (`cap_add: [SYS_TIME]`).  
> If the capability is missing, the UI states the reason and the fix — it will not fail silently.  
> Time sync uses a built-in lightweight SNTP client (standard UDP 123 packets) and does **not** depend on
> ntpd / chrony / systemd-timesyncd.

### 6.5 Mobile support

The UI is adapted for phone browsers:

- The status bar at the top is collapsed by default; tap the **📊** icon to expand/collapse it (237px → 77px).
- Larger touch targets: buttons ≥40px, primary actions ≥42px, radio/switch labels ≥40px.
- Forms become single-column and full-width on narrow screens, with 42px inputs and 15px text (avoiding iOS auto-zoom on focus).
- The tab bar scrolls horizontally and sticks to the top on narrow screens; tables scroll horizontally more smoothly and pagination buttons are easier to hit.
- Bottom safe-area insets for full-screen phones are handled, and "sticky" hover styles were removed for touch.

---

## 7. Push rules and behaviour

- **Polling scan**: every 60s by default; a file must be "stable for 5s and the same size across two scans" before it is processed, so half-written files are never sent
- **Idempotent**: the same `(rule, channel, path, sha256)` is sent only once; only changed content is re-sent. So a report that is rewritten daily gets re-sent, while untouched files never spam the chat
- **Image compression**: anything over the channel limit is compressed automatically (webhook 2MB / smart robot 10MB)
- **Markdown degradation**: over 3800 bytes it becomes a file message; over that it is sliced with an `(i/n)` marker
- **WeCom webhook file messages have no filename** (official limitation) — if you need to tell reports apart in the chat, lower the Markdown limit so more images are sent, or use the smart robot channel (files carry filenames)

### 7.1 Scan performance tuning (three adjustable parameters)

> Applies to **directories that accumulate by day** (e.g. a new `2026-10-12/` directory every day holding
> that day's reports) — the total file count keeps growing, and these three parameters make sure
> "no matter how many files, nothing is missed and nothing is re-read every minute".
>
> All three live in **Web UI → Scan settings** and take effect as soon as you click "Save scan settings" —
> no restart needed. (Under the hood it is `PUT /api/config`; you can also edit `config/config.json` in the
> mounted directory and restart the container.)

| Parameter | Default | What it does | How to set it |
|---|---|---|---|
| `max_files_per_round`<br>max files pushed per round | **500** | Upper bound on how many **pending** files are queued in a single round, so one burst cannot flood a channel | **Files already pushed do not count against this quota**, so you rarely need to raise it. Set it above your **daily increment** — 30 files/day is fine with the default 500 |
| `digest_cache_max_entries`<br>content-hash cache entries | **5000** | Remembers each file's "size + mtime + hash"; if all three are unchanged on the next scan it is **reused** instead of reading and re-hashing the file | Roughly 300 bytes/entry, so 5000 entries is only ~2MB. Estimate days covered from your daily increment (30/day → about 166 days). Set **0 = disable the cache** (back to full re-hashing every round; only for troubleshooting) |
| `skip_dirs_idle_days`<br>idle-directory cooldown (days) | **0 (off)** | If a subdirectory has had no change for N days, the **whole subtree is skipped** and the stat+hash cost of historical snapshots drops to zero | Suits "one date directory per day, old directories never change" — set **2** to stop scanning directories that have been quiet for two days.<br>**⚠ Side effect: skipped directories take part in no scanning at all** — neither failed sends inside them nor re-pushes after "clear send records" will happen. Set it back to 0 when you need to re-push |

> **Important**: these three parameters affect **performance only**, never correctness. Deduplication is
> always decided by the `(rule, channel, path, sha256)` tuple; a cache miss merely recomputes a hash and
> **never** causes duplicate or missed pushes.

### 7.2 Recursion and filtering (recursive / include / exclude)

These three fields on a rule decide "which files are seen":

- **Recursive `recursive`**
  - **Yes (default)**: every level of subdirectory under `watch_dir` is scanned.
  - **No**: only the top level of `watch_dir` is scanned; subdirectories are skipped entirely.
  - ⚠️ If your files live in date subdirectories (e.g. `share/2026-10-12/xxx.png`) this **must stay on**; turning it off yields "0 files matched", because the root itself holds no files.

- **Include `include`**: only matching files are pushed. Case-insensitive, wildcards supported.
  - e.g. `*.png`, `*.md`, `report_*.pdf`
  - Shortcut: a bare `png` is treated as `*.png`; leaving it empty equals `*` (everything).

- **Exclude `exclude`**: a match is skipped, and it takes priority over include.
  - **It also applies to directory names, pruning the whole subtree**: e.g. `test-verify` excludes the entire `test-verify/` directory no matter how deeply nested.
  - Commonly used for temporary or noisy directories; hidden directories starting with `.` are never scanned.
  - Also, files ending in `.tmp` / `.part` / `.crdownload` are always skipped (so downloads or in-progress writes are not picked up), and zero-byte files are ignored as well.

---

## 8. Records page

- **Two side-by-side tabs**: "Send records" and "Event log" — whichever you click is shown.
- Fixed table width, long content is **truncated with an ellipsis** and the **full value appears on hover**; scroll **horizontally** when there are many columns.
- **Filters**: time (fuzzy match, e.g. `2026-10` or `17:2`), channel, type, level (event log); send records add "failures/skips only".
- **Pagination**: exactly **15 rows per page**, longer results paginate and **never scroll vertically**; short pages are padded with blank rows, so page height is constant and never jumps.
- **Click the whitespace to page**: clicking any blank area below the table goes to the next page (and back to the previous one on the last page); clicking the table or buttons themselves does not trigger it.
- **⬇ Export**: export the current filtered result as CSV (with a BOM, so Chinese text is not garbled in Excel).

> The content area **fills the full window width** (no centred narrowing), so on a wide screen you can scroll anywhere — there are no dead margins on either side.

---

## 9. Day-to-day management

> The commands below all assume `sudo`. If your user is in the `docker` group, drop it.

```bash
# Status
curl http://<IP>:8899/api/status
# or check the "Overview" page

# Back up config (export JSON)
curl -OJ http://<IP>:8899/api/config/export

# Stop
sudo docker compose down

# Restart
sudo docker compose restart

# Logs
sudo docker compose logs -f

# Upgrade to the latest image
sudo docker compose pull && sudo docker compose up -d

# Edit config (.env is owned by root when installed with sudo — sudo is required to save)
sudo nano .env

# Uninstall
sudo docker compose down && rm -rf config .env
```

---

## 10. Building from source (optional)

Normally the official image from Docker Hub is all you need — **no build required**. Build it yourself
only in these two cases: ① you changed the source / `Dockerfile` / `requirements.txt`; ② your architecture
is unusual and the official image will not run. Building requires pulling the base image `python:3.11-slim`.

```bash
# Build in the repository directory (tagged the same as in compose) and recreate the container
sudo docker compose build
sudo docker compose up -d --force-recreate
```

> **⚠ Changed the source or dependencies but nothing happened? It is almost always stale build cache.**
> Docker caches layer by layer: as long as `requirements.txt` is unchanged the `pip install` layer is
> reused and your new code never enters the image.

```bash
# 1) Ignore the cache and rebuild from scratch (most common)
sudo docker compose build --no-cache
sudo docker compose up -d --force-recreate

# 2) Clear the BuildKit build cache (-a includes unused entries; -f skips confirmation)
sudo docker builder prune            # interactive confirmation
sudo docker builder prune -af        # clear everything

# 3) Remove dangling images (leftover <none> layers from builds)
sudo docker image prune -f

# 4) See how much each category uses before deciding what to clear
sudo docker system df

# 5) Nuclear option (careful: removes all unused images / caches / networks)
sudo docker system prune -af
```

> Clearing the build cache only affects the speed of the next build (dependencies are re-downloaded) and
> **never touches the data in `config/`** — channels, rules and send history all live in that volume.

---

## 11. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Port already in use | Change the left-hand port in `docker-compose.yml`, e.g. `9090:8899` |
| Architecture mismatch | The official image already covers amd64 / arm64; for anything else build from source with `docker compose build` |
| No files detected | Check that `WATCH_DIR` in `.env` exists, that `/data` is actually mounted into the container, and that the rule's `include` wildcard is right |
| Channel "Test" fails | Read the returned error code (the UI explains common errcodes such as 45009 rate limit, 93000 bad parameter) |
| A new channel is missing when binding | Click "↻ Refresh channels" at the top right of the Rules page |
| Image pull is slow / times out | Configure a registry mirror (`registry-mirrors` in `daemon.json`), then retry `docker compose pull` |
| Schedule not taking effect | Confirm **that rule's** schedule toggle is enabled and its window is non-empty; use "Scan now" to force a push and validate the channel |
| Changed polling interval but nothing changed | You must click "Save scan settings" on the Overview page; a rule-level interval overrides the global one |
| A channel is missing inside a rule | That channel is disabled. Enable it on the Channels page, or click "↻ Refresh channels" |
| Setting time / syncing fails | The container lacks `CAP_SYS_TIME`. Make sure `docker-compose.yml` has `cap_add: [SYS_TIME]`, then `docker compose up -d --force-recreate` |
| Time server unreachable | Container DNS or outbound access is restricted. Click "Test server" to see the actual error; try `ntp.aliyun.com` or `cn.pool.ntp.org` |
| Time did not change after switching timezone | Timezone only changes the display/decision basis, not the system clock. Configure sync or set the time manually |
| Top bar too tall on mobile | Tap the **📊** icon in the top bar to collapse/expand the status area |
| Whitespace next to the table does not page | The content area is one full-width scroller, so any blank spot (including next to the scrollbar) pages. If you still see old styling, hard-refresh (Ctrl/Cmd+Shift+R) |
| Content too wide / too narrow | Top bar, tab bar and content share one width variable (max 1543px, centred); above 1600px the cap is lifted |
| Adding a custom time server fails | Check for illegal characters, a port outside 1–65535, or a length over 255; the UI states the exact reason |
| NTP sync fails | First confirm reachability with "Test server"; for LAN servers make sure UDP 123 is not blocked |
| Timestamps show the wrong zone | Check the timezone dropdown on the Overview page; switching it re-renders record times |

---

## 12. Version and upgrade

- Current version: **26.10.16** (see `VERSION`; the image tag matches it, and `latest` always points to the newest release).
- **Upgrading**: pull the new image and recreate the container — your configuration and data in `config/` are preserved.

```bash
sudo docker compose pull
sudo docker compose up -d
```

- To pin a version, change `image:` in `docker-compose.yml` to `smi1ezz/dir2chat:<version>`.
- Historic versions and changes are listed under [Releases](https://github.com/Smi13zZ/dir2chat/releases) and in `CHANGELOG.md`.
