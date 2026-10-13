/* dir2chat Web UI —— 原生 JS，无构建步骤 */

const state = {
  types: [],
  channels: [],
  rules: [],
  status: null,
  logView: "history",     // history | events
  logPage: { history: 1, events: 1 },
  logData: { history: [], events: [] },
  logFilters: { time: "", channel: "", kind: "", level: "", onlyFail: false },
};
const PAGE_SIZE = 15;
const LOG_ROWS = 15;      // 记录表每页固定行数（高度按此固定，超出靠翻页而非滚动）
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}

function toast(msg, ok = true) {
  const el = $("#toast");
  el.textContent = msg;
  el.style.background = ok ? "var(--text)" : "var(--err)";
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2600);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function downloadBlob(content, filename, mime = "application/json") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---------------- 悬停提示（显示被省略的完整内容） ---------------- */

function bindTooltips(root) {
  const tip = $("#tooltip");
  root.querySelectorAll("[data-full]").forEach(el => {
    if (el._tipBound) return;
    el._tipBound = true;
    el.addEventListener("mouseenter", () => {
      const full = el.getAttribute("data-full");
      if (!full || full === "—") return;
      tip.textContent = full;
      tip.style.display = "block";
      const r = el.getBoundingClientRect();
      const tw = Math.min(460, window.innerWidth - 24);
      tip.style.maxWidth = tw + "px";
      let left = Math.min(r.left, window.innerWidth - tw - 12);
      tip.style.left = Math.max(12, left) + "px";
      tip.style.top = (r.bottom + 8) + "px";
      requestAnimationFrame(() => tip.classList.add("show"));
    });
    el.addEventListener("mouseleave", () => {
      tip.classList.remove("show");
      setTimeout(() => { if (!tip.classList.contains("show")) tip.style.display = "none"; }, 130);
    });
  });
}

/* ---------------- 状态栏与概览 ---------------- */

async function refreshStatus() {
  const s = await api("/api/status");
  state.status = s;
  const last = s.scan.last_scan_at
    ? new Date(s.scan.last_scan_at * 1000).toLocaleTimeString() : "尚未扫描";
  const timed = s.rules.filter(r => r.schedule && r.schedule.enabled).length;
  const paused = s.rules.filter(r => r.schedule && r.schedule.enabled && !r.schedule.active).length;
  $("#status").innerHTML = `
    <span>运行 <b>${Math.floor(s.uptime_seconds / 60)} 分钟</b></span>
    <span>扫描间隔 <b>${s.scan.interval_seconds}s</b>${s.scan.enabled ? "" : "（已暂停）"}</span>
    <span>定时规则 <b>${timed - paused}/${timed}</b>${timed ? (paused ? " 窗口内" : "") : "（无）"}</span>
    <span>上次扫描 <b>${last}</b>（命中 ${s.scan.last_found}）</span>
    <span>队列 <b>${s.queue}</b></span>
    <span>今日成功 <b>${s.stats.today}</b></span>
    <span>失败 <b>${s.stats.fail}</b></span>`;
  if (s.version) $("#ver").textContent = "v" + s.version;

  $("#overview-cards").innerHTML = [
    ["通道", `${s.channels.filter(c => c.enabled && c.live).length}/${s.channels.length}`],
    ["规则", s.rules.filter(r => r.enabled).length + "/" + s.rules.length],
    ["今日已发", s.stats.today],
    ["累计成功", s.stats.ok],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  if (!s.channels.length) {
    $("#overview-channels").innerHTML = `<div class="empty">还没有通道，去「通道」页添加一个。</div>`;
    return;
  }
  const rows = [];
  for (const c of s.channels) {
    let health = { ready: false, detail: "未启用" };
    if (c.enabled) health = await api(`/api/channels/${c.id}/health`).catch(() => health);
    rows.push(`<div class="item"><div class="item-head">
      <span class="item-title">${esc(c.name)}</span>
      <span class="item-type">${esc(c.type)}</span>
      <span class="badge ${c.enabled ? (health.ready ? "ok" : "warn") : ""}">${
        c.enabled ? (health.ready ? "就绪" : "未就绪") : "已停用"}</span>
      <span class="help">${esc(health.detail || "")}${health.last_error ? " · " + esc(health.last_error) : ""}</span>
    </div></div>`);
  }
  $("#overview-channels").innerHTML = rows.join("");
}

/* ---------------- 扫描设置（全局轮询等） ---------------- */

async function loadScanSettings() {
  const cfg = await api("/api/config");
  const s = cfg.scan || {};
  $("#scan-interval").value = s.interval_seconds ?? 60;
  $("#scan-stable").value = s.stable_seconds ?? 5;
  $("#scan-maxfiles").value = s.max_files_per_round ?? 500;
  $("#scan-digestcache").value = s.digest_cache_max_entries ?? 5000;
  $("#scan-idledays").value = s.skip_dirs_idle_days ?? 0;
  $("#scan-enabled").value = String(s.enabled !== false);
}

async function saveScanSettings() {
  const interval = Math.max(5, Number($("#scan-interval").value) || 60);
  const stable = Math.max(0, Number($("#scan-stable").value) || 0);
  const maxfiles = Math.max(1, Number($("#scan-maxfiles").value) || 500);
  const digestcache = Math.max(0, Number($("#scan-digestcache").value) || 0);
  const idledays = Math.max(0, Number($("#scan-idledays").value) || 0);
  const enabled = $("#scan-enabled").value === "true";
  try {
    await api("/api/config", "PUT", {
      scan: {
        interval_seconds: interval,
        stable_seconds: stable,
        max_files_per_round: maxfiles,
        digest_cache_max_entries: digestcache,
        skip_dirs_idle_days: idledays,
        enabled,
      },
    });
    toast(`扫描设置已保存（间隔 ${interval}s）`);
    await refreshStatus();
    await loadScanSettings();
  } catch (e) { toast(e.message, false); }
}

/* ---------------- 时区与时间 ---------------- */

const timeState = {
  zonesLoaded: false,
  status: null,
  config: { mode: "auto", timezone: "", ntp_server: "" },
  servers: [],
  tickTimer: null,
};

/* 本地时钟：每秒跳动，不需要反复请求后端 */
function startClock() {
  if (timeState.tickTimer) clearInterval(timeState.tickTimer);
  const tick = () => {
    const el = $("#time-now");
    if (!el) return;
    try {
      const tz = timeState.config.timezone || undefined;
      const now = new Date();
      const t = now.toLocaleTimeString("zh-CN", { hour12: false, timeZone: tz || undefined });
      const d = now.toLocaleDateString("zh-CN", {
        year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
        timeZone: tz || undefined,
      });
      el.textContent = t;
      const dateEl = $("#time-date");
      if (dateEl) dateEl.textContent = d + " · 每天 24 小时制";
    } catch { /* 时区名不被浏览器识别时忽略 */ }
  };
  tick();
  timeState.tickTimer = setInterval(tick, 1000);
}

async function loadTimeSettings() {
  const [status, zones] = await Promise.all([
    api("/api/time"),
    api("/api/time/zones"),
  ]);
  timeState.status = status;

  const cfg = await api("/api/config");
  const tcfg = cfg.time || {};
  timeState.config.mode = tcfg.mode || "auto";
  timeState.config.timezone = tcfg.timezone || status.timezone || "Asia/Shanghai";
  timeState.config.ntp_server = tcfg.ntp_server || "ntp.tencent.com";

  // 时区下拉
  const tzSel = $("#time-timezone");
  const curTz = timeState.config.timezone;
  const zoneList = zones.zones || [];
  if (!zoneList.some(z => z.value === curTz)) {
    zoneList.unshift({ value: curTz, label: curTz });
  }
  tzSel.innerHTML = zoneList.map(z =>
    `<option value="${esc(z.value)}" ${z.value === curTz ? "selected" : ""}>${esc(z.label)}</option>`).join("");

  // NTP 服务器下拉（内置 + 自定义）
  timeState.servers = zones.servers || [];
  renderServerSelect();

  // 模式单选
  $("#time-mode-auto").checked = timeState.config.mode === "auto";
  $("#time-mode-manual").checked = timeState.config.mode !== "auto";
  applyTimeMode();

  // 徽标与说明
  $("#time-tz-badge").textContent = (status.offset ? status.offset + " " : "") + (status.abbr || "");
  const tzHelp = $("#time-tz-help");
  if (status.zoneinfo_root) {
    tzHelp.textContent = `选择所在时区，影响定时窗口的判断与记录时间显示（共 ${zoneList.length} 个可选）`;
  } else {
    tzHelp.textContent = "运行环境缺少 /usr/share/zoneinfo，时区切换可能受限";
  }

  // 无 CAP_SYS_TIME 时给出明确提示，避免用户以为坏了
  const warn = $("#time-cap-warn");
  if (!status.can_set_time) {
    warn.style.display = "";
    warn.innerHTML = `⚠️ 当前容器没有 <b>CAP_SYS_TIME</b> 权限，<b>改系统时间会失败</b>（时区切换与 NTP 查询不受影响）。`
      + `如果确实需要改时间，请在 docker-compose.yml 里给 dir2chat 服务加 <code>cap_add: [SYS_TIME]</code> 后重建容器。`;
  } else {
    warn.style.display = "none";
  }

  if (!timeState.zonesLoaded) {
    timeState.zonesLoaded = true;
    bindTimeControls();
  }
  startClock();
}

function applyTimeMode() {
  const auto = $("#time-mode-auto").checked;
  $("#time-auto-body").style.display = auto ? "" : "none";
  $("#time-manual-body").style.display = auto ? "none" : "";
}

/* 渲染时间服务器下拉：内置项 + 用户自定义项（自定义项带「自定义」标记） */
function renderServerSelect() {
  const svSel = $("#time-ntp-server");
  if (!svSel) return;
  const list = timeState.servers.slice();
  const cur = timeState.config.ntp_server;
  if (cur && !list.some(s => s.value === cur)) {
    list.unshift({ value: cur, label: cur, custom: true });
  }
  svSel.innerHTML = list.map(s =>
    `<option value="${esc(s.value)}" ${s.value === cur ? "selected" : ""}>${esc(s.label)}</option>`
  ).join("") || `<option value="">（无可用服务器）</option>`;
  renderCustomServers();
}

/* 自定义服务器列表：内侧显示，可逐个删除 */
function renderCustomServers() {
  const box = $("#ntp-custom-list");
  if (!box) return;
  const customs = timeState.servers.filter(s => s.custom);
  if (!customs.length) {
    box.innerHTML = `<span class="cs-empty">还没有自定义服务器</span>`;
    return;
  }
  box.innerHTML = customs.map(s =>
    `<span class="cs-chip" data-host="${esc(s.value)}">`
    + `<span class="cs-chip-name">${esc(s.value)}</span>`
    + `<button class="cs-del" data-del="${esc(s.value)}" title="删除" aria-label="删除 ${esc(s.value)}">×</button>`
    + `</span>`).join("");
}

function bindTimeControls() {
  $$('input[name="time-mode"]').forEach(r => {
    r.addEventListener("change", applyTimeMode);
  });

  $("#time-timezone").addEventListener("change", async (ev) => {
    const tz = ev.target.value;
    try {
      const r = await api("/api/time/timezone", "PUT", { timezone: tz });
      timeState.config.timezone = tz;
      timeState.status = r.status;
      $("#time-tz-badge").textContent = (r.status.offset || "") + " " + (r.status.abbr || "");
      toast(`时区已切换为 ${tz}`);
      startClock();
    } catch (e) { toast(e.message, false); }
  });

  $("#btn-ntp-test").addEventListener("click", async () => {
    const server = $("#time-ntp-server").value;
    const box = $("#ntp-result");
    if (!server) { toast("请先选择或添加一个时间服务器", false); return; }
    box.textContent = "测试中…";
    try {
      const r = await api("/api/time/ntp/test", "POST", { server });
      if (r.ok) {
        const off = r.offset_seconds;
        box.textContent = `✅ ${server} 可用 · 本机偏差 ${off > 0 ? "+" : ""}${off}s · 延迟 ${r.rtt_seconds}s`;
      } else {
        box.textContent = `❌ ${r.reason}`;
      }
    } catch (e) { box.textContent = "❌ " + e.message; }
  });

  $("#btn-ntp-sync").addEventListener("click", async () => {
    const server = $("#time-ntp-server").value;
    const box = $("#ntp-result");
    if (!server) { toast("请先选择或添加一个时间服务器", false); return; }
    box.textContent = "同步中…";
    try {
      const r = await api("/api/time/ntp/sync", "POST", { server });
      if (r.ok) {
        box.textContent = `✅ 已与 ${server} 同步（调整 ${r.adjusted_seconds}s）`;
        toast("时间已同步");
      } else {
        box.textContent = `❌ ${r.reason}`;
        toast(r.reason, false);
      }
      if (r.status) timeState.status = r.status;
    } catch (e) { box.textContent = "❌ " + e.message; }
  });

  // 添加自定义时间服务器（支持局域网 IP / 主机名）
  const addCustom = async () => {
    const input = $("#ntp-custom-input");
    const msg = $("#ntp-custom-msg");
    const host = (input.value || "").trim();
    if (!host) { toast("请输入服务器地址", false); return; }
    msg.textContent = "添加中…";
    try {
      const r = await api("/api/time/servers", "POST", { host });
      timeState.servers = r.servers || timeState.servers;
      if (r.builtin) {
        timeState.config.ntp_server = host;
        msg.textContent = "该地址已在内置列表中，已直接选中";
      } else if (r.duplicated) {
        msg.textContent = "该服务器已存在";
      } else {
        timeState.config.ntp_server = host;
        msg.textContent = `✅ 已添加 ${host}`;
      }
      renderServerSelect();
      input.value = "";
    } catch (e) {
      msg.textContent = "❌ " + e.message;
      toast(e.message, false);
    }
  };
  $("#btn-ntp-add").addEventListener("click", addCustom);
  $("#ntp-custom-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); addCustom(); }
  });

  // 删除自定义服务器（事件委托）
  $("#ntp-custom-list").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-del]");
    if (!btn) return;
    const host = btn.dataset.del;
    const msg = $("#ntp-custom-msg");
    try {
      const r = await api("/api/time/servers/delete", "POST", { host });
      timeState.servers = r.servers || [];
      if (timeState.config.ntp_server === host) {
        timeState.config.ntp_server = "ntp.tencent.com";
      }
      renderServerSelect();
      msg.textContent = `已删除 ${host}`;
    } catch (e) { msg.textContent = "❌ " + e.message; }
  });

  $("#btn-manual-now").addEventListener("click", () => {
    const tz = timeState.config.timezone || undefined;
    const now = new Date();
    const p = (n) => String(n).padStart(2, "0");
    // 用所选时区拼出 datetime-local 需要的 "YYYY-MM-DDTHH:MM:SS"
    let s;
    try {
      s = now.toLocaleString("sv-SE", { timeZone: tz }).replace(" ", "T");
    } catch {
      s = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`
        + `T${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
    }
    $("#time-manual-input").value = s;
  });

  $("#btn-manual-apply").addEventListener("click", async () => {
    const val = $("#time-manual-input").value;
    const box = $("#manual-result");
    if (!val) { toast("请先填写时间", false); return; }
    const time = val.replace("T", " ");
    box.textContent = "应用中…";
    try {
      const r = await api("/api/time/manual", "POST", { time });
      if (r.ok) {
        box.textContent = `✅ 已设置为 ${r.now}`;
        toast("系统时间已更新");
      } else {
        box.textContent = `❌ ${r.reason}`;
        toast(r.reason, false);
      }
      if (r.status) timeState.status = r.status;
    } catch (e) { box.textContent = "❌ " + e.message; }
  });
}

async function saveTimeSettings() {
  const mode = $("#time-mode-manual").checked ? "manual" : "auto";
  const timezone = $("#time-timezone").value;
  const ntp_server = $("#time-ntp-server").value;
  try {
    const r = await api("/api/time/config", "PUT", { mode, timezone, ntp_server });
    timeState.config.mode = mode;
    timeState.config.timezone = timezone;
    timeState.config.ntp_server = ntp_server;
    timeState.status = r.status;
    startClock();
    toast(`时间设置已保存（${mode === "auto" ? "自动同步" : "手动设置"}）`);
  } catch (e) { toast(e.message, false); }
}

/* ---------------- 配置备份 / 导入 ---------------- */

async function exportConfig() {
  try {
    const cfg = await api("/api/config");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    downloadBlob(JSON.stringify(cfg, null, 2), `dir2chat-config-${stamp}.json`);
    toast("配置已导出");
  } catch (e) { toast(e.message, false); }
}

async function importConfig(file) {
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    if (!confirm("导入会整体覆盖当前的通道、规则与扫描设置，确定继续？")) return;
    await api("/api/config/import", "POST", { config: data });
    toast("配置已导入，正在刷新…");
    await init();
  } catch (e) { toast("导入失败: " + e.message, false); }
}

/* ---------------- 通道 ---------------- */

/* 通道类型展示元数据：色点用于选择器与列表标识 */
const TYPE_META = {
  wecom_webhook: { color: "#3b82f6" },
  wecom_bot:     { color: "#6366f1" },
  wecom_app:     { color: "#0ea5e9" },
  qq_bot:        { color: "#e2231a" },
};
let newChannelType = "";

function typeColor(type) {
  return (TYPE_META[type] || {}).color || "var(--muted)";
}

function renderPicker() {
  if (!state.types.length) return;
  if (!state.types.some(t => t.type === newChannelType)) {
    newChannelType = state.types[0].type;
  }
  const cur = state.types.find(t => t.type === newChannelType);
  $("#picker-dot").style.background = typeColor(cur.type);
  $("#picker-dot").style.boxShadow = `0 0 0 3px color-mix(in srgb, ${typeColor(cur.type)} 22%, transparent)`;
  $("#picker-label").textContent = cur.label;
  $("#picker-menu").innerHTML = state.types.map(t => `
    <button type="button" role="option" aria-selected="${t.type === newChannelType}"
      class="picker-option ${t.type === newChannelType ? "selected" : ""}" data-type="${t.type}">
      <span class="picker-dot" style="background:${typeColor(t.type)};box-shadow:0 0 0 3px color-mix(in srgb, ${typeColor(t.type)} 22%, transparent)"></span>
      <span class="picker-option-text"><b>${esc(t.label)}</b><i>${esc(t.desc || "")}</i></span>
    </button>`).join("");
}

function bindPicker() {
  const picker = $("#type-picker");
  $("#picker-btn").addEventListener("click", (ev) => {
    ev.stopPropagation();
    picker.classList.toggle("open");
  });
  $("#picker-menu").addEventListener("click", (ev) => {
    const opt = ev.target.closest(".picker-option");
    if (!opt) return;
    newChannelType = opt.dataset.type;
    renderPicker();
    picker.classList.remove("open");
  });
  document.addEventListener("click", () => picker.classList.remove("open"));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") picker.classList.remove("open");
  });
}

function fieldHtml(f, value) {
  const v = value ?? "";
  if (f.type === "select") {
    const opts = f.choices.map(c =>
      `<option value="${c.value}" ${String(c.value) === String(v) ? "selected" : ""}>${esc(c.label)}</option>`).join("");
    return `<div class="field"><label>${esc(f.label)}</label><select data-key="${f.key}">${opts}</select>
      <div class="help">${esc(f.help || "")}</div></div>`;
  }
  if (f.type === "bool") {
    return `<div class="field"><label>${esc(f.label)}</label>
      <select data-key="${f.key}"><option value="true" ${v === true || v === "true" ? "selected" : ""}>是</option>
      <option value="false" ${!(v === true || v === "true") ? "selected" : ""}>否</option></select>
      <div class="help">${esc(f.help || "")}</div></div>`;
  }
  const type = f.type === "int" ? "number" : (f.type === "secret" ? "password" : "text");
  return `<div class="field"><label>${esc(f.label)}</label>
    <input type="${type}" data-key="${f.key}" value="${esc(v)}" placeholder="${esc(f.placeholder || "")}">
    <div class="help">${esc(f.help || "")}</div></div>`;
}

async function renderChannels() {
  const cfg = await api("/api/config");
  state.channels = cfg.channels;
  const host = $("#channel-list");
  if (!cfg.channels.length) {
    host.innerHTML = `<div class="empty">还没有通道。填写名称、选择类型后点「新增通道」。</div>`;
    return;
  }
  host.innerHTML = cfg.channels.map((ch) => {
    const spec = state.types.find(t => t.type === ch.type) || { fields: [], caps: {} };
    const caps = Object.entries(spec.caps || {})
      .filter(([, v]) => v).map(([k]) => k).join(" / ");
    return `<div class="item" data-id="${ch.id}">
      <div class="item-head">
        <span class="item-title">${esc(ch.name)}</span>
        <span class="item-type"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${typeColor(ch.type)};margin-right:5px;vertical-align:1px"></span>${esc(spec.label || ch.type)}</span>
        <span class="badge">${esc(caps)}</span>
        <div class="item-actions">
          <label class="switch-inline"><input type="checkbox" data-act="enable" ${ch.enabled ? "checked" : ""}> 启用</label>
          <button class="btn sm" data-act="test">测试</button>
          ${ch.type === "wecom_bot" ? '<button class="btn sm" data-act="chats">会话 ID</button>' : ""}
          <button class="btn sm danger" data-act="del">删除</button>
        </div>
      </div>
      <p class="item-desc">${esc(spec.desc || "")}</p>
      <div class="grid">
        <div class="field"><label>通道名称</label>
          <input data-name value="${esc(ch.name)}" placeholder="给这个通道起个好记的名字">
          <div class="help">会显示在规则绑定和记录里，建议填清楚用途</div></div>
        ${spec.fields.map(f => fieldHtml(f, ch.options[f.key])).join("")}
      </div>
    </div>`;
  }).join("");

  host.querySelectorAll(".item").forEach(el => bindChannel(el));
}

function bindChannel(el) {
  const id = el.dataset.id;
  const collect = () => {
    const ch = state.channels.find(c => c.id === id);
    const patch = { options: {} };
    el.querySelectorAll("[data-key]").forEach(inp => {
      const key = inp.dataset.key;
      const spec = (state.types.find(t => t.type === ch.type) || {}).fields || [];
      const f = spec.find(s => s.key === key);
      let v = inp.value;
      if (f && f.type === "int") v = Number(v);
      if (f && f.type === "bool") v = v === "true";
      patch.options[key] = v;
    });
    return patch;
  };

  const nameInput = el.querySelector("[data-name]");
  if (nameInput) {
    nameInput.addEventListener("change", async () => {
      try {
        await api(`/api/channels/${id}`, "PUT", { name: nameInput.value.trim() || "未命名通道" });
        state.channels = (await api("/api/config")).channels;
        toast("通道名称已保存");
      } catch (e) { toast(e.message, false); }
    });
  }

  el.querySelectorAll("input[data-key], select[data-key]").forEach(inp => {
    inp.addEventListener("change", async () => {
      try {
        await api(`/api/channels/${id}`, "PUT", collect());
        state.channels = (await api("/api/config")).channels;
        toast("已保存");
      } catch (e) { toast(e.message, false); }
    });
  });

  el.querySelector('[data-act="enable"]').addEventListener("change", async (ev) => {
    await api(`/api/channels/${id}`, "PUT", { enabled: ev.target.checked });
    state.channels = (await api("/api/config")).channels;
    toast(ev.target.checked ? "通道已启用" : "通道已停用");
    refreshStatus();
  });

  el.querySelector('[data-act="test"]').addEventListener("click", async () => {
    try {
      const r = await api(`/api/channels/${id}/test`, "POST", {});
      toast(r.detail || "测试消息已发出");
    } catch (e) { toast(e.message, false); }
  });

  const chatBtn = el.querySelector('[data-act="chats"]');
  if (chatBtn) {
    chatBtn.addEventListener("click", async () => {
      const chats = await api(`/api/channels/${id}/chats`);
      if (!chats.length) {
        toast("还没有收到消息回调，先在企微里给机器人发一条消息");
      } else {
        const lines = chats.map(c => `${c.chattype || "?"}  ${c.chatid}`).join("\n");
        prompt("复制会话 ID 填到 chatid（单聊填 userid）：", lines.split("\n")[0].split("  ").pop());
      }
    });
  }

  el.querySelector('[data-act="del"]').addEventListener("click", async () => {
    if (!confirm("删除这个通道？相关规则里的绑定也会移除。")) return;
    await api(`/api/channels/${id}`, "DELETE");
    renderChannels(); refreshStatus();
  });
}

/* ---------------- 规则 ---------------- */

async function renderRules() {
  const cfg = await api("/api/config");
  state.rules = cfg.rules;
  const host = $("#rule-list");
  if (!cfg.rules.length) {
    host.innerHTML = `<div class="empty">还没有规则。点「新增规则」创建一条。</div>`;
    return;
  }
  const chMap = {};
  cfg.channels.forEach(c => { chMap[c.id] = c; });
  // 只展示「已启用」的通道作为可选项；已绑定但已停用的通道仍保留显示（标灰 + 标注），
  // 否则用户会看不到自己绑定的通道、也无法取消绑定。
  const enabledChannels = cfg.channels.filter(c => c.enabled);

  host.innerHTML = cfg.rules.map(r => {
    const boundNames = (r.channels || []).map(cid => (chMap[cid] || {}).name || cid);
    const boundHtml = boundNames.length
      ? boundNames.map(n => `<b>${esc(n)}</b>`).join("、")
      : "（未绑定任何通道）";

    const bindable = enabledChannels.slice();
    (r.channels || []).forEach(cid => {
      const c = chMap[cid];
      if (c && !c.enabled && !bindable.some(x => x.id === cid)) bindable.push(c);
    });
    const hiddenDisabled = cfg.channels.filter(c => !c.enabled && !(r.channels || []).includes(c.id)).length;
    const chips = bindable.length ? bindable.map(c => {
      const on = (r.channels || []).includes(c.id);
      const off = !c.enabled;
      return `<label class="chan-chip ${on ? "on" : ""} ${off ? "off" : ""}">
        <input type="checkbox" data-chan value="${c.id}" ${on ? "checked" : ""}>
        <span class="dot" style="background:${off ? "var(--muted)" : typeColor(c.type)}"></span>${esc(c.name)}${off ? '<span class="chip-tag">已停用</span>' : ""}</label>`;
    }).join("") : `<span class="help">还没有已启用的通道，请先到「通道」页启用至少一个。</span>`;
    const hiddenNote = hiddenDisabled
      ? `<div class="help" style="margin-top:6px">已隐藏 ${hiddenDisabled} 个未启用的通道</div>`
      : "";

    const sched = r.schedule || { enabled: false, windows: [] };
    const ownInterval = Number(r.interval_seconds || 0);
    const intervalHtml = `<div class="field"><label>独立轮询间隔（秒）</label>
      <input type="number" min="0" data-key="interval_seconds" value="${ownInterval || 0}"
        placeholder="0 = 跟随全局">
      <div class="help">填 0 或留空表示跟随概览里的全局间隔（${esc(globalInterval())}s）；填入 ≥5 的整数则这条规则单独按该间隔扫描</div></div>`;
    const windowsHtml = (sched.windows || []).map((w, i) => `
      <div class="window-row" data-widx="${i}">
        <input type="time" class="win-start" value="${esc(w.start || "09:00")}">
        <span class="win-sep">至</span>
        <input type="time" class="win-end" value="${esc(w.end || "22:00")}">
        <button class="btn sm danger win-del" type="button">删除</button>
      </div>`).join("");
    const schedPhase = r.enabled && sched.enabled
      ? (windowActiveNow(sched)
          ? '<span class="phase on" style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:20px;font-size:12px;color:var(--ok);background:var(--ok-soft)">✅ 当前在推送窗口内</span>'
          : '<span class="phase off" style="display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:20px;font-size:12px;color:var(--muted);background:var(--bg-soft)">⏸ 窗口外暂停</span>')
      : "";

    return `<div class="item" data-id="${r.id}">
      <div class="item-head">
        <span class="item-title">${esc(r.name)}</span>
        <span class="item-type">${esc(r.watch_dir)}</span>
        <span class="channel-names">→ ${boundHtml}</span>
        <div class="item-actions">
          <label class="switch-inline"><input type="checkbox" data-act="enable" ${r.enabled ? "checked" : ""}> 启用</label>
          <button class="btn sm" data-act="purge">清空记录</button>
          <button class="btn sm danger" data-act="del">删除</button>
        </div>
      </div>
      <div class="grid">
        <div class="field"><label>规则名称</label><input data-key="name" value="${esc(r.name)}"></div>
        <div class="field"><label>监听目录（容器内路径）</label><input data-key="watch_dir" value="${esc(r.watch_dir)}">
          <div class="help">默认只读挂载到 /data</div></div>
        <div class="field"><label>包含模式（逗号分隔）</label><input data-key="include" value="${esc((r.include || []).join(", "))}">
          <div class="help">例：*.png, *.jpg, *.md</div></div>
        <div class="field"><label>排除模式（逗号分隔）</label><input data-key="exclude" value="${esc((r.exclude || []).join(", "))}"></div>
        <div class="field"><label>递归子目录</label>
          <select data-key="recursive"><option value="true" ${r.recursive ? "selected" : ""}>是</option>
          <option value="false" ${!r.recursive ? "selected" : ""}>否</option></select></div>
        <div class="field"><label>markdown 处理</label>
          <select data-key="markdown_mode">
            <option value="auto" ${r.markdown_mode === "auto" ? "selected" : ""}>自动（超限转文件）</option>
            <option value="file" ${r.markdown_mode === "file" ? "selected" : ""}>总是当文件发</option>
            <option value="split" ${r.markdown_mode === "split" ? "selected" : ""}>总是切片发</option>
          </select></div>
        <div class="field"><label>标题模板</label><input data-key="caption_template" value="${esc(r.caption_template)}">
          <div class="help">可用 {filename} {name} {dirname} {path}</div></div>
        <div class="field"><label>单文件上限(字节)</label><input type="number" data-key="max_file_bytes" value="${r.max_file_bytes}"></div>
        ${intervalHtml}
        <div class="field field-full"><label>绑定通道（点击切换，仅显示已启用）</label>
          <div class="chan-bind">${chips}</div>
          ${hiddenNote}</div>
      </div>
      <div class="rule-sched">
        <div class="rs-head">
          <span class="rs-title">⏰ 定时开关（本规则独立控制）</span>
          <label class="switch-inline"><input type="checkbox" data-act="sched-enable" ${sched.enabled ? "checked" : ""}> 启用定时</label>
          <button class="btn sm" data-act="sched-add" ${sched.enabled ? "" : "disabled"}>+ 添加时间段</button>
          ${schedPhase}
        </div>
        <div class="rule-windows ${sched.enabled ? "" : "disabled"}" ${sched.enabled ? "" : 'style="display:none"'}>
          ${windowsHtml || '<div class="empty small">还没有时间段。点「+ 添加时间段」，例如 09:00 至 22:00。</div>'}
        </div>
        <div class="help" style="margin-top:6px">开启后仅在该规则的窗口内推送；窗口外自动暂停（不丢文件）。「立即扫描」不受定时限制。</div>
      </div>
    </div>`;
  }).join("");

  host.querySelectorAll(".item").forEach(el => bindRule(el));
  bindTooltips(host);
}

/* 全局轮询间隔（秒），规则里的「独立间隔」留空时用它 */
function globalInterval() {
  const s = state.status;
  return (s && s.scan && s.scan.interval_seconds) || 60;
}

/* 前端本地判断窗口（仅用于显示，不依赖后端轮询） */
function windowActiveNow(sched) {
  if (!sched || !sched.enabled) return true;
  const wins = (sched.windows || []).filter(w => w.start && w.end);
  if (!wins.length) return true;
  const now = new Date();
  const cur = now.getHours() * 60 + now.getMinutes();
  for (const w of wins) {
    const [sh, sm] = w.start.split(":").map(Number);
    const [eh, em] = w.end.split(":").map(Number);
    const s = sh * 60 + sm, e = eh * 60 + em;
    if (s === e) continue;
    if (s < e) { if (cur >= s && cur < e) return true; }
    else { if (cur >= s || cur < e) return true; }
  }
  return false;
}

function bindRule(el) {
  const id = el.dataset.id;
  const rule = () => state.rules.find(x => x.id === id);
  const save = async (patch) => {
    await api(`/api/rules/${id}`, "PUT", patch);
    state.rules = (await api("/api/config")).rules;
  };

  el.querySelectorAll("input[data-key], select[data-key]").forEach(inp => {
    inp.addEventListener("change", async () => {
      const k = inp.dataset.key;
      let v = inp.value;
      if (k === "include" || k === "exclude") {
        v = v.split(",").map(s => s.trim()).filter(Boolean);
      } else if (k === "recursive") {
        v = v === "true";
      } else if (k === "max_file_bytes") {
        v = Number(v);
      } else if (k === "interval_seconds") {
        v = Math.max(0, Number(v) || 0);
      }
      try {
        await save({ [k]: v });
        if (k === "name" || k === "watch_dir" || k === "interval_seconds") toast("已保存");
      } catch (e) { toast(e.message, false); }
    });
  });

  // 绑定通道（点击式多选）
  el.querySelectorAll("[data-chan]").forEach(inp => {
    inp.addEventListener("change", async () => {
      const ids = Array.from(el.querySelectorAll("[data-chan]")).filter(x => x.checked).map(x => x.value);
      const chip = inp.closest(".chan-chip");
      if (chip) chip.classList.toggle("on", inp.checked);
      try {
        await save({ channels: ids });
        const names = ids.map(cid => (state.channels.find(c => c.id === cid) || {}).name || cid);
        el.querySelector(".channel-names").innerHTML =
          "→ " + (names.length ? names.map(n => `<b>${esc(n)}</b>`).join("、") : "（未绑定任何通道）");
        toast("通道绑定已保存");
      } catch (e) { toast(e.message, false); }
    });
  });

  el.querySelector('[data-act="enable"]').addEventListener("change", async (ev) => {
    await save({ enabled: ev.target.checked });
    toast(ev.target.checked ? "规则已启用" : "规则已停用");
    refreshStatus();
  });

  // 定时开关
  el.querySelector('[data-act="sched-enable"]').addEventListener("change", async (ev) => {
    const r = rule();
    const sched = Object.assign({ enabled: false, timezone: "Asia/Shanghai", windows: [] }, r.schedule || {});
    sched.enabled = ev.target.checked;
    await save({ schedule: sched });
    await renderRules();
    toast(ev.target.checked ? "已启用定时" : "已关闭定时");
  });

  el.querySelector('[data-act="sched-add"]').addEventListener("click", async () => {
    if (el.querySelector('[data-act="sched-add"]').disabled) return;
    const r = rule();
    const sched = Object.assign({ enabled: true, timezone: "Asia/Shanghai", windows: [] }, r.schedule || {});
    sched.windows = sched.windows || [];
    sched.windows.push({ start: "09:00", end: "22:00" });
    await save({ schedule: sched });
    await renderRules();
  });

  el.querySelectorAll(".window-row").forEach(row => {
    const idx = Number(row.dataset.widx);
    const upd = async () => {
      const r = rule();
      const sched = Object.assign({ enabled: true, timezone: "Asia/Shanghai", windows: [] }, r.schedule || {});
      sched.windows[idx] = { start: row.querySelector(".win-start").value, end: row.querySelector(".win-end").value };
      await save({ schedule: sched });
    };
    row.querySelector(".win-start").addEventListener("change", upd);
    row.querySelector(".win-end").addEventListener("change", upd);
    row.querySelector(".win-del").addEventListener("click", async () => {
      const r = rule();
      const sched = Object.assign({ enabled: true, timezone: "Asia/Shanghai", windows: [] }, r.schedule || {});
      sched.windows.splice(idx, 1);
      await save({ schedule: sched });
      await renderRules();
    });
  });

  el.querySelector('[data-act="purge"]').addEventListener("click", async () => {
    if (!confirm("清空这条规则的发送记录？下一轮会重新推送目录里所有文件。")) return;
    const r = await api(`/api/rules/${id}/purge`, "POST", {});
    toast(`已清空 ${r.deleted} 条记录`);
  });

  el.querySelector('[data-act="del"]').addEventListener("click", async () => {
    if (!confirm("删除这条规则？")) return;
    await api(`/api/rules/${id}`, "DELETE");
    renderRules(); refreshStatus();
  });
}

/* ---------------- 记录 ---------------- */

/* 不足 15 行时补空行占位，保证每页表格高度恒定（不要滚动，只翻页）。
   若本页一条都没有，首个空行显示「暂无记录/暂无事件」。 */
function emptyRows(filled) {
  const n = Math.max(0, LOG_ROWS - filled);
  if (!n) return "";
  const cols = state.logView === "history" ? 6 : 3;
  const label = state.logView === "history" ? "暂无记录" : "暂无事件";
  let html = "";
  for (let i = 0; i < n; i++) {
    if (filled === 0 && i === 0) {
      html += `<tr class="filler empty-row"><td colspan="${cols}">${label}</td></tr>`;
    } else {
      html += `<tr class="filler"><td colspan="${cols}">&nbsp;</td></tr>`;
    }
  }
  return html;
}

function channelName(cid) {
  return (state.channels.find(c => c.id === cid) || {}).name || cid;
}

function filteredLogs() {
  const f = state.logFilters;
  let rows = state.logData[state.logView] || [];
  if (state.logView === "history" && f.onlyFail) rows = rows.filter(r => r.status !== "ok");
  if (f.time) rows = rows.filter(r => String(r.sent_at || r.created_at || "").includes(f.time));
  if (f.channel) rows = rows.filter(r => channelName(r.channel_id) === f.channel);
  if (f.kind) rows = rows.filter(r => r.kind === f.kind);
  if (f.level) rows = rows.filter(r => r.level === f.level);
  return rows;
}

function renderLogView() {
  const isHistory = state.logView === "history";
  $("#history").style.display = isHistory ? "" : "none";
  $("#events").style.display = isHistory ? "none" : "";
  $("#only-fail").parentElement.style.display = isHistory ? "" : "none";
  $("#flt-channel").style.display = isHistory ? "" : "none";
  $("#flt-kind").style.display = isHistory ? "" : "none";
  $("#flt-level").style.display = isHistory ? "none" : "";

  const rows = filteredLogs();
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  let page = state.logPage[state.logView];
  if (page > totalPages) page = totalPages;
  state.logPage[state.logView] = page;
  const slice = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  $("#log-count").textContent = `共 ${rows.length} 条 · 第 ${page}/${totalPages} 页`;

  if (isHistory) {
    $("#history").innerHTML = `
      <table>
        <colgroup><col class="c-time"><col class="c-chan"><col class="c-kind"><col class="c-file"><col class="c-status"><col class="c-note"></colgroup>
        <thead><tr><th>时间</th><th>通道</th><th>类型</th><th>文件</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>${slice.map(r => {
          const name = channelName(r.channel_id);
          const file = (r.path || "").split("/").pop();
          return `<tr>
            <td class="clip" data-full="${esc(r.sent_at)}">${esc(r.sent_at)}</td>
            <td class="clip" data-full="${esc(name)}">${esc(name)}</td>
            <td>${esc(r.kind)}</td>
            <td class="clip path" data-full="${esc(r.path)}">${esc(file)}</td>
            <td><span class="badge ${r.status === "ok" ? "ok" : (r.status === "skipped" ? "warn" : "err")}">${esc(r.status)}</span></td>
            <td class="clip err" data-full="${esc(r.error || "—")}">${esc(r.error || "—")}</td>
          </tr>`;
        }).join("")}${emptyRows(slice.length)}</tbody></table>`;
  } else {
    $("#events").innerHTML = `
      <table>
        <colgroup><col class="c-time"><col class="c-level"><col class="c-msg"></colgroup>
        <thead><tr><th>时间</th><th>级别</th><th>内容</th></tr></thead>
        <tbody>${slice.map(e => `<tr>
          <td class="clip" data-full="${esc(e.created_at)}">${esc(e.created_at)}</td>
          <td><span class="badge ${e.level === "error" ? "err" : (e.level === "warning" ? "warn" : "ok")}">${esc(e.level)}</span></td>
          <td class="clip err" data-full="${esc(e.message)}">${esc(e.message)}</td>
        </tr>`).join("")}${emptyRows(slice.length)}</tbody></table>`;
  }

  // 分页
  const pager = $("#log-pager");
  if (totalPages <= 1) { pager.innerHTML = ""; }
  else {
    pager.innerHTML = `
      <button class="btn sm" data-pg="first" ${page === 1 ? "disabled" : ""}>« 首页</button>
      <button class="btn sm" data-pg="prev" ${page === 1 ? "disabled" : ""}>‹ 上一页</button>
      <span class="pg-info">${page} / ${totalPages}</span>
      <button class="btn sm" data-pg="next" ${page === totalPages ? "disabled" : ""}>下一页 ›</button>
      <button class="btn sm" data-pg="last" ${page === totalPages ? "disabled" : ""}>末页 »</button>`;
    pager.querySelectorAll("[data-pg]").forEach(b => b.addEventListener("click", () => {
      const act = b.dataset.pg;
      if (act === "first") page = 1;
      else if (act === "prev") page = Math.max(1, page - 1);
      else if (act === "next") page = Math.min(totalPages, page + 1);
      else page = totalPages;
      state.logPage[state.logView] = page;
      renderLogView();
    }));
  }

  // 空白区域也能翻页：记录页下方/两侧的留白点一下就翻页（无下一页时翻上一页）
  const blank = $("#log-blank");
  if (blank) {
    blank.dataset.pages = totalPages;
    blank.dataset.page = page;
    blank.innerHTML = totalPages > 1
      ? `<div class="log-blank-hint">点击此处空白区域可翻页 · ${page} / ${totalPages}</div>`
      : "";
  }

  bindTooltips(document);
}

/* 点击空白区域翻页（事件委托在 main 上，只在点到非交互元素时触发） */
function bindBlankPaging() {
  const main = document.querySelector("main");
  if (!main) return;
  main.addEventListener("click", (ev) => {
    if (state.logView !== "history" && state.logView !== "events") return;
    if (!$("#tab-logs").classList.contains("active")) return;
    // 点到任何可交互/有内容的地方都不翻页
    if (ev.target.closest("button, a, input, select, textarea, label, .item, .block, table, .seg, .filters, .pager, .table-wrap, [data-full]")) return;
    const blank = $("#log-blank");
    if (!blank) return;
    const totalPages = Number(blank.dataset.pages || 1);
    if (totalPages <= 1) return;
    let page = Number(blank.dataset.page || 1);
    page = page >= totalPages ? Math.max(1, page - 1) : page + 1;
    state.logPage[state.logView] = page;
    renderLogView();
    main.scrollTo({ top: main.scrollHeight, behavior: "smooth" });
  });
}

function fillFilters() {
  const chNames = Array.from(new Set(state.channels.map(c => c.name)));
  const curCh = $("#flt-channel").value;
  $("#flt-channel").innerHTML = `<option value="">全部通道</option>` +
    chNames.map(n => `<option value="${esc(n)}" ${n === curCh ? "selected" : ""}>${esc(n)}</option>`).join("");

  const kinds = Array.from(new Set((state.logData.history || []).map(r => r.kind).filter(Boolean)));
  const curKind = $("#flt-kind").value;
  $("#flt-kind").innerHTML = `<option value="">全部类型</option>` +
    kinds.map(k => `<option value="${esc(k)}" ${k === curKind ? "selected" : ""}>${esc(k)}</option>`).join("");

  const levels = Array.from(new Set((state.logData.events || []).map(e => e.level).filter(Boolean)));
  const curLv = $("#flt-level").value;
  $("#flt-level").innerHTML = `<option value="">全部级别</option>` +
    levels.map(l => `<option value="${esc(l)}" ${l === curLv ? "selected" : ""}>${esc(l)}</option>`).join("");
}

async function renderLogs() {
  const [hist, evs] = await Promise.all([
    api("/api/history?limit=500"),
    api("/api/events?limit=500"),
  ]);
  state.logData.history = hist;
  state.logData.events = evs;
  fillFilters();
  renderLogView();
}

function exportLogs() {
  const isHistory = state.logView === "history";
  const rows = filteredLogs();
  let csv;
  if (isHistory) {
    const head = ["时间", "通道", "类型", "文件", "状态", "说明"];
    csv = [head.join(",")].concat(rows.map(r => [
      r.sent_at, channelName(r.channel_id), r.kind, r.path, r.status, (r.error || "")
    ].map(csvCell).join(","))).join("\n");
  } else {
    const head = ["时间", "级别", "内容"];
    csv = [head.join(",")].concat(rows.map(e => [
      e.created_at, e.level, e.message
    ].map(csvCell).join(","))).join("\n");
  }
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  // 加 BOM，Excel 打开中文不乱码
  downloadBlob("\ufeff" + csv, `dir2chat-${isHistory ? "history" : "events"}-${stamp}.csv`, "text/csv");
  toast(`已导出 ${rows.length} 条记录`);
}

function csvCell(v) {
  const s = String(v ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/* ---------------- 初始化 ---------------- */

function bindTabs() {
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      $(`#tab-${tab.dataset.tab}`).classList.add("active");
      // 切标签时把内容区滚回顶部（导航栏本身固定在视口，不会动）
      const m = document.querySelector("main");
      if (m) m.scrollTop = 0;
      if (tab.dataset.tab === "logs") renderLogs();
      if (tab.dataset.tab === "rules") renderRules();
      if (tab.dataset.tab === "overview") { loadScanSettings(); loadTimeSettings(); }
    });
  });
}

function bindLogControls() {
  $$("#log-switch .seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      $$("#log-switch .seg-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.logView = btn.dataset.view;
      updateClearBtn();
      renderLogView();
    });
  });
  $("#only-fail").addEventListener("change", (ev) => {
    state.logFilters.onlyFail = ev.target.checked;
    state.logPage.history = 1;
    renderLogView();
  });
  $("#flt-time").addEventListener("input", (ev) => {
    state.logFilters.time = ev.target.value.trim();
    state.logPage[state.logView] = 1;
    renderLogView();
  });
  $("#flt-channel").addEventListener("change", (ev) => {
    state.logFilters.channel = ev.target.value;
    state.logPage.history = 1; renderLogView();
  });
  $("#flt-kind").addEventListener("change", (ev) => {
    state.logFilters.kind = ev.target.value;
    state.logPage.history = 1; renderLogView();
  });
  $("#flt-level").addEventListener("change", (ev) => {
    state.logFilters.level = ev.target.value;
    state.logPage.events = 1; renderLogView();
  });
  $("#btn-export-logs").addEventListener("click", exportLogs);
  $("#btn-clear-logs").addEventListener("click", async () => {
    const isHist = state.logView === "history";
    if (!confirm(isHist
      ? "清空全部发送记录？下一轮会重新推送目录里所有文件。"
      : "清空全部事件日志？")) return;
    const r = await api(isHist ? "/api/history/purge" : "/api/events/purge", "POST", {});
    toast(`已清空 ${r.deleted} 条`);
    await renderLogs();
  });
  updateClearBtn();
}

function updateClearBtn() {
  const btn = $("#btn-clear-logs");
  if (!btn) return;
  btn.textContent = state.logView === "history" ? "🗑 清空发送记录" : "🗑 清空事件日志";
}

async function init() {
  state.types = await api("/api/channel-types");
  state.channels = (await api("/api/config")).channels;

  bindTabs();
  bindPicker();
  bindLogControls();
  bindBlankPaging();
  renderPicker();

  $("#btn-add-channel").addEventListener("click", async () => {
    const type = newChannelType || (state.types[0] || {}).type;
    if (!type) return;
    const name = ($("#new-channel-name").value || "").trim();
    const ch = await api("/api/channels", "POST", { type, name: name || undefined, enabled: false });
    $("#new-channel-name").value = "";
    toast(`已创建「${ch.name}」，请填写凭据`);
    renderChannels(); refreshStatus();
  });

  $("#btn-refresh-channels").addEventListener("click", async () => {
    state.channels = (await api("/api/config")).channels;
    await renderRules();
    toast("通道列表已刷新");
  });

  $("#btn-add-rule").addEventListener("click", async () => {
    await api("/api/rules", "POST", { name: "新规则", watch_dir: "/data" });
    toast("已创建规则");
    renderRules(); refreshStatus();
  });

  $("#btn-scan").addEventListener("click", async () => {
    try {
      const r = await api("/api/scan/now", "POST", {});
      toast(`本轮命中 ${r.found} 个文件，已入队 ${r.queued}`);
    } catch (e) { toast(e.message, false); }
    refreshStatus();
  });

  $("#btn-export").addEventListener("click", exportConfig);
  $("#btn-save-scan").addEventListener("click", saveScanSettings);
  $("#btn-save-time").addEventListener("click", saveTimeSettings);
  $("#btn-toggle-status").addEventListener("click", () => {
    document.querySelector(".topbar").classList.toggle("show-status");
  });
  $("#btn-import").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", async (ev) => {
    const file = ev.target.files[0];
    if (file) await importConfig(file);
    ev.target.value = "";
  });

  await Promise.all([refreshStatus(), renderChannels(), renderRules(),
                     loadScanSettings(), loadTimeSettings()]);
  setInterval(refreshStatus, 15000);
}

init().catch(e => toast("初始化失败: " + e.message, false));
