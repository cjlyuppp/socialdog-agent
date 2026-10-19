"use strict";

const $ = (id) => document.getElementById(id);
const workspace = document.querySelector(".workspace");
const state = {status: null, session: null, sessions: [], jobs: new Map(), pollers: new Map(), token: "", sending: false, selection: 0};
const toolLabels = {describe_data: "数据清单", query_aggregate: "订单汇总", inspect_factors: "候选因素检查", check_analysis_readiness: "研究条件检查"};

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

async function api(path, body) {
  const options = {cache: "no-store", headers: {}};
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {"Content-Type": "application/json", "X-Socialdog-Token": state.token};
    options.body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error("无法连接本地服务，请重新打开“启动网页研究助理.command”。"); }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "本次请求未完成，请重试。");
  return data;
}

function notice(text = "") {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}

function date(value, withTime = false) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "—";
  const parts = new Intl.DateTimeFormat("sv-SE", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", ...(withTime ? {hour: "2-digit", minute: "2-digit", hour12: false} : {})}).format(parsed);
  return parts;
}

function showSource(info, version, historical = false) {
  const source = info || {};
  $("data-cutoff").textContent = date(source.source_data_as_of);
  $("source-checked").textContent = date(source.source_checked_at, true);
  $("snapshot-version").textContent = version || "—";
  const ready = state.status?.ready;
  const overdue = source.freshness_status === "check_overdue";
  const sourceStatus = $("source-status");
  sourceStatus.replaceChildren(node("span", "status-dot"), document.createTextNode(
    !info ? "数据连接暂不可用" : historical ? "这条回答使用的版本" : overdue ? "来源核验已过期" : "已连接 · 每日同步"));
  sourceStatus.className = `source-status${!info ? " error" : overdue ? " warning" : ""}`;
  $("source-note").textContent = !info ? "请检查本机配置与飞书每日同步。" : overdue ? "最近核验已超过每日检查窗口，尚未确认来源有无新变化。" : "通过飞书每日核验读取。数据截至日期不等于今天，下一次提问会检查最新同步版本。";
  $("welcome-source").textContent = source.source_data_as_of ? `数据截至 ${date(source.source_data_as_of)} · 每天核验，逐题锁定版本` : "数据连接暂不可用，请检查本机同步。";
  $("model-pill").textContent = state.status?.model || "DeepSeek";
  if (!ready && !state.session?.turns.length) notice(state.status?.error || "正在检查配置…");
}

async function refreshStatus() {
  const status = await api("/api/status");
  state.status = status;
  state.token = status.csrf_token;
  showSource(status.data_source, status.snapshot_version);
  updateSend();
  if (status.ready) notice();
  else notice(status.error || "本地配置暂不可用。");
}

async function refreshSessions() {
  state.sessions = (await api("/api/sessions")).sessions;
  renderSessions();
}

function renderSessions() {
  const list = $("session-list");
  list.replaceChildren();
  $("session-count").textContent = state.sessions.length;
  if (!state.sessions.length) list.append(node("p", "no-sessions", "你的研究记录会出现在这里。"));
  for (const session of state.sessions) {
    const button = node("button", "session-item" + (session.id === state.session?.id ? " active" : ""));
    button.type = "button";
    button.append(node("strong", "", session.title), node("small", "", date(session.updated_at, true)));
    button.addEventListener("click", () => selectSession(session.id).catch((error) => notice(error.message)));
    list.append(button);
  }
}

async function selectSession(id) {
  const selection = ++state.selection;
  const session = await api(`/api/sessions/${id}`);
  if (selection !== state.selection) return;
  state.session = session;
  history.replaceState(null, "", "#" + id);
  $("conversation-title").textContent = session.title;
  workspace.classList.remove("show-sidebar");
  notice();
  renderSessions();
  renderConversation();
  if (session.active_job_id) watchJob(session.active_job_id);
}

function inline(element, text) {
  // Build safe DOM nodes: model text is never interpreted as HTML or script.
  const pattern = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    element.append(document.createTextNode(text.slice(cursor, match.index)));
    element.append(node(match[2] ? "strong" : "code", "", match[2] || match[3]));
    cursor = match.index + match[0].length;
  }
  element.append(document.createTextNode(text.slice(cursor)));
}

function markdown(text) {
  const root = node("div", "markdown");
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  const isList = (line) => /^\s*(?:[-*] |\d+[.)] )/.test(line);
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index++; continue; }
    if (/^\s*```/.test(line)) {
      const code = [];
      index++;
      while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);
      const pre = node("pre");
      pre.append(node("code", "", code.join("\n")));
      root.append(pre);
      index++;
      continue;
    }
    if (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || "")) {
      const cells = (value) => value.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
      const wrap = node("div", "table-wrap"), table = node("table"), thead = node("thead"), heading = node("tr");
      for (const cell of cells(line)) { const th = node("th"); inline(th, cell); heading.append(th); }
      thead.append(heading); table.append(thead);
      const tbody = node("tbody"); index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        const row = node("tr");
        for (const cell of cells(lines[index++])) { const td = node("td"); inline(td, cell); row.append(td); }
        tbody.append(row);
      }
      table.append(tbody); wrap.append(table); root.append(wrap); continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) { const h = node("h" + Math.min(heading[1].length + 1, 4)); inline(h, heading[2]); root.append(h); index++; continue; }
    if (isList(line)) {
      const ordered = line.match(/^\s*(\d+)[.)] /);
      const list = node(ordered ? "ol" : "ul");
      if (ordered) list.start = Number(ordered[1]);
      while (index < lines.length && isList(lines[index])) {
        const item = node("li"); inline(item, lines[index++].replace(/^\s*(?:[-*] |\d+[.)] )/, "")); list.append(item);
      }
      root.append(list); continue;
    }
    const paragraph = node("p"); inline(paragraph, line); index++;
    while (index < lines.length && lines[index].trim() && !/^(#{1,4}\s|\s*```)/.test(lines[index]) && !isList(lines[index]) && !(lines[index].includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || ""))) {
      paragraph.append(node("br")); inline(paragraph, lines[index++]);
    }
    root.append(paragraph);
  }
  return root;
}

function showEvidence(result) {
  const content = $("evidence-content"); content.replaceChildren();
  const evidence = result?.evidence || [];
  $("evidence-count").textContent = evidence.length;
  if (!result) {
    const empty = node("div", "evidence-empty");
    empty.append(node("span", "", "▧"), node("p", "", "提问后，查看实际调用的工具、\n查询条件和返回结果。"));
    content.append(empty);
    if (state.status) showSource(state.status.data_source, state.status.snapshot_version);
    return;
  }
  if (result.data_source) showSource(result.data_source, (result.snapshot_versions || []).join("、"), true);
  content.append(node("p", "evidence-caption", "下面是这条回答的实际工具记录。展开可查看参数与结果。"));
  for (const [index, item] of evidence.entries()) {
    const details = node("details", "evidence-item");
    const summary = node("summary", "", `${index + 1}. ${toolLabels[item.tool] || item.tool}`);
    summary.append(node("small", "", item.result?.status === "ok" ? "已返回" : item.result?.status === "unavailable" ? "受限" : "未完成"));
    details.append(summary);
    let argumentsText = item.arguments;
    try { argumentsText = JSON.stringify(JSON.parse(item.arguments), null, 2); } catch { /* Display literal arguments safely. */ }
    details.append(node("div", "evidence-query", `${item.tool}\n参数：${argumentsText}`));
    details.append(node("pre", "", JSON.stringify(item.result, null, 2)));
    content.append(details);
  }
  content.append(node("div", "audit-id", `审计编号\n${result.request_id}\n本次统计检验：${result.statistical_tests_performed ?? 0}`));
}

function revealEvidence(result) {
  showEvidence(result);
  workspace.classList.remove("hide-evidence");
  workspace.classList.add("show-evidence");
}

function progressContent(job) {
  const root = node("div", "progress");
  const event = job?.events?.at(-1);
  root.append(node("span", "", "●"), document.createTextNode(event ? event.text + (event.tool ? ` · ${toolLabels[event.tool] || event.tool}` : "") : "正在准备本次研究…"));
  const trail = node("div", "progress-trail");
  for (const tool of new Set((job?.events || []).filter((event) => event.phase === "tool_complete").map((event) => event.tool))) {
    trail.append(node("small", "", "✓ " + (toolLabels[tool] || tool)));
  }
  root.append(trail);
  return root;
}

function renderConversation() {
  const turns = state.session?.turns || [];
  $("welcome").hidden = turns.length > 0;
  const messages = $("messages"); messages.replaceChildren();
  let latestResult = null;
  for (const turn of turns) {
    const section = node("article", "turn");
    const user = node("div", "user-message"); user.append(node("div", "user-bubble", turn.question));
    section.append(user);
    const row = node("div", "assistant-row");
    row.append(node("div", "assistant-avatar", "S·"));
    const content = node("div", "assistant-content");
    content.append(node("div", "assistant-label", "SOCIALDOG"));
    if (turn.status === "running") {
      const holder = node("div"); holder.id = "progress-" + turn.id;
      holder.append(progressContent(state.jobs.get(turn.id))); content.append(holder);
    } else if (turn.status === "error") {
      content.append(node("div", "answer-error", turn.error));
      const retry = node("button", "retry-button", "重新编辑这个问题 ↗");
      retry.addEventListener("click", () => fillQuestion(turn.question)); content.append(retry);
    } else if (turn.result) {
      latestResult = turn.result;
      content.append(markdown(turn.result.answer));
      const meta = node("div", "answer-meta");
      const evidence = node("button", "evidence-button", `▧ ${turn.result.evidence.length} 条工具依据`);
      evidence.addEventListener("click", () => revealEvidence(turn.result));
      const copy = node("button", "copy-button", "复制回答");
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(turn.result.answer); copy.textContent = "已复制"; }
        catch { notice("浏览器未允许复制，请选中回答文字复制。"); }
      });
      meta.append(evidence, copy, node("span", "meta-version", (turn.result.snapshot_versions || []).join("、")));
      content.append(meta);
    }
    row.append(content); section.append(row); messages.append(section);
  }
  if (turns.at(-1)?.status === "ok") {
    const followups = node("div", "followups");
    for (const question of ["再按星期比较", "说明分析限制"]) {
      const button = node("button", "followup", question);
      button.addEventListener("click", () => fillQuestion(question)); followups.append(button);
    }
    messages.lastElementChild.querySelector(".assistant-content").append(followups);
  }
  showEvidence(latestResult);
  updateSend();
  $("conversation-scroll").scrollTop = $("conversation-scroll").scrollHeight;
}

function updateSend() {
  const busy = state.sending || state.session?.active_job_id;
  $("send").disabled = !state.status?.ready || !!busy || !$("question").value.trim();
  $("send").textContent = busy ? "···" : "↑";
  $("question").setAttribute("aria-busy", busy ? "true" : "false");
  $("new-chat").disabled = state.sending;
  $("session-list").querySelectorAll("button").forEach((button) => { button.disabled = state.sending; });
}

function fillQuestion(question) {
  $("question").value = question;
  resizeInput(); updateSend(); $("question").focus();
}

function resizeInput() {
  $("question").style.height = "auto";
  $("question").style.height = Math.min(160, $("question").scrollHeight) + "px";
}

function watchJob(id) {
  if (state.pollers.has(id)) return;
  const poll = async () => {
    try {
      const job = await api(`/api/jobs/${id}`);
      state.jobs.set(id, job);
      const holder = $("progress-" + id);
      if (holder) holder.replaceChildren(progressContent(job));
      if (job.status !== "running") {
        state.pollers.delete(id);
        await refreshSessions();
        if (state.session?.id === job.session_id) await selectSession(job.session_id);
        return;
      }
      state.pollers.set(id, setTimeout(poll, 900));
    } catch (error) {
      state.pollers.delete(id);
      notice(error.message);
      // A restart marks interrupted turns failed in persistent storage.
      if (state.session) {
        try { await selectSession(state.session.id); } catch { /* Keep the visible conversation. */ }
      }
    }
  };
  state.pollers.set(id, true); poll();
}

$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("question").value.trim();
  if (!question || $("send").disabled) return;
  state.sending = true; updateSend(); notice();
  try {
    if (!state.session) state.session = await api("/api/sessions", {});
    const sessionId = state.session.id;
    const {job_id} = await api(`/api/sessions/${sessionId}/messages`, {question});
    $("question").value = ""; resizeInput();
    await refreshSessions(); await selectSession(sessionId); watchJob(job_id);
  } catch (error) { notice(error.message); }
  finally { state.sending = false; updateSend(); }
});
$("question").addEventListener("input", () => { resizeInput(); updateSend(); });
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("chat-form").requestSubmit(); }
});
document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => fillQuestion(button.dataset.question)));
$("new-chat").addEventListener("click", () => {
  ++state.selection; state.session = null;
  history.replaceState(null, "", location.pathname); $("conversation-title").textContent = "新的研究";
  workspace.classList.remove("show-sidebar");
  renderSessions(); renderConversation(); notice(); fillQuestion("");
});
$("menu-toggle").addEventListener("click", () => workspace.classList.toggle("show-sidebar"));
$("sidebar-close").addEventListener("click", () => workspace.classList.remove("show-sidebar"));
$("evidence-toggle").addEventListener("click", () => {
  if (innerWidth <= 980) workspace.classList.toggle("show-evidence");
  else workspace.classList.toggle("hide-evidence");
});
$("refresh-source").addEventListener("click", async () => {
  $("refresh-source").disabled = true;
  try { await refreshStatus(); } catch (error) { notice(error.message); }
  finally { $("refresh-source").disabled = false; }
});

(async () => {
  try {
    await refreshStatus(); await refreshSessions();
    const id = location.hash.slice(1);
    if (/^[0-9a-f]{32}$/.test(id)) await selectSession(id);
    else renderConversation();
  } catch (error) { notice(error.message); }
})();
