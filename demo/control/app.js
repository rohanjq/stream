const OVERLAYS = {
  market_structure: ["Market structure", "Trend and pivots"],
  key_levels: ["Key levels", "Support and resistance"],
  premium_discount: ["Premium / discount", "Range valuation"],
  fvg: ["Fair-value gaps", "Imbalances"],
  order_blocks: ["Order blocks", "Institutional zones"],
  patterns: ["Patterns", "Candle formations"],
  liquidity: ["Liquidity", "Pools and sweeps"],
};

const VIEW_COPY = {
  overview: ["Overview", "Live operations"],
  scene: ["Scene", "Composition and overlays"],
  broadcast: ["Broadcast", "Compositor overlays"],
  voice: ["Voice", "Character and queue"],
  youtube: ["YouTube", "Channel interaction"],
  audience: ["Audience", "Permissions and automation"],
  music: ["Music", "Playback and catalog"],
  activity: ["Activity", "Messages and command audit"],
  settings: ["Settings", "Access and runtime"],
};

const state = {
  control: null,
  health: null,
  runtime: null,
  youtube: null,
  speech: null,
  audience: null,
  compositorHealth: null,
  compositor: null,
  music: null,
  catalog: [],
  messages: [],
  panelConfig: null,
  selectedLayout: "grid",
  selectedTimeframes: ["1m", "5m", "15m", "1h"],
  lastSync: null,
  refreshTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[char]));
const number = (value) => Number(value || 0).toLocaleString();
const titleCase = (value) => String(value || "").replace(/[._]/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

function token() {
  return sessionStorage.getItem("nightshift-control-token") || "";
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(path, {
    method: options.method || (options.body !== undefined ? "POST" : "GET"),
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: AbortSignal.timeout(15000),
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(payload?.error || payload || `Request failed (${response.status})`);
  return payload;
}

function toast(message, type = "success") {
  const element = document.createElement("div");
  element.className = `toast ${type}`;
  element.textContent = message;
  $("#toastRegion").appendChild(element);
  window.setTimeout(() => element.remove(), 4200);
}

function setBusy(button, busy, label = "Working") {
  if (!button) return;
  if (busy) {
    button.dataset.originalLabel = button.textContent;
    button.textContent = label;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalLabel || button.textContent;
    button.disabled = false;
  }
}

function setDot(element, mode) {
  if (!element) return;
  element.classList.remove("healthy", "warning", "unhealthy");
  element.classList.add(mode);
}

function selectView(name, updateHash = true) {
  if (!VIEW_COPY[name]) name = "overview";
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  $$(".nav-item[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $("#viewTitle").textContent = VIEW_COPY[name][0];
  $("#viewEyebrow").textContent = VIEW_COPY[name][1];
  if (updateHash) history.replaceState(null, "", `#${name}`);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setupNavigation() {
  $$("[data-view]").forEach((button) => button.addEventListener("click", () => selectView(button.dataset.view)));
  $$("[data-go]").forEach((button) => button.addEventListener("click", () => selectView(button.dataset.go)));
  selectView(location.hash.slice(1) || "overview", false);
  window.addEventListener("hashchange", () => selectView(location.hash.slice(1), false));
}

function setupPreview() {
  const sceneUrl = `${location.protocol}//${location.hostname}:8080/?operator-preview=1`;
  $("#scenePreview").src = sceneUrl;
  $("#openScene").href = sceneUrl;
  $("#runtimeScene").textContent = sceneUrl;
  $("#runtimeConsole").textContent = location.href.split("#")[0];
  $("#scenePreview").addEventListener("load", () => $("#previewShade").classList.add("hidden"));
}

function announcementPatch() {
  if (!$("#announceScene").checked) return { announce: false };
  const patch = { announce: true };
  const announcement = $("#sceneAnnouncement").value.trim();
  const requestedBy = $("#sceneRequestedBy").value.trim();
  const context = $("#sceneContext").value.trim();
  if (announcement) patch.announcement = announcement;
  if (requestedBy) patch.requested_by = requestedBy;
  if (context) patch.announcement_context = context;
  return patch;
}

function resetAnnouncement() {
  $("#announceScene").checked = false;
  $("#sceneAnnouncement").value = "";
  $("#sceneContext").value = "";
}

async function applyControl(patch, message = "Scene updated") {
  const result = await api("/api/control", {
    body: { ...patch, ...announcementPatch(), source: "operator-console" },
  });
  state.control = result;
  state.selectedLayout = result.layout;
  state.selectedTimeframes = [...result.timeframes];
  resetAnnouncement();
  renderControl();
  renderOverview();
  toast(result.announcement?.queued ? `${message}; announcement queued` : message);
  return result;
}

function renderControl() {
  const control = state.control;
  if (!control) return;
  $("#sceneRevision").textContent = `Revision ${control.revision}`;
  $("#showSidebar").checked = control.show_sidebar;
  $("#sidebarState").textContent = control.show_sidebar ? "Currently visible" : "Currently hidden";
  $$("[data-layout]").forEach((button) => button.classList.toggle("active", button.dataset.layout === state.selectedLayout));
  $$("[data-scene-timeframe]").forEach((button) => button.classList.toggle("active", state.selectedTimeframes[0] === button.dataset.sceneTimeframe));
  $$("#gridChoices input").forEach((input) => { input.checked = state.selectedTimeframes.includes(input.value); });
  $$(".overlay-item input").forEach((input) => { input.checked = Boolean(control.overlays[input.dataset.overlay]); });
  const label = state.selectedLayout === "single" ? state.selectedTimeframes[0] : state.selectedTimeframes.join(" / ");
  $("#sceneSelectionSummary").textContent = `${titleCase(state.selectedLayout)} · ${label}`;
}

function renderOverview() {
  const health = state.health || {};
  const youtube = state.youtube || {};
  const audience = state.audience || {};
  const speech = state.speech || {};
  const control = state.control;
  setDot($("#sceneService"), health.scene ? "healthy" : "unhealthy");
  setDot($("#musicService"), health.music ? "healthy" : "unhealthy");
  setDot($("#youtubeService"), youtube.connected ? "healthy" : youtube.configured ? "warning" : "unhealthy");
  setDot($("#ohlcService"), "warning");
  $("#sceneServiceText").textContent = health.scene ? "Online" : "Unavailable";
  $("#musicServiceText").textContent = health.music ? "Online" : "Unavailable";
  $("#youtubeServiceText").textContent = youtube.connected ? "Connected" : youtube.configured ? "Reconnecting" : "Not configured";
  $("#ohlcServiceText").textContent = state.runtime?.ohlc_symbol ? `Configured · ${state.runtime.ohlc_symbol}` : "No runtime config";
  $("#overviewSpeechState").textContent = speech.speaking ? "Speaking now" : "Ready";
  $("#overviewSpeechMeta").textContent = `${number(speech.queued)} queued · ${number(speech.completed)} completed`;
  $("#metricReceived").textContent = number(youtube.received);
  $("#metricSpoken").textContent = number(youtube.spoken);
  $("#metricExecuted").textContent = number(audience.counts?.executed);
  $("#metricQueued").textContent = number(Number(speech.queued || 0) + Number(audience.queued || 0));
  if (control) {
    $("#previewLayout").textContent = `${titleCase(control.layout)} · ${control.timeframes.join(" / ")}`;
    $("#previewRevision").textContent = `Revision ${control.revision}`;
  }
  renderMusicSummary();
  renderMiniMessages();
}

function renderMusicSummary() {
  const music = state.music;
  if (!music?.track) return;
  $("#overviewTrack").textContent = music.track.title;
  $("#overviewArtist").textContent = music.track.artist || "Unknown artist";
  $("#overviewCredit").textContent = music.track.credit || "Licensed catalog";
  $("#overviewMusicMode").textContent = music.playing ? "Playing" : "Paused";
  $("#musicTrackTitle").textContent = music.track.title;
  $("#musicTrackArtist").textContent = music.track.artist || "Unknown artist";
  $("#musicTrackCredit").textContent = music.track.credit || "Licensed catalog";
  $("#musicPlaybackState").textContent = music.playing ? "Playing" : "Paused";
  $("#recordArt").classList.toggle("playing", Boolean(music.playing));
  $$("[data-music-toggle]").forEach((button) => { button.textContent = music.playing ? "Pause" : "Play"; });
  if (document.activeElement !== $("#musicVolume")) $("#musicVolume").value = music.volume ?? 0;
  $("#musicVolumeOutput").textContent = `${Math.round(Number(music.volume || 0) * 100)}%`;
  renderCatalog();
}

function renderMiniMessages() {
  const messages = state.messages.slice(-3).reverse();
  $("#overviewMessages").innerHTML = messages.length ? messages.map((message) => `
    <div class="mini-message ${escapeHtml(message.kind)}"><strong>${escapeHtml(message.who)}</strong><span>${escapeHtml(message.text)}</span></div>`).join("") : '<p class="empty-state">Waiting for messages</p>';
}

function renderYoutube() {
  const youtube = state.youtube;
  if (!youtube) return;
  if (document.activeElement !== $("#youtubeMode")) $("#youtubeMode").value = youtube.speak_mode || "off";
  if (document.activeElement !== $("#youtubeCooldown")) $("#youtubeCooldown").value = youtube.cooldown_seconds ?? 0;
  $("#youtubeConnection").textContent = youtube.connected ? "Connected" : youtube.configured ? "Reconnecting" : "Not configured";
  $("#youtubeConnection").classList.toggle("warning", Boolean(youtube.configured && !youtube.connected));
  $("#youtubeLastError").textContent = youtube.last_error || "No connection errors";
  $("#ytReceived").textContent = number(youtube.received);
  $("#ytSpoken").textContent = number(youtube.spoken);
  $("#ytPublished").textContent = number(youtube.published);
  $("#ytDropped").textContent = number(youtube.dropped);
  const template = $("#youtubeTemplate");
  const selected = template.value;
  template.innerHTML = (youtube.templates || []).map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(titleCase(name))}</option>`).join("");
  if ([...template.options].some((option) => option.value === selected)) template.value = selected;
}

function renderSpeech() {
  const speech = state.speech;
  if (!speech) return;
  $("#speechMode").textContent = speech.speaking ? "Speaking" : "Idle";
  $("#speechQueued").textContent = number(speech.queued);
  $("#speechCompleted").textContent = number(speech.completed);
  $("#speechDropped").textContent = number(speech.dropped);
  $("#speechGap").textContent = `${speech.gap_seconds ?? 0}s`;
  $("#speechCurrent").textContent = speech.current?.source || "None";
}

function renderAudience() {
  const audience = state.audience;
  if (!audience) return;
  const counts = audience.counts || {};
  for (const name of ["considered", "matched", "approved", "executed", "denied", "failed"]) {
    $(`#audience${titleCase(name)}`).textContent = number(counts[name]);
  }
  $("#audienceRouter").textContent = `Router: ${audience.router || "--"}`;
  const container = $("#audiencePolicies");
  if (container.matches(":focus-within")) return;
  const roleLabels = ["viewer", "sponsor", "superchat", "moderator", "owner"];
  container.innerHTML = Object.entries(audience.rules || {}).map(([tool, rule]) => `
    <article class="policy-card" data-policy-tool="${escapeHtml(tool)}">
      <div class="policy-title"><strong>${escapeHtml(titleCase(tool))}</strong><span>${escapeHtml(tool)}</span></div>
      <div class="policy-controls">
        <label class="input-field"><span>Global cooldown</span><input name="global_cooldown_seconds" type="number" min="0" value="${Number(rule.global_cooldown_seconds || 0)}"></label>
        <label class="input-field"><span>User cooldown</span><input name="user_cooldown_seconds" type="number" min="0" value="${Number(rule.user_cooldown_seconds || 0)}"></label>
        <label class="input-field"><span>Default duration</span><input name="default_duration_seconds" type="number" min="0" value="${Number(rule.default_duration_seconds || 0)}"></label>
        <label class="input-field"><span>Maximum duration</span><input name="max_duration_seconds" type="number" min="0" value="${Number(rule.max_duration_seconds || 0)}"></label>
        <label class="input-field"><span>Min. Super Chat</span><input name="min_superchat_micros" type="number" min="0" step="100000" value="${Number(rule.min_superchat_micros || 0)}"></label>
        <div class="role-row">${roleLabels.map((role) => `<label><input type="checkbox" name="roles" value="${role}" ${rule.roles?.includes(role) ? "checked" : ""}><span>${role}</span></label>`).join("")}</div>
      </div>
      <div class="policy-flags">
        <label title="Enable command"><span class="field-label">Enabled</span><span class="switch"><input name="enabled" type="checkbox" ${rule.enabled ? "checked" : ""}><span></span></span></label>
        <label title="Execute automatically"><span class="field-label">Auto</span><span class="switch"><input name="auto_execute" type="checkbox" ${rule.auto_execute ? "checked" : ""}><span></span></span></label>
        <label title="Require paid message"><span class="field-label">Paid</span><span class="switch"><input name="require_superchat" type="checkbox" ${rule.require_superchat ? "checked" : ""}><span></span></span></label>
        <label title="Announce before action"><span class="field-label">Announce</span><span class="switch"><input name="announce_result" type="checkbox" ${rule.announce_result ? "checked" : ""}><span></span></span></label>
        <button class="button compact" type="button" data-save-policy>Save</button>
      </div>
    </article>`).join("") || '<p class="empty-state">No command policies configured</p>';
}

function renderBroadcast() {
  const health = state.compositorHealth || {};
  const compositor = state.compositor || {};
  setDot($("#compositorBeacon"), health.status === "ok" ? "healthy" : health.status === "stalled" ? "unhealthy" : "warning");
  $("#compositorStatus").textContent = titleCase(health.status || "Unavailable");
  $("#compositorFrameAge").textContent = health.last_frame_age_seconds === undefined ? "--" : `${health.last_frame_age_seconds}s ago`;
  $("#compositorLayout").textContent = titleCase(compositor.layout || "--");
  $("#compositorInput").textContent = health.input || "--";
  $$("[data-broadcast-layout]").forEach((button) => button.classList.toggle("active", button.dataset.broadcastLayout === compositor.layout));
  $("#broadcastLogs").innerHTML = compositor.logs?.length ? [...compositor.logs].reverse().map((entry) => `<div class="log-entry"><time>${escapeHtml(entry.ts)}</time><span>${escapeHtml(entry.text)}</span></div>`).join("") : '<p class="empty-state">No updates yet</p>';
  if (compositor.poll) {
    $("#currentPoll").innerHTML = `<strong>${escapeHtml(compositor.poll.question)}</strong>${(compositor.poll.options || []).map((option) => `<div class="poll-option"><span>${escapeHtml(option.label)}</span><b>${number(option.votes)} votes</b><button type="button" data-poll-vote="${escapeHtml(option.label)}">+ vote</button></div>`).join("")}`;
  } else {
    $("#currentPoll").innerHTML = '<p class="empty-state">No active poll</p>';
  }
  if (document.activeElement !== $("#leaderboardEntries") && !$("#leaderboardEntries").value.trim()) {
    $("#leaderboardEntries").value = (compositor.leaderboard || []).map((entry) => `${entry.name}: ${entry.score}`).join("\n");
  }
}

function renderCatalog() {
  const query = $("#musicSearch").value.trim().toLowerCase();
  const tracks = state.catalog.filter((track) => `${track.title} ${track.artist}`.toLowerCase().includes(query));
  $("#musicCatalog").innerHTML = tracks.length ? tracks.map((track, index) => `
    <button class="track-row ${state.music?.track?.id === track.id ? "active" : ""}" type="button" data-track-id="${escapeHtml(track.id)}">
      <span class="track-index">${String(index + 1).padStart(2, "0")}</span>
      <span><strong>${escapeHtml(track.title)}</strong><small>${escapeHtml(track.artist || "Unknown artist")}</small></span>
      <small>${state.music?.track?.id === track.id ? (state.music.playing ? "Playing" : "Paused") : "Play"}</small>
    </button>`).join("") : '<p class="empty-state">No matching tracks</p>';
}

function renderActivity() {
  const messages = [...state.messages].reverse();
  $("#activityMessages").innerHTML = messages.length ? messages.map((message) => `
    <article class="activity-row"><time>#${message.id}</time><strong>${escapeHtml(message.who)}</strong><p>${escapeHtml(message.text)}</p><small>${escapeHtml(titleCase(message.kind))}</small></article>`).join("") : '<p class="empty-state">Waiting for messages</p>';
  const commands = [...(state.audience?.recent || [])].reverse();
  $("#activityCommands").innerHTML = commands.length ? commands.map((entry) => `
    <article class="activity-row"><time>${formatTime(entry.completed_at || entry.at)}</time><strong>${escapeHtml(entry.author || entry.role || "Audience")}</strong><p>${escapeHtml(titleCase(entry.tool))} · ${escapeHtml(entry.reason || entry.parser || "approved")}</p><small class="${entry.executed ? "result-ok" : entry.reason ? "result-bad" : ""}">${entry.executed ? "Executed" : entry.reason ? "Denied" : "Approved"}</small></article>`).join("") : '<p class="empty-state">No audience commands yet</p>';
}

function renderSettings() {
  $("#tokenState").textContent = token() ? "Session token set" : state.panelConfig?.local_access ? "Local access" : "Token required";
  $("#runtimeSymbol").textContent = state.runtime?.ohlc_symbol || "--";
  $("#runtimeWs").textContent = state.runtime?.ohlc_ws_url || "--";
  const endpoints = [
    ["Scene state", "/api/control", state.control ? "Responding" : "Unavailable"],
    ["Speech", "/api/speech/status", state.speech ? "Responding" : "Unavailable"],
    ["Audience", "/api/audience/status", state.audience ? "Responding" : "Unavailable"],
    ["Music", "/api/music/status", state.music ? "Responding" : "Unavailable"],
  ];
  $("#endpointList").innerHTML = endpoints.map(([name, path, status]) => `<div class="endpoint"><span>${path}</span><strong>${name}</strong><small>${status}</small></div>`).join("");
}

function formatTime(timestamp) {
  if (!timestamp) return "--";
  return new Date(Number(timestamp) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatElapsed(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function renderConnection(ok) {
  setDot($("#syncDot"), ok ? "healthy" : "unhealthy");
  setDot($("#railHealthDot"), ok ? (state.health?.status === "degraded" ? "warning" : "healthy") : "unhealthy");
  $("#syncText").textContent = ok ? "Synced" : "Connection lost";
  $("#railHealthText").textContent = ok ? (state.health?.status === "degraded" ? "Degraded" : "Systems online") : "API unavailable";
}

function renderAll() {
  renderControl();
  renderOverview();
  renderYoutube();
  renderSpeech();
  renderAudience();
  renderBroadcast();
  renderActivity();
  renderSettings();
}

async function refresh(options = {}) {
  const requests = {
    health: api("/api/health"),
    control: api("/api/control"),
    runtime: api("/api/runtime-config"),
    youtube: api("/api/youtube/status"),
    speech: api("/api/speech/status"),
    audience: api("/api/audience/status"),
    compositorHealth: api("/compositor-api/health"),
    compositor: api("/compositor-api/state"),
    music: api("/api/music/status"),
    catalog: api("/api/music/catalog"),
    messageResult: api("/api/messages?since=0"),
    panelConfig: api("/panel-config"),
  };
  const entries = Object.entries(requests);
  const results = await Promise.allSettled(entries.map(([, request]) => request));
  let successCount = 0;
  results.forEach((result, index) => {
    const key = entries[index][0];
    if (result.status === "fulfilled") {
      successCount += 1;
      if (key === "messageResult") state.messages = result.value.messages || [];
      else if (key === "catalog") state.catalog = result.value.tracks || [];
      else state[key] = result.value;
    }
  });
  if (state.control && !state.lastSync) {
    state.selectedLayout = state.control.layout;
    state.selectedTimeframes = [...state.control.timeframes];
  }
  state.lastSync = Date.now();
  renderAll();
  const healthIndex = entries.findIndex(([key]) => key === "health");
  renderConnection(results[healthIndex]?.status === "fulfilled");
  if (options.notify) toast(successCount === entries.length ? "Control data refreshed" : `Refreshed ${successCount} of ${entries.length} services`, successCount ? "warning" : "error");
}

async function compositorAction(path, body, successMessage) {
  await api(`/compositor-api${path}`, { body });
  const [health, compositor] = await Promise.all([
    api("/compositor-api/health"), api("/compositor-api/state"),
  ]);
  state.compositorHealth = health;
  state.compositor = compositor;
  renderBroadcast();
  toast(successMessage);
}

async function musicAction(action, body = {}) {
  const result = await api(`/api/music/${action}`, { body });
  state.music = result;
  renderMusicSummary();
  const messages = { play: "Track changed", next: "Next track playing", previous: "Previous track playing", pause: "Music paused", resume: "Music resumed", volume: "Music volume updated" };
  toast(messages[action] || "Music updated");
}

function setupSceneControls() {
  $("#overlayGrid").innerHTML = Object.entries(OVERLAYS).map(([key, [label, detail]]) => `
    <label class="overlay-item"><span><strong>${label}</strong><small>${detail}</small></span><span class="switch"><input type="checkbox" data-overlay="${key}"><span></span></span></label>`).join("");
  $$("[data-layout]").forEach((button) => button.addEventListener("click", () => {
    state.selectedLayout = button.dataset.layout;
    if (state.selectedLayout === "single") state.selectedTimeframes = [state.selectedTimeframes[0] || "1m"];
    renderControl();
  }));
  $$("[data-scene-timeframe]").forEach((button) => button.addEventListener("click", () => {
    state.selectedLayout = "single";
    state.selectedTimeframes = [button.dataset.sceneTimeframe];
    renderControl();
  }));
  $$("#gridChoices input").forEach((input) => input.addEventListener("change", () => {
    const selected = $$("#gridChoices input:checked").map((item) => item.value);
    if (selected.length > 4) {
      input.checked = false;
      return toast("A grid can show at most four timeframes", "warning");
    }
    if (!selected.length) {
      input.checked = true;
      return toast("Select at least one timeframe", "warning");
    }
    state.selectedLayout = selected.length === 1 ? "single" : "grid";
    state.selectedTimeframes = selected;
    renderControl();
  }));
  $$("[data-grid-preset]").forEach((button) => button.addEventListener("click", () => {
    state.selectedTimeframes = button.dataset.gridPreset.split(",");
    state.selectedLayout = state.selectedTimeframes.length === 1 ? "single" : "grid";
    renderControl();
  }));
  $("#applyScene").addEventListener("click", async () => {
    setBusy($("#applyScene"), true, "Applying");
    try { await applyControl({ layout: state.selectedLayout, timeframes: state.selectedTimeframes }); }
    catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#applyScene"), false); }
  });
  $("#showSidebar").addEventListener("change", async (event) => {
    try { await applyControl({ show_sidebar: event.target.checked }, `Conversation panel ${event.target.checked ? "shown" : "hidden"}`); }
    catch (error) { event.target.checked = !event.target.checked; toast(error.message, "error"); }
  });
  $$(".overlay-item input").forEach((input) => input.addEventListener("change", async (event) => {
    try { await applyControl({ overlays: { [event.target.dataset.overlay]: event.target.checked } }, `${OVERLAYS[event.target.dataset.overlay][0]} ${event.target.checked ? "enabled" : "disabled"}`); }
    catch (error) { event.target.checked = !event.target.checked; toast(error.message, "error"); }
  }));
  $("#disableOverlays").addEventListener("click", async () => {
    try { await applyControl({ overlays: Object.fromEntries(Object.keys(OVERLAYS).map((key) => [key, false])) }, "All overlays cleared"); }
    catch (error) { toast(error.message, "error"); }
  });
  $$("[data-quick-timeframe]").forEach((button) => button.addEventListener("click", async () => {
    try { await applyControl({ timeframe: button.dataset.quickTimeframe }, `${button.dataset.quickTimeframe} scene live`); }
    catch (error) { toast(error.message, "error"); }
  }));
  $$("[data-quick-grid]").forEach((button) => button.addEventListener("click", async () => {
    try { await applyControl({ layout: "grid", timeframes: button.dataset.quickGrid.split(",") }, "Four-view grid live"); }
    catch (error) { toast(error.message, "error"); }
  }));
}

function setupVoice() {
  $("#voiceText").addEventListener("input", (event) => { $("#voiceCount").textContent = event.target.value.length; });
  $("#speakNow").addEventListener("click", async () => {
    const text = $("#voiceText").value.trim();
    if (!text) return toast("Enter words for the character", "warning");
    setBusy($("#speakNow"), true, "Queueing");
    try {
      const result = await api("/api/speech/trigger", { body: {
        text,
        priority: $("#voicePriority").value,
        source: $("#voiceSource").value.trim() || "operator",
        show_text: $("#voiceShowText").checked,
        dedupe_key: $("#voiceDedupe").value.trim(),
        cooldown_seconds: Number($("#voiceCooldown").value || 0),
      } });
      if (!result.queued) throw new Error(result.reason || "Speech was not queued");
      $("#voiceText").value = "";
      $("#voiceCount").textContent = "0";
      toast(`Speech queued at ${result.priority} priority`);
      state.speech = await api("/api/speech/status");
      renderSpeech();
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#speakNow"), false); }
  });
  $("#askHost").addEventListener("click", async () => {
    const text = $("#askText").value.trim();
    if (!text) return toast("Enter a question for the host", "warning");
    setBusy($("#askHost"), true, "Asking");
    try {
      await api("/api/ask", { body: { who: $("#askWho").value.trim() || "Operator", text } });
      $("#askText").value = "";
      toast("Question sent to the host");
      await refresh();
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#askHost"), false); }
  });
}

function setupBroadcast() {
  $("#broadcastLog").addEventListener("input", (event) => { $("#broadcastLogCount").textContent = event.target.value.length; });
  $$("[data-broadcast-layout]").forEach((button) => button.addEventListener("click", async () => {
    try { await compositorAction("/layout", { layout: button.dataset.broadcastLayout }, `${titleCase(button.dataset.broadcastLayout)} overlay live`); }
    catch (error) { toast(error.message, "error"); }
  }));
  $("#pushBroadcastLog").addEventListener("click", async () => {
    const text = $("#broadcastLog").value.trim();
    if (!text) return toast("Enter an update first", "warning");
    setBusy($("#pushBroadcastLog"), true, "Adding");
    try {
      await compositorAction("/log", { text }, "Broadcast update added");
      $("#broadcastLog").value = "";
      $("#broadcastLogCount").textContent = "0";
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#pushBroadcastLog"), false); }
  });
  $("#createPoll").addEventListener("click", async () => {
    const question = $("#pollQuestion").value.trim();
    const options = $("#pollOptions").value.split("\n").map((item) => item.trim()).filter(Boolean);
    if (!question || options.length < 2) return toast("Enter a question and at least two options", "warning");
    setBusy($("#createPoll"), true, "Creating");
    try {
      await compositorAction("/poll", { question, options }, "Poll created");
      if ($("#showPollAfterCreate").checked) await compositorAction("/layout", { layout: "poll" }, "Poll overlay live");
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#createPoll"), false); }
  });
  $("#currentPoll").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-poll-vote]");
    if (!button) return;
    try { await compositorAction("/poll/vote", { option: button.dataset.pollVote }, `Vote added for ${button.dataset.pollVote}`); }
    catch (error) { toast(error.message, "error"); }
  });
  $("#saveLeaderboard").addEventListener("click", async () => {
    const lines = $("#leaderboardEntries").value.split("\n").map((line) => line.trim()).filter(Boolean);
    const entries = [];
    for (const line of lines) {
      const splitAt = line.lastIndexOf(":");
      const name = line.slice(0, splitAt).trim();
      const score = Number(line.slice(splitAt + 1).trim());
      if (splitAt < 1 || !name || !Number.isFinite(score)) return toast(`Invalid leaderboard entry: ${line}`, "warning");
      entries.push({ name, score });
    }
    if (!entries.length) return toast("Enter at least one leaderboard entry", "warning");
    setBusy($("#saveLeaderboard"), true, "Saving");
    try {
      await compositorAction("/leaderboard", { entries }, "Leaderboard updated");
      if ($("#showLeaderboardAfterSave").checked) await compositorAction("/layout", { layout: "leaderboard" }, "Leaderboard overlay live");
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#saveLeaderboard"), false); }
  });
}

function setupYoutube() {
  $("#youtubeMessage").addEventListener("input", (event) => { $("#youtubeMessageCount").textContent = event.target.value.length; });
  $("#saveYoutubePolicy").addEventListener("click", async () => {
    setBusy($("#saveYoutubePolicy"), true, "Saving");
    try {
      await api("/api/youtube/policy", { body: { speak_mode: $("#youtubeMode").value, cooldown_seconds: Number($("#youtubeCooldown").value) } });
      state.youtube = await api("/api/youtube/status");
      renderYoutube();
      toast("YouTube reply policy saved");
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#saveYoutubePolicy"), false); }
  });
  $("#postTemplate").addEventListener("click", async () => {
    if (!$("#youtubeTemplate").value) return toast("No message template is available", "warning");
    setBusy($("#postTemplate"), true, "Publishing");
    try {
      await api("/api/youtube/publish", { body: { template: $("#youtubeTemplate").value, source: "operator-console" } });
      toast("Template queued for YouTube");
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#postTemplate"), false); }
  });
  $("#postYoutubeMessage").addEventListener("click", async () => {
    const text = $("#youtubeMessage").value.trim();
    if (!text) return toast("Write a channel message first", "warning");
    setBusy($("#postYoutubeMessage"), true, "Publishing");
    try {
      await api("/api/youtube/publish", { body: { text, category: "manual", source: "operator-console" } });
      $("#youtubeMessage").value = "";
      $("#youtubeMessageCount").textContent = "0";
      toast("Message queued for YouTube");
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy($("#postYoutubeMessage"), false); }
  });
}

function setupAudience() {
  $("#audiencePolicies").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-save-policy]");
    if (!button) return;
    const card = button.closest("[data-policy-tool]");
    const numeric = ["global_cooldown_seconds", "user_cooldown_seconds", "default_duration_seconds", "max_duration_seconds", "min_superchat_micros"];
    const rule = Object.fromEntries(numeric.map((name) => [name, Number($(`[name="${name}"]`, card).value || 0)]));
    rule.roles = $$('[name="roles"]:checked', card).map((input) => input.value);
    for (const name of ["enabled", "auto_execute", "require_superchat", "announce_result"]) rule[name] = $(`[name="${name}"]`, card).checked;
    if (!rule.roles.length) return toast("At least one audience role is required", "warning");
    setBusy(button, true, "Saving");
    try {
      await api("/api/audience/policy", { body: { tool: card.dataset.policyTool, rule } });
      state.audience = await api("/api/audience/status");
      toast(`${titleCase(card.dataset.policyTool)} policy saved`);
    } catch (error) { toast(error.message, "error"); }
    finally { setBusy(button, false); }
  });
}

function setupMusic() {
  document.addEventListener("click", async (event) => {
    const actionButton = event.target.closest("[data-music-action]");
    const toggleButton = event.target.closest("[data-music-toggle]");
    const trackButton = event.target.closest("[data-track-id]");
    try {
      if (actionButton) await musicAction(actionButton.dataset.musicAction);
      else if (toggleButton) await musicAction(state.music?.playing ? "pause" : "resume");
      else if (trackButton) await musicAction("play", { id: trackButton.dataset.trackId });
    } catch (error) { toast(error.message, "error"); }
  });
  $("#musicVolume").addEventListener("input", (event) => { $("#musicVolumeOutput").textContent = `${Math.round(Number(event.target.value) * 100)}%`; });
  $("#musicVolume").addEventListener("change", async (event) => {
    try { await musicAction("volume", { volume: Number(event.target.value) }); }
    catch (error) { toast(error.message, "error"); }
  });
  $("#musicSearch").addEventListener("input", renderCatalog);
}

function setupActivity() {
  $$("[data-activity]").forEach((button) => button.addEventListener("click", () => {
    $$("[data-activity]").forEach((item) => item.classList.toggle("active", item === button));
    $$(".activity-log").forEach((log) => log.classList.toggle("active", log.id === `activity${titleCase(button.dataset.activity)}`));
  }));
  $("#refreshActivity").addEventListener("click", async () => {
    try { await refresh({ notify: true }); } catch (error) { toast(error.message, "error"); }
  });
}

function setupSettings() {
  $("#controlToken").value = token();
  $("#saveToken").addEventListener("click", async () => {
    const value = $("#controlToken").value.trim();
    if (value) sessionStorage.setItem("nightshift-control-token", value);
    else sessionStorage.removeItem("nightshift-control-token");
    renderSettings();
    await refresh({ notify: true });
  });
  $("#clearToken").addEventListener("click", () => {
    sessionStorage.removeItem("nightshift-control-token");
    $("#controlToken").value = "";
    renderSettings();
    toast("Session token cleared");
  });
}

function setupClock() {
  const update = () => {
    $("#clock").textContent = new Date().toLocaleTimeString([], { hour12: false });
    if (state.music?.playing) {
      state.music.elapsed_seconds = Number(state.music.elapsed_seconds || 0) + 1;
      $("#musicElapsed").textContent = formatElapsed(state.music.elapsed_seconds);
    } else {
      $("#musicElapsed").textContent = formatElapsed(state.music?.elapsed_seconds || 0);
    }
  };
  update();
  window.setInterval(update, 1000);
}

async function start() {
  setupNavigation();
  setupPreview();
  setupSceneControls();
  setupBroadcast();
  setupVoice();
  setupYoutube();
  setupAudience();
  setupMusic();
  setupActivity();
  setupSettings();
  setupClock();
  $("#refreshAll").addEventListener("click", () => refresh({ notify: true }));
  try { await refresh(); }
  catch (error) { renderConnection(false); toast(error.message, "error"); }
  state.refreshTimer = window.setInterval(() => refresh().catch(() => renderConnection(false)), 5000);
}

start();