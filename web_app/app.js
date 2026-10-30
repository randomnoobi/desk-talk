/* ============================================
   DESK TALK — computer app
   Left: chats. Center: the open chat or page.
   Right: what the desk camera sees.
   Agents aren't fixed: when the camera sees an object it joins the chat,
   and when it's gone a while it leaves. Without a server it runs as a demo.
   Its numbers (refresh times, limits, the habit rules ...) come from the
   server: GET /api/ui_settings.
   ============================================ */
'use strict';

// ————— Settings from the server —————
// Filled from GET /api/ui_settings when the page starts (and again when the server comes back after a break).
// The demo fills in the few it needs itself (see "Demo" below). The page keeps no copies of its own.
let ui = null;

// How long the first check waits for the server (see firstCheck). It can't be a setting like the others: the
// settings are what it asks the server for. With no answer by then, the page runs the demo.
const FIRST_CHECK_MS = 4000;

// Units, not settings.
const MS = 1000;               // thousandths of a second in a second
const MINUTE = 60;             // seconds
const HOUR = 3600;             // seconds
const DAY_MS = 24 * HOUR * MS;
const PERCENT = 100;

/** How many of something the page shows (settings → web.list_sizes). Missing (an older server): no limit. */
const size = (name) => (ui?.list_sizes || ui?.web?.list_sizes || {})[name];
const maxLength = (n) => (n ? ` maxlength="${n}"` : '');
/** 'pen/pencil' → 'pen': the first of the detector's words for a thing. */
const plainLabel = (label) => String(label || '').split('/')[0].trim();

// ————— Who can talk —————
const GROUP = {
  id: 'group', name: 'The desk', art: 'art/desk.svg', color: 'ink',
  about: 'Everyone on your desk, talking to you and to each other.',
  empty: 'Nobody here yet. When the camera sees something on your desk, it joins the chat. Type @ to talk to one of them.',
};
// Lens is the camera. It only talks in the group chat, about the camera view (and fills in for missing agents).
const lens = () => ui.lens_card;
const known = (c) => ui.web.palette.includes(c);
const colorVar = (c) => `var(--${known(c) ? (c === 'coral' ? 'coral-ink' : c) : 'plum'})`;
const tintVar = (c) => (c === 'ink' ? 'var(--mist)' : `var(--${known(c) ? c : 'plum'}-tint)`);

// ————— Small helpers —————
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const todayKey = () => new Date().toLocaleDateString('en-CA');

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function clock(tsSeconds) {
  return new Date(tsSeconds * MS).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}
function agoText(tsSeconds) {
  if (!tsSeconds) return '';
  const s = Math.max(0, Math.floor(Date.now() / MS - tsSeconds));
  if (s < MINUTE) return 'just now';
  if (s < HOUR) return `${Math.floor(s / MINUTE)} min ago`;
  return `${Math.floor(s / HOUR)} h ago`;
}
const mins = (sec) => Math.round((sec || 0) / MINUTE);
const liters = (ml) => `${((ml || 0) / MS).toFixed(1)} L`;
/** 60 → 'an hour', 120 → '2 hours', 45 → '45 minutes'. */
function duration(minutes) {
  if (minutes === MINUTE) return 'an hour';
  if (minutes > MINUTE && minutes % MINUTE === 0) return `${minutes / MINUTE} hours`;
  return `${minutes} minutes`;
}
/** '2026-10-30' → that day at midnight, here. */
function localDay(day) {
  const [y, m, d] = String(day).split('-').map(Number);
  return new Date(y, m - 1, d);
}
/** Whole days from today to `day` ('YYYY-MM-DD'): 0 today, 1 tomorrow, -1 yesterday. */
function daysFromToday(day) {
  return Math.round((localDay(day) - localDay(todayKey())) / DAY_MS);
}
const dayName = (day) => localDay(day).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });

/** fetch → JSON, with a timeout (an ordinary request's, from the settings). Throws on network errors and non-2xx
 * (then the error's `status` is the reply's status). The first check, which fetches the settings, passes its own
 * (FIRST_CHECK_MS). */
async function api(path, { method = 'GET', body, timeout = ui?.web?.request_timeout_ms } = {}) {
  const ctl = new AbortController();
  const t = timeout ? setTimeout(() => ctl.abort(), timeout) : null;
  try {
    const res = await fetch(path, {
      method,
      signal: ctl.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw Object.assign(new Error(`${path} → ${res.status}`), { status: res.status });
    return res.status === 204 ? null : await res.json();
  } finally {
    clearTimeout(t);
  }
}

// localStorage can throw (private windows, blocked storage) — never let that break the app.
const storage = {
  load(key, fallback) {
    try { const v = JSON.parse(localStorage.getItem(key)); return v ?? fallback; } catch { return fallback; }
  },
  save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
  },
};

/** Put `html` in `box`, unless it's already there. With `poll` (a timed refresh, not something you did), also
 * leave it alone while you're typing in it: one of its boxes has the focus or some text. True when it was drawn. */
const drawn = new WeakMap();
function paint(box, html, poll = false) {
  if (drawn.get(box) === html) return false;
  if (poll && $$('input:not([type="checkbox"]):not([type="range"]), select', box)
    .some((i) => i === document.activeElement || (i.tagName === 'INPUT' && i.value))) return false;
  box.innerHTML = html;
  drawn.set(box, html);
  return true;
}

// ————— State —————
const state = {
  live: false,          // the Python server answered when the page opened (otherwise: the demo)
  offline: false,       // ...but can't be reached right now
  cameraSource: 'pi',   // pi | phone | webcam
  camOk: false,         // pictures are arriving right now
  camNoted: false,      // what the last "Camera connected / disconnected" note said...
  camSince: 0,          // ...and since when (ms) camOk has been what it is now
  camIndex: 0,
  feed: 'analysis',     // 'analysis' (boxes) or 'clean'
  phone: null,          // { url, qr_svg } for phone pairing
  piCommand: '',        // the command to run on the Pi
  view: 'chat',
  chat: 'group',
  agents: {},           // agent id → profile + on_desk / joined_at / left_at
  messages: { group: [] },
  unread: { group: 0 },
  waiting: {},          // chat id → replies still on their way (the typing dots stay until they're in)
  typing: null,         // { char, chat }: whose typing dots show
  habits: null,         // /api/habits/today
  habitsFailed: false,  // the last time the page asked for them, it got no answer
  logFilter: 'all',
  logDay: null,         // the day the Event log shows; null = today (the server's)
  logDays: null,        // GET /api/log/days: { days, today }
  snapshotIds: null,
  seen: new Set(),      // message ids already shown (replies can arrive twice)
  aiText: '',
};

const agentIds = () => Object.keys(state.agents);
/** Who a chat or message belongs to: the group, Lens, or an agent. */
function who(id) {
  if (id === 'group') return GROUP;
  if (id === 'lens') return lens();
  return state.agents[id] || { id, name: id, color: 'plum', icon: '✨', about: '' };
}
function ensureChat(id) {
  if (!state.messages[id]) state.messages[id] = [];
  if (state.unread[id] == null) state.unread[id] = 0;
}

/** The agent's drawing, or a round badge with its emoji. */
function avatar(a, size = 36, cls = '') {
  if (a.art) return `<img class="av ${cls}" src="${esc(a.art)}" alt="" width="${size}" height="${size}">`;
  return `<span class="av av-gen ${cls}" style="--av-bg:${tintVar(a.color)};--av-fg:${colorVar(a.color)};width:${size}px;height:${size}px;font-size:${Math.round(size * 0.5)}px" aria-hidden="true">${esc(a.icon || (a.name || '?')[0])}</span>`;
}

// ————— Chat history (kept in this browser so a refresh doesn't wipe the chats) —————
const HISTORY_KEY = 'desktalk_chats_v2';
const CLEARED_KEY = 'desktalk_chats_cleared_v1';    // when you last pressed "Clear all chats" (ms)
let historyTimer = null;
function saveHistory() {
  if (!state.live) return;            // the demo never saves
  clearTimeout(historyTimer);
  historyTimer = setTimeout(() => {
    const chats = {};
    for (const [id, list] of Object.entries(state.messages)) chats[id] = list.filter((m) => !m.note).slice(-ui.web.chat_saved);
    const agents = {};
    for (const [id, a] of Object.entries(state.agents)) agents[id] = { ...a, on_desk: false };
    storage.save(HISTORY_KEY, { saved: Date.now(), chats, agents });
  }, 400);
}
function loadHistory() {
  const d = storage.load(HISTORY_KEY, null);
  if (!d?.chats) return;
  for (const [id, a] of Object.entries(d.agents || {})) if (a && a.name) state.agents[id] = { ...a, on_desk: false };
  for (const [id, list] of Object.entries(d.chats)) {
    if (!Array.isArray(list)) continue;
    ensureChat(id);
    state.messages[id] = list.filter((m) => m && typeof m.text === 'string');
  }
}

// ————— To-do list (Book's panel). The server keeps it (GET /api/todos) and its copy wins; this browser keeps
// one too, sent to the server only while the server has none yet. —————
const TODO_KEY = 'desktalk_todo_v1';
let todo = null;
// Where the list stands with the server:
//   'server'  it keeps one, and its copy wins (ours is never sent over it)
//   'none'    it has none yet: ours is sent once
//   'old'     an older server with no GET /api/todos (it said 404): ours is sent, as before
//   'unknown' the last ask failed (no answer, or a server error): nothing is sent, and the page asks again later
let todoOnServer = 'unknown';
const SEND_OURS = ['none', 'old'];
const cleanTodo = (i) => ({ text: String(i?.text ?? ''), done: !!i?.done, due: typeof i?.due === 'string' && i.due ? i.due : null });
function loadTodoCopy() {
  const saved = storage.load(TODO_KEY, null);
  todo = Array.isArray(saved?.items)
    ? { type: 'todo', items: saved.items.map(cleanTodo) }
    : { type: 'todo', items: ui.starter_todo ? [cleanTodo({ text: ui.starter_todo })] : [] };
}
const todoAgents = () => agentIds().filter((id) => state.agents[id].tracker === 'todo');
function todoChanged(id) {
  storage.save(TODO_KEY, todo);
  if (state.view === 'chat' && state.chat === id) renderTracker();
  if (state.live) {
    api('/api/chat_todos', { method: 'POST', body: { chat: id, widget: todo } })
      .then(() => { if (todoOnServer === 'none') todoOnServer = 'server'; }, () => {});
  }
}
/** Send this browser's list, only when the server has none (or is too old to keep one): its copy wins. */
function syncTodos() {
  if (!state.live || !SEND_OURS.includes(todoOnServer)) return;
  const byChat = Object.fromEntries(todoAgents().map((id) => [id, todo]));
  if (!Object.keys(byChat).length) return;
  api('/api/chat_todos_sync', { method: 'POST', body: { by_chat: byChat } })
    .then(() => { if (todoOnServer === 'none') todoOnServer = 'server'; }, () => {});
}
/** The server's list, when it has one; otherwise this browser's goes to it. When the ask fails, nothing is sent
 * (a passing error must never wipe the server's list); refreshHabits asks again. */
async function loadTodos() {
  if (!state.live) return;
  let r;
  try {
    r = await api('/api/todos');
  } catch (err) {
    todoOnServer = err?.status === 404 ? 'old' : 'unknown';
    syncTodos();
    return;
  }
  if (r?.has_list && Array.isArray(r.items)) {
    todoOnServer = 'server';
    todo.items = r.items.map(cleanTodo);
    storage.save(TODO_KEY, todo);
    if (state.view === 'chat' && who(state.chat).tracker === 'todo') renderTracker(true);
  } else {
    todoOnServer = 'none';
    syncTodos();
  }
}
/** 'due today', 'was due 2 days ago', 'due Fri, Oct 30' ... and whether it's late. */
function dueText(item) {
  if (!item.due) return null;
  const n = daysFromToday(item.due);
  if (Number.isNaN(n)) return null;
  if (n === 0) return { text: 'due today', late: false };
  if (n === 1) return { text: 'due tomorrow', late: false };
  if (n === -1) return { text: 'was due yesterday', late: !item.done };
  if (n < 0) return { text: `was due ${-n} days ago`, late: !item.done };
  return { text: `due ${dayName(item.due)}`, late: false };
}

// ————— Your own counters (Habits page) —————
const HABIT_KEY = 'desktalk_habits_v2';
// The starter counters an older version of the page added (posture and nail biting are gone). A browser that
// saved them loses these two once (`tidied`), and keeps every counter you added; one you add again later stays.
const OLD_STARTER_COUNTERS = ['Nail biting', 'Slouching'];
let counters = null;
function loadCounters() {
  const saved = storage.load(HABIT_KEY, null);
  const items = Array.isArray(saved?.items) ? saved.items : null;
  const kept = items && !saved.tidied ? items.filter((i) => !OLD_STARTER_COUNTERS.includes(i.label)) : items;
  if (kept && saved.day === todayKey()) counters = { day: saved.day, items: kept, tidied: true };
  else {
    const labels = kept ? kept.map((i) => i.label) : [...(ui.starter_counters || [])];
    counters = { day: todayKey(), items: labels.map((label) => ({ label, count: 0 })), tidied: true };
  }
  if (items && !saved.tidied) saveCounters();
}
const saveCounters = () => storage.save(HABIT_KEY, counters);
/** They start again from 0 each day (also when the page stays open past midnight). */
function countersForToday() {
  if (counters.day === todayKey()) return;
  counters = { day: todayKey(), items: counters.items.map((c) => ({ label: c.label, count: 0 })), tidied: true };
  saveCounters();
}

// ————— Agents joining and leaving —————
function upsertAgent(a) {
  if (!a || !a.id) return;
  state.agents[a.id] = { ...(state.agents[a.id] || {}), ...a };
  ensureChat(a.id);
}
/** Reconcile with the server's list (asked for every web.refresh_ms.live) so a missed event can't leave it stale. */
function syncAgents(list) {
  if (!Array.isArray(list)) return;
  const onDesk = new Set(list.filter((a) => a.on_desk).map((a) => a.id));
  let changed = false;
  for (const a of list) {
    const before = state.agents[a.id];
    if (!before || before.on_desk !== a.on_desk || before.name !== a.name) changed = true;
    upsertAgent(a);
  }
  for (const id of agentIds()) {
    if (state.agents[id].on_desk && !onDesk.has(id)) { state.agents[id].on_desk = false; changed = true; }
  }
  if (changed) { renderChatList(); if (state.view === 'chat') renderChatHead(); }
}
function rosterEvent(d) {
  const joined = d.action === 'joined';
  const when = d.ts || Date.now() / MS;
  upsertAgent({ ...d.agent, on_desk: joined, ...(joined ? { joined_at: when } : { left_at: when }) });
  if (!d.quiet) {
    addMessage('group', { id: d.id, time: d.ts ? d.ts * MS : Date.now(), system: joined ? 'joined' : 'left', agent: d.agent.id, text: d.text || `${d.agent.name} ${joined ? 'joined' : 'left'} the chat` });
  }
  if (joined && d.agent.tracker === 'todo') loadTodos();
  renderChatList();
  if (state.view === 'chat' && state.chat === d.agent.id) renderChatHead();
}

// ————— Sidebar —————
/** The group, then agents on the desk, then ones that left a short while ago (web.left_chat_shown_min) or that
 * you chatted with. */
function chatOrder() {
  const leftLately = (a) => a.left_at && a.left_at > Date.now() / MS - ui.web.left_chat_shown_min * MINUTE;
  const ids = agentIds().filter((id) => {
    const a = state.agents[id];
    return a.on_desk || (state.messages[id] || []).length || leftLately(a);
  });
  ids.sort((x, y) => (state.agents[y].on_desk - state.agents[x].on_desk)
    || ((state.agents[y].joined_at || 0) - (state.agents[x].joined_at || 0)));
  return ['group', ...ids];
}

function renderChatList() {
  paint($('#chat-list'), chatOrder().map((id) => {
    const c = who(id);
    const msgs = state.messages[id] || [];
    const last = msgs.findLast((m) => !m.note);
    let preview = id === 'group' ? `${agentIds().filter((a) => state.agents[a].on_desk).length} on the desk` : (c.about || '').split('.')[0];
    if (last?.mine) preview = `You: ${last.text}`;
    else if (last?.system) preview = last.text;
    else if (last) preview = id === 'group' ? `${who(last.char).name}: ${last.text}` : last.text;
    const unread = state.unread[id] || 0;
    const current = state.view === 'chat' && state.chat === id;
    const away = id !== 'group' && !c.on_desk;
    return `<button type="button" class="chat-link${away ? ' away' : ''}" data-chat="${esc(id)}" aria-current="${current}">
      <span class="chat-link-art">${avatar(c, 44)}${id !== 'group' && c.on_desk ? '<i class="here" title="On the desk"></i>' : ''}</span>
      <span class="chat-link-text"><span class="chat-link-name">${esc(c.name)}${away ? ' <small>left</small>' : ''}</span>
        <span class="chat-link-preview">${esc(preview)}</span></span>
      ${unread ? `<span class="badge" aria-label="${unread} unread">${unread > 9 ? '9+' : unread}</span>` : '<span></span>'}
    </button>`;
  }).join(''));
}

function renderPageLinks() {
  $$('.page-link').forEach((b) => b.setAttribute('aria-current', String(state.view === b.dataset.view)));
}

function renderConn() {
  const el = $('#conn');
  let st = 'demo';
  let text = 'Demo mode';
  if (state.live && state.offline) {
    st = 'waiting';
    text = 'Offline';
  } else if (state.live) {
    const name = { pi: 'Pi camera', phone: 'Phone camera', webcam: 'Webcam' }[state.cameraSource] || 'Camera';
    st = state.camOk ? 'live' : 'waiting';
    text = state.camOk ? `${name} on` : (state.cameraSource === 'phone' ? 'Connect your phone' : `${name} off`);
  }
  el.dataset.state = st;
  $('#conn-text').textContent = text;
  el.title = st === 'demo' ? DEMO_HOW_TO_RUN : '';
}

// ————— Navigation —————
function renderChatHead() {
  const c = who(state.chat);
  $('#chat-art').innerHTML = avatar(c, 56);
  $('#chat-name').textContent = c.name;
  const status = state.chat === 'group' ? '' : (c.on_desk ? ' On the desk now.' : ' Not on the desk right now.');
  $('#chat-about').textContent = (c.about || '') + status;
  $('#input').placeholder = state.chat === 'group' ? 'Message the desk' : `Message ${c.name}`;
}

function openChat(id) {
  ensureChat(id);
  state.view = 'chat';
  state.chat = id;
  state.unread[id] = 0;
  $$('.view').forEach((v) => { v.hidden = v.id !== 'view-chat'; });
  renderChatHead();
  renderTracker();
  renderMessages();
  renderChatList();
  renderPageLinks();
  $('#input').focus();
  if (state.live && id !== 'group') api('/api/widget_event', { method: 'POST', body: { chat: id, action: 'view' } }).catch(() => {});
  if (!state.live && id === 'group') playDemoOnce();
}

function openPage(view) {
  state.view = view;
  $$('.view').forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  renderChatList();
  renderPageLinks();
  if (view === 'habits') { renderHabits(); if (state.live) refreshHabits(); }
  if (view === 'log') { state.logDays = null; renderLog(); }
  if (view === 'captures') renderCaptures();
  if (view === 'settings') renderSettings();
}

// ————— Messages —————
function mentionWords() {
  const words = {};
  for (const [id, a] of Object.entries(state.agents)) {
    for (const w of [id, a.name, a.nickname, a.object, ...(a.labels || [])]) {
      if (w) words[String(w).toLowerCase().split('/')[0].trim()] = id;
    }
  }
  return words;
}

function formatText(text) {
  let s = esc(text);
  s = s.replace(/@user\b/g, '<span class="mention-you">@you</span>');
  const words = mentionWords();
  s = s.replace(/@(\w+)/g, (m, word) => {
    const id = words[word.toLowerCase()];
    if (!id) return m;
    return `<span class="mention" style="--mention-color:${colorVar(state.agents[id].color)}">@${esc(state.agents[id].name)}</span>`;
  });
  return s.replace(/\n/g, '<br>');
}

function messageHtml(m, chatId) {
  if (m.note) return `<p class="note">${esc(m.text)}</p>`;
  if (m.system) {
    const a = who(m.agent);
    return `<p class="note system ${m.system}">${avatar(a, 20)}<span>${esc(m.text)}</span><time>${m.time ? new Date(m.time).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''}</time></p>`;
  }
  if (m.mine) return `<div class="msg mine"><div class="msg-body"><div class="bubble">${formatText(m.text)}</div></div></div>`;
  const c = who(m.char);
  const quote = m.replyTo && m.replyTo.text
    ? `<div class="quote" style="--quote-color:${colorVar(who(m.replyTo.character).color)}">${esc(who(m.replyTo.character).name)}: ${esc(m.replyTo.text.slice(0, size('quote_chars')))}</div>`
    : '';
  // Photos live on the server; after a restart old ones may be gone, so hide broken ones.
  const photo = m.snapshot && state.live
    ? `<button type="button" class="photo-btn" data-photo="/api/snapshot/${esc(m.snapshot)}" aria-label="Open the photo from the desk cam"><img src="/api/snapshot/${esc(m.snapshot)}" alt="Photo from the desk cam" loading="lazy" onerror="this.parentElement.remove()"></button>`
    : '';
  const cta = m.cta && m.cta.action === 'captures'
    ? `<button type="button" class="btn-quiet msg-cta" data-open="captures">${esc(m.cta.label || 'Desk captures')}</button>`
    : '';
  const tag = m.habit ? `<span class="msg-tag">${esc(HABIT_NAMES[m.habit] || m.habit)}</span>` : '';
  return `<div class="msg" style="--tint:${tintVar(c.color)}">
    <span class="msg-art">${avatar(c, 36)}</span>
    <div class="msg-body">
      ${chatId === 'group' ? `<span class="msg-name" style="color:${colorVar(c.color)}">${esc(c.name)}${tag}</span>` : ''}
      ${quote}
      <div class="bubble">${formatText(m.text)}${photo}</div>
      ${cta}
    </div>
  </div>`;
}

function renderMessages() {
  const box = $('#messages');
  const list = state.messages[state.chat] || [];
  const c = who(state.chat);
  const empty = list.some((m) => !m.note) || state.typing?.chat === state.chat
    ? ''
    : `<div class="empty">${avatar(c, 132)}<p>${esc(c.empty || `${c.name} hasn't said anything yet. Say hi!`)}</p></div>`;
  box.innerHTML = list.map((m) => messageHtml(m, state.chat)).join('') + empty;
  drawTyping();
  box.scrollTop = box.scrollHeight;
}

function addMessage(chatId, msg) {
  if (msg.id) {
    if (state.seen.has(msg.id)) return;     // already shown (came over /api/events too)
    state.seen.add(msg.id);
  }
  ensureChat(chatId);
  const list = state.messages[chatId];
  list.push({ ...msg, time: msg.time || Date.now() });
  if (list.length > ui.web.chat_kept) list.splice(0, list.length - ui.web.chat_kept);
  const visible = state.view === 'chat' && state.chat === chatId;
  if (visible) {
    const box = $('#messages');
    if (!msg.note) box.querySelector('.empty')?.remove();
    const html = messageHtml(list.at(-1), chatId);
    const dots = $('#typing');                // someone is still typing: their dots stay at the bottom
    if (dots) dots.insertAdjacentHTML('beforebegin', html);
    else box.insertAdjacentHTML('beforeend', html);
    box.scrollTop = box.scrollHeight;
  } else if (!msg.mine && !msg.note) {
    state.unread[chatId] += 1;
  }
  if (!msg.mine && !msg.note && !msg.system && !visible && (msg.toast || msg.text.includes('@user'))) {
    toast(who(msg.char), msg.text.replace(/@user/g, '@you'));
  }
  renderChatList();
  if (!msg.note) saveHistory();
}

/** Typing dots for `charId` in `chatId`, until hideTyping (when that reply is in). */
function showTyping(charId, chatId) {
  state.typing = { char: charId, chat: chatId };
  if (state.view === 'chat' && state.chat === chatId) $('#messages').querySelector('.empty')?.remove();
  drawTyping();
}
function drawTyping() {
  $('#typing')?.remove();
  const t = state.typing;
  if (!t || state.view !== 'chat' || state.chat !== t.chat) return;
  const c = who(t.char);
  const box = $('#messages');
  box.insertAdjacentHTML('beforeend', `<div class="msg" id="typing" style="--tint:${tintVar(c.color)}">
    <span class="msg-art">${avatar(c, 36)}</span>
    <div class="bubble typing" aria-label="${esc(c.name)} is typing"><i></i><i></i><i></i></div></div>`);
  box.scrollTop = box.scrollHeight;
}
function hideTyping(chatId) {
  if (chatId && state.typing?.chat !== chatId) return;
  state.typing = null;
  $('#typing')?.remove();
}

let toastTimer = null;
function toast(character, text) {
  const el = $('#toast');
  el.innerHTML = `${character ? avatar(character, 32) : ''}<span>${esc(text.slice(0, size('toast_chars')))}</span>`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ui.web.toast_ms);
}

// ————— Sending —————
/** The messages before the one just sent (at most chat.history_from_app of them). */
function historyFor(chatId) {
  const earlier = (state.messages[chatId] || []).filter((m) => !m.note && !m.system).slice(0, -1);
  return earlier.slice(earlier.length - ui.chat.history_from_app).map((m) => ({
    role: m.mine ? 'user' : 'assistant',
    name: m.mine ? undefined : who(m.char).name,
    text: m.text.slice(0, ui.chat.message_max_chars),
  }));
}

async function send(text) {
  const chatId = state.chat;
  addMessage(chatId, { mine: true, text });
  if (!state.live) return demoReply(chatId, text);
  state.waiting[chatId] = (state.waiting[chatId] || 0) + 1;
  showTyping(chatId === 'group' ? 'group' : chatId, chatId);
  let res = null;
  try {
    res = await api('/api/chat', {
      method: 'POST',
      timeout: ui.chat_reply_timeout_ms,      // as long as the server may keep trying the AI, up to web.chat_reply_max_ms
      body: { chat: chatId, message: text, recent_messages: historyFor(chatId), widget: who(chatId).tracker === 'todo' ? todo : undefined },
    });
  } catch {
    res = null;
  } finally {
    state.waiting[chatId] -= 1;
    if (!state.waiting[chatId]) hideTyping(chatId);
  }
  if (!res) {
    addMessage(chatId, { note: true, text: 'Couldn’t reach Desk Talk. Is it still running on this computer?' });
    return;
  }
  for (const r of res.messages || []) {
    addMessage(chatId, { id: r.id, char: r.character || chatId, text: r.text, replyTo: r.reply_to, cta: r.cta });
  }
  if (res.actions?.length && chatId !== 'group') applyTodoActions(chatId, res.actions);
  if (res.habits_changed) refreshHabits();
  if (res.error && !(res.messages || []).length) addMessage(chatId, { note: true, text: res.error });
}

/** The AI can change the to-do list (habit counts are changed on the server). */
function applyTodoActions(id, actions) {
  let changed = false;
  for (const a of actions) {
    const text = (a.text || '').toLowerCase();
    if (a.action === 'add_todo' && a.text && todo.items.length < ui.chat.todo_max_items) {
      todo.items.push(cleanTodo({ text: a.text.slice(0, ui.chat.todo_max_chars), due: a.due })); changed = true;
    } else if (a.action === 'done_todo' && text) {
      const item = todo.items.find((i) => !i.done && i.text.toLowerCase().includes(text));
      if (item) { item.done = true; changed = true; }
    } else if (a.action === 'del_todo' && text) {
      const i = todo.items.findIndex((x) => x.text.toLowerCase().includes(text));
      if (i >= 0) { todo.items.splice(i, 1); changed = true; }
    }
  }
  if (changed) todoChanged(id);
}

// ————— @mentions —————
let mentionStart = -1;
let mentionIndex = 0;

function mentionMatches(query) {
  const q = query.toLowerCase();
  const ids = chatOrder().slice(1);
  return ids.filter((id) => {
    const a = state.agents[id];
    return !q || [a.name, a.nickname, a.object, ...(a.labels || [])].some((w) => w && String(w).toLowerCase().startsWith(q));
  });
}

function updateMentions() {
  const input = $('#input');
  const before = input.value.slice(0, input.selectionStart);
  const at = before.lastIndexOf('@');
  const query = at >= 0 ? before.slice(at + 1) : '';
  const box = $('#mentions');
  // Longer than mention_max_chars after an @ and you're not typing a name any more.
  if (at < 0 || (at > 0 && before[at - 1] !== ' ') || /\s/.test(query) || query.length > size('mention_max_chars')) {
    box.hidden = true; mentionStart = -1; return;
  }
  const ids = mentionMatches(query);
  if (!ids.length) { box.hidden = true; mentionStart = -1; return; }
  mentionStart = at;
  mentionIndex = Math.min(mentionIndex, ids.length - 1);
  box.innerHTML = ids.map((id, i) => `<button type="button" class="mention-opt" role="option" data-id="${esc(id)}" aria-selected="${i === mentionIndex}">
      ${avatar(state.agents[id], 30)}${esc(state.agents[id].name)}${state.agents[id].on_desk ? '<small>on the desk</small>' : ''}</button>`).join('');
  box.hidden = false;
}

function insertMention(id) {
  const input = $('#input');
  const after = input.value.slice(input.selectionStart);
  const word = `@${state.agents[id].name.toLowerCase().split(' ')[0]} `;
  input.value = input.value.slice(0, mentionStart) + word + after;
  const pos = mentionStart + word.length;
  input.setSelectionRange(pos, pos);
  $('#mentions').hidden = true;
  mentionStart = -1;
  input.focus();
}

// ————— Habits data (from the server's habit tracker) —————
const HABIT_NAMES = { hydration: 'Water', breaks: 'Breaks', light: 'Light', phone_free: 'Phone-free', screens: 'Screens', food: 'Food', tasks: 'To-dos' };
let habitsTimer = null;
async function refreshHabits() {
  if (state.live && todoOnServer === 'unknown') loadTodos();      // the last ask for the to-do list failed
  if (!state.live) { state.habits = state.habits || demoHabits(); }
  else {
    try {
      state.habits = await api('/api/habits/today');
      state.habitsFailed = false;
    } catch {
      state.habitsFailed = true;
      if (state.view === 'habits') renderHabits(true);
      return;
    }
  }
  if (state.view === 'chat' && who(state.chat).tracker) renderTracker(true);
  if (state.view === 'habits') renderHabits(true);
}
function refreshHabitsSoon() {
  clearTimeout(habitsTimer);
  habitsTimer = setTimeout(refreshHabits, 300);
}
async function logHabit(habit, action, extra = {}) {
  if (!state.live) {
    demoLog(habit, action, extra);
    if (state.view === 'chat') renderTracker();
    if (state.view === 'habits') renderHabits();
    return;
  }
  try {
    state.habits = await api('/api/habits/log', { method: 'POST', body: { habit, action, ...extra } });
    state.habitsFailed = false;
  } catch {
    toast(null, 'Couldn’t save that. Is Desk Talk still running?');
    return;
  }
  if (state.view === 'chat') renderTracker();
  if (state.view === 'habits') renderHabits();
}
/** A button with data-log="habit:action" or "habit:action:minutes". */
function logFromButton(btn) {
  const [habit, action, minutes] = btn.dataset.log.split(':');
  logHabit(habit, action, minutes ? { minutes: +minutes } : {});
}

function bar(value, goal, pace, what) {
  const pct = Math.max(0, Math.min(PERCENT, (value / Math.max(1, goal)) * PERCENT));
  const pacePct = pace != null ? Math.max(0, Math.min(PERCENT, (pace / Math.max(1, goal)) * PERCENT)) : null;
  return `<div class="bar" role="img" aria-label="${esc(what)}: ${Math.round(pct)} percent of the way"><i style="width:${pct}%"></i>${pacePct != null ? `<b style="left:${pacePct}%" title="Where the pace says you should be now"></b>` : ''}</div>`;
}

// ————— Trackers (panel on top of an agent's 1-on-1 chat) —————
// Drawn again when the habit numbers come in (`poll`): then a panel you're typing in is left alone.
// The buttons are wired once, in wirePanels().
const PANELS = ['todo', 'hydration', 'breaks', 'food', 'phone_free'];
/** The panel's html, or null while the habit numbers it shows haven't come in yet. */
function trackerHtml(kind, h) {
  if (kind === 'todo') {
    const items = todo.items.map((item, i) => {
      const due = dueText(item);
      return `<li class="${due?.late ? 'late' : ''}">
        <label><input type="checkbox" data-todo="${i}" ${item.done ? 'checked' : ''}><span class="${item.done ? 'done' : ''}">${esc(item.text)}</span></label>
        ${due ? `<span class="due">${esc(due.text)}</span>` : ''}
        <button type="button" class="x" data-todo-del="${i}" aria-label="Remove ${esc(item.text)}">×</button></li>`;
    }).join('');
    return `<ul class="todo">${items}</ul>
      <form class="inline-add" id="todo-add"><input type="text"${maxLength(ui.chat.todo_max_chars)} placeholder="Add a task" aria-label="New task">
        <input type="date" class="due-box" aria-label="Due date (optional)" title="Due date (optional)"><button type="submit" class="btn-quiet">Add</button></form>
      <p class="tracker-note">Give a task a date and Book reminds you the day before, on the day, and while it’s late.</p>`;
  }
  if (!h) return null;

  if (kind === 'hydration') {
    const w = h.hydration;
    return `<div class="tracker-row">
        <div class="stat"><strong>${liters(w.water_ml)}</strong><span>of ${liters(w.goal_ml)} today · ${w.sips} sips</span></div>
        <div class="tracker-grow">${bar(w.water_ml, w.goal_ml, w.pace_ml, 'Water today')}<span class="tracker-note">Pace for now: ${liters(w.pace_ml)}</span></div>
        <button type="button" class="btn-quiet" data-log="hydration:sip">+ Sip</button>
      </div>
      <p class="tracker-note">${w.last_sip_ts ? `Last sip ${agoText(w.last_sip_ts)}. ` : ''}A sip counts when you lift the cup off the desk and put it back.</p>`;
  }

  if (kind === 'breaks') {
    const b = h.breaks;
    return `<div class="tracker-row">
        <div class="tracker-stats">
          <div class="stat"><strong>${b.count}</strong><span>breaks today</span></div>
          <div class="stat"><strong>${mins(b.sitting_sec)}</strong><span>min sitting now (of ${b.sit_limit_min})</span></div>
        </div>
        <button type="button" class="btn-quiet" data-log="breaks:break">Log a break</button>
      </div>
      <p class="tracker-note">${b.last_break_ts ? `Last break ${agoText(b.last_break_ts)}. ` : ''}Breaks log themselves when you leave the desk for ${ui.habits.breaks.break_min} minutes. <button type="button" class="link" data-open="habits">Habits</button></p>`;
  }

  if (kind === 'food') {
    const entries = h.food.entries || [];
    return `<div class="pills">${entries.map((e, i) => `<span class="pill ${e.healthy ? 'healthy' : 'treat'}">${esc(e.label)}<button type="button" data-food-del="${i}" aria-label="Remove ${esc(e.label)}">×</button></span>`).join('') || '<span class="muted">Nothing logged yet today.</span>'}</div>
      <form class="inline-add" id="food-add"><input type="text"${maxLength(ui.habits.food_label_max_chars)} placeholder="What did you eat?" aria-label="Food name">
        <button type="submit" class="btn-quiet">Healthy</button><button type="button" class="btn-quiet" data-treat>Treat</button></form>
      <p class="tracker-note">Type what you ate, then say if it was healthy or a treat.</p>`;
  }

  const p = h.phone_free;
  return p.counted ? `<div class="tracker-row">
        <div class="stat"><strong>${mins(p.phone_free_sec)} min</strong><span>phone-free desk time (goal ${p.goal_min})</span></div>
        <div class="tracker-grow">${bar(mins(p.phone_free_sec), p.goal_min, null, 'Phone-free time today')}</div>
        <div class="stat"><strong>${mins(p.streak_sec)}</strong><span>min streak</span></div>
      </div>
      <p class="tracker-note">${p.phone_on_desk ? 'The phone is on the desk right now.' : 'No phone on the desk right now.'} Keep it out of sight while you focus.</p>`
    : '<p class="tracker-note">The phone is the camera right now, so phone-free time isn’t counted.</p>';
}

function renderTracker(poll = false) {
  const box = $('#tracker');
  const id = state.chat;
  const a = who(id);
  const kind = id === 'group' ? null : a.tracker;
  if (!PANELS.includes(kind)) { box.hidden = true; return; }      // no panel (the light panel went with Lamp)
  const html = trackerHtml(kind, state.habits);
  box.hidden = false;
  box.style.setProperty('--tint', tintVar(a.color));
  if (html === null) { paint(box, '<p class="muted">Loading…</p>'); refreshHabits(); return; }
  paint(box, html, poll);
}

/** 'sunlight within an hour of waking (10 min clear, 20 cloudy, 30 overcast)', from protocols.yaml → light. */
function morningLight(extra = '') {
  const m = ui.habits.morning_minutes;
  return `sunlight within ${duration(ui.habits.morning_window_min)} of waking (${m.clear} min clear, ${m.partly_cloudy} cloudy, ${m.overcast} overcast${extra})`;
}

// ————— Habits page —————
const SKIES = [['clear', 'Clear sky'], ['partly_cloudy', 'Cloudy'], ['overcast', 'Overcast']];
function renderHabits(poll = false) {
  const page = $('#view-habits');
  const head = '<header><h1 class="page-title">Habits</h1>';
  const h = state.live ? state.habits : (state.habits || demoHabits());
  if (!h || state.habitsFailed) {
    paint(page, `${head}</header>${state.habitsFailed
      ? '<p class="muted">Couldn’t load today’s habits. <button type="button" class="link" data-retry="habits">Try again</button></p>'
      : '<p class="muted">Loading…</p>'}`, poll);
    return;
  }
  countersForToday();
  const w = h.hydration; const b = h.breaks; const p = h.phone_free; const l = h.light;
  const rules = ui.habits;
  const sunButtons = SKIES.map(([sky, name]) => {
    const n = rules.morning_minutes[sky];
    return `<button type="button" class="btn-quiet" data-log="light:sunlight:${n}">${name}, ${n} min</button>`;
  }).join('');
  paint(page, `${head}
      <p class="page-intro">Four everyday habits, partly checked by the desk camera. Your day: up at ${clock(h.times.wake_ts)}, bed at ${clock(h.times.bed_ts)} (<button type="button" class="link" data-open="settings">change</button>).</p></header>
    <section class="protocols">
      <article class="habit-card" style="--tint:var(--teal-tint)">
        <h2>Water</h2>
        <p class="big">${liters(w.water_ml)} <small>of ${liters(w.goal_ml)}</small></p>
        ${bar(w.water_ml, w.goal_ml, w.pace_ml, 'Water today')}
        <p class="hint">${w.sips} sips. Pace for now: ${liters(w.pace_ml)}. About ${liters(w.on_waking_ml)} right after waking, then a glass about every waking hour; less in the ${duration(rules.stop_before_bed_hours * MINUTE)} before bed. A sip counts when you lift the cup off the desk and put it back.</p>
        <div class="row"><button type="button" class="btn-quiet" data-log="hydration:sip">+ Sip</button></div>
      </article>
      <article class="habit-card" style="--tint:var(--cobalt-tint)">
        <h2>Breaks</h2>
        <p class="big">${b.count} <small>breaks today</small></p>
        ${bar(mins(b.sitting_sec), b.sit_limit_min, null, 'Sitting time before the next break')}
        <p class="hint">Sitting for ${mins(b.sitting_sec)} min now; longest stretch today ${mins(b.longest_sit_sec)} min. Walk ${rules.breaks.break_min} minutes for every ${b.sit_limit_min} minutes of sitting${rules.breaks.walk_after_meal ? ', and a short walk after meals' : ''}. Leave the phone behind.</p>
        <div class="row"><button type="button" class="btn-quiet" data-log="breaks:break">Log a break</button></div>
      </article>
      <article class="habit-card" style="--tint:var(--mustard-tint)">
        <h2>Light</h2>
        <p class="big">${l.sunlight_ts ? 'Sunlight ✓' : 'No sunlight yet'}</p>
        <p class="hint">${l.afternoon_light_ts ? 'Afternoon light ✓. ' : ''}Sunset ${clock(h.times.sunset_ts)}. Morning ${morningLight('; not through a window')}, late-afternoon light before sunset, and dim lights for ${duration(rules.evening_dim_before_bed_min)} before bed.</p>
        <div class="row">${sunButtons}</div>
      </article>
      <article class="habit-card" style="--tint:var(--coral-tint)">
        <h2>Phone-free</h2>
        ${p.counted ? `<p class="big">${mins(p.phone_free_sec)} min <small>of ${p.goal_min}</small></p>
        ${bar(mins(p.phone_free_sec), p.goal_min, null, 'Phone-free time today')}
        <p class="hint">Desk time with no phone in sight. Streak now ${mins(p.streak_sec)} min, best ${mins(p.best_sec)} min. ${p.phone_on_desk ? 'The phone is on the desk.' : ''} Even a phone in the same room pulls at your focus; an hour before bed, put it away.</p>`
        : '<p class="hint">The phone is the camera right now, so this isn’t counted. Use the Raspberry Pi camera to track phone-free time.</p>'}
      </article>
    </section>
    <section><h2>Today</h2>
      <div class="today">
        <div class="stat"><strong>${mins(h.desk_sec)}</strong><span>minutes at the desk</span></div>
        <div class="stat"><strong>${h.danger_count}</strong><span>desk warnings</span></div>
        <div class="stat"><strong>${(h.food.entries || []).length}</strong><span>foods logged</span></div>
      </div></section>
    <section><h2>Your own counters</h2>
      <p class="hint">Tap + whenever it happens. They reset at midnight.</p>
      <ul class="counter-list">${counters.items.map((c, i) => `<li><span>${esc(c.label)}</span>
        <button type="button" class="round" data-dec="${i}" aria-label="One less: ${esc(c.label)}">−</button><strong>${c.count}</strong>
        <button type="button" class="round" data-inc="${i}" aria-label="One more: ${esc(c.label)}">+</button>
        <button type="button" class="round" data-del="${i}" aria-label="Remove ${esc(c.label)}">×</button></li>`).join('')}</ul>
      <form class="inline-add" id="add-habit" style="max-width:520px"><input type="text"${maxLength(size('counter_label_chars'))} placeholder="Something else to count" aria-label="New counter"><button type="submit" class="btn-quiet">Add</button></form></section>`, poll);
}

// ————— Event log page —————
// Only what you'd care about: your habits, reminders and warnings (the server leaves out the rest).
const LOG_FILTERS = [
  ['all', 'Everything', 'habit,nudge,alert,camera'],
  ['habits', 'Habits', 'habit'],
  ['reminders', 'Reminders', 'nudge'],
  ['warnings', 'Warnings', 'alert,camera'],
];
/** [who, what happened, a plain subtitle], or null for an event the page doesn't show. */
function eventLine(e) {
  const a = e.agent ? who(e.agent) : null;
  const cam = lens();
  switch (e.type) {
    case 'alert': return [a || cam, e.text || `${who(e.agent).name} is too close to something`, e.subtitle || ''];
    case 'habit': return e.action ? [cam, e.say || `${HABIT_NAMES[e.habit] || 'A habit'} logged`, HABIT_NAMES[e.habit] || ''] : null;
    case 'nudge': return e.text ? [a || cam, e.text, 'Reminder'] : null;
    case 'camera': return e.text ? [cam, e.text, 'Camera'] : null;
    default: return null;
  }
}
async function renderLog(poll = false) {
  const page = $('#view-log');
  const head = `<header><h1 class="page-title">Event log</h1>
    <p class="page-intro">What happened at your desk: sips, breaks, food, to-dos ticked off, reminders and warnings.</p></header>`;
  if (!state.live) {
    paint(page, `${head}<p class="muted">Start Desk Talk on this computer to keep an event log.</p>`);
    return;
  }
  const filter = LOG_FILTERS.find((f) => f[0] === state.logFilter) || LOG_FILTERS[0];
  const dayQ = state.logDay ? `day=${encodeURIComponent(state.logDay)}&` : '';
  let evs = null;
  try { evs = await api(`/api/log/events?${dayQ}limit=${ui.web.log_shown}&types=${filter[2]}`); } catch { /* says so below */ }
  // The day chips: asked for when the page opens, and again when the server's day moved on (past midnight).
  if (evs && (!state.logDays || (!state.logDay && evs.day !== state.logDays.today))) {
    state.logDays = await api('/api/log/days').catch(() => state.logDays);
  }
  if (state.view !== 'log') return;
  const today = state.logDays?.today || evs?.day || todayKey();
  const days = [today, ...(state.logDays?.days || []).filter((d) => d !== today)];
  const chip = (d) => {
    const isToday = d === today;
    const pressed = isToday ? !state.logDay || state.logDay === today : state.logDay === d;
    return `<button type="button" class="chip" data-day="${isToday ? '' : esc(d)}" aria-pressed="${pressed}">${isToday ? 'Today' : esc(dayName(d))}</button>`;
  };
  const lines = (evs?.events || []).map((e) => [e, eventLine(e)]).filter(([, line]) => line);
  const list = !evs
    ? '<p class="muted">Couldn’t load the event log. <button type="button" class="link" data-retry="log">Try again</button></p>'
    : lines.length ? `<ul class="log events">${lines.map(([e, [a, title, sub]]) => `<li><time>${clock(e.ts)}</time>${avatar(a, 28)}<p>${esc(title)}${sub ? `<small>${esc(sub)}</small>` : ''}</p></li>`).join('')}</ul>`
      : '<p class="muted">Nothing yet.</p>';
  paint(page, `${head}
    <section>
      <div class="chips" role="group" aria-label="Day">${days.map(chip).join('')}</div>
      <div class="chips" role="group" aria-label="Show">${LOG_FILTERS.map(([k, label]) => `<button type="button" class="chip" data-filter="${k}" aria-pressed="${state.logFilter === k}">${label}</button>`).join('')}</div>
      ${list}</section>`, poll);
}

// ————— Captures page —————
async function renderCaptures(poll = false) {
  const page = $('#view-captures');
  const head = `<header><h1 class="page-title">Captures</h1>
    <p class="page-intro">Photos the desk cam took: proof for warnings, and pictures of easy-to-lose things when they show up. They’re kept until Desk Talk restarts.</p></header>`;
  if (!state.live) { paint(page, `${head}<p class="muted">Start Desk Talk on this computer to collect desk photos.</p>`); return; }
  let items = null;
  try { items = (await api('/api/snapshots')).items || []; } catch { /* says so below */ }
  if (state.view !== 'captures') return;
  const when = (ts) => new Date((ts || 0) * MS).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  paint(page, head + (items === null
    ? '<p class="muted">Couldn’t load photos. <button type="button" class="link" data-retry="captures">Try again</button></p>'
    : items.length
      ? `<div class="gallery">${items.map((it) => `<button type="button" data-photo="/api/snapshot/${esc(it.id)}">
        <img src="/api/snapshot/${esc(it.id)}" alt="" loading="lazy">
        <strong>${esc(it.label || 'Capture')}</strong><span>${when(it.ts)}</span></button>`).join('')}</div>`
      : '<p class="muted">No photos yet. They appear when something gets too close, or when your phone, keys, or wallet show up.</p>'), poll);
}

// ————— Settings page —————
// The sliders' ranges and where a new danger zone's slider starts come from ui.web.settings_sliders and
// ui.web.new_zone_px (settings → web). The sensitivity steps come from ui.sensitivity_steps (settings → agents),
// each with what it does in words (`says`), and what the slider covers from ui.sensitivity_intro (prompts.yaml → ui).
const hourText = (h) => `${String(h).padStart(2, '0')}:00`;
let savedTimer = null;
async function renderSettings() {
  const page = $('#view-settings');
  const head = `<header><h1 class="page-title">Settings</h1>
    <p class="page-intro">Your day, how picky the camera is, how close is too close, and when the desk should stay quiet.</p></header>`;
  if (!state.live) { page.innerHTML = `${head}<p class="muted">Settings are saved by Desk Talk on your computer. Start it to change them.</p>`; return; }
  let rules; let prefs;
  try {
    [rules, prefs] = await Promise.all([api('/api/rules'), api('/api/preferences')]);
  } catch {
    page.innerHTML = `${head}<p class="muted">Couldn’t load the settings. <button type="button" class="link" data-open="settings">Try again</button></p>`;
    return;
  }
  const zones = { ...(rules.danger_zones || {}) };
  const builtIn = ui.desk.edge_proximity;
  const edge = { danger_px: builtIn.danger_px, warn_px: builtIn.warn_px, ...(rules.edge_proximity || {}) };
  let outline = (rules.desk_outline || []).map(([x, y]) => [x, y]);
  let outlineChanged = false;
  let startOver = outline.length >= ui.desk.min_desk_corners;   // a saved outline: the first tap starts a new one
  let picSize = null;
  const sliders = ui.web.settings_sliders;
  const newZonePx = ui.web.new_zone_px;
  const steps = ui.sensitivity_steps || [];
  // What you set but haven't saved yet: kept when the page is drawn again (adding or removing a danger zone).
  const draft = {
    wake_time: prefs.wake_time, bed_time: prefs.bed_time, quiet_enabled: !!prefs.quiet_enabled,
    quiet_start: hourText(prefs.quiet_start_hour ?? ui.quiet_hours.start_hour),
    quiet_end: hourText(prefs.quiet_end_hour ?? ui.quiet_hours.end_hour),
    sensitivity: Math.min(steps.length - 1, Math.max(0, prefs.sensitivity ?? ui.sensitivity_default)),
  };
  const keepTyped = () => {
    if (!$('#wake-time')) return;
    draft.wake_time = $('#wake-time').value;
    draft.bed_time = $('#bed-time').value;
    draft.quiet_enabled = $('#dnd-on').checked;
    draft.quiet_start = $('#dnd-start').value;
    draft.quiet_end = $('#dnd-end').value;
    if ($('#sensitivity')) draft.sensitivity = +$('#sensitivity').value;
  };
  const range = (name, value, { min, max }, label) => `<span class="range"><input type="range" name="${name}" min="${min}" max="${max}" value="${value}" aria-label="${esc(label)}"></span>`;

  const draw = () => {
    page.innerHTML = `${head}
    <section><h2>Your day</h2>
      <p class="hint">The habit reminders are timed from these: water right after waking, sunlight in the first hour, dim lights and no screens in the last hour before bed.</p>
      <div class="form-rows">
        <label class="field"><span>I wake up at</span><input type="time" id="wake-time" value="${esc(draft.wake_time)}"></label>
        <label class="field"><span>I go to bed at</span><input type="time" id="bed-time" value="${esc(draft.bed_time)}"></label>
      </div></section>
    ${steps.length ? `<section><h2>How picky the camera is</h2>
      <p class="hint">${esc(ui.sensitivity_intro)}</p>
      <div class="form-rows">
        <label class="field"><span>Sensitivity</span><span class="range"><input type="range" id="sensitivity" min="0" max="${steps.length - 1}" step="1" value="${draft.sensitivity}" aria-valuetext="${esc(steps[draft.sensitivity].name)}"><output id="sensitivity-name">${esc(steps[draft.sensitivity].name)}</output></span></label>
        <p class="range-ends"><span>${esc(steps[0].name)}</span><span>${esc(steps.at(-1).name)}</span></p>
        <p class="hint" id="sensitivity-says" role="status">${esc(steps[draft.sensitivity].says)}</p>
      </div></section>` : ''}
    <section><h2>Danger zones</h2>
      <p class="hint">Each object gets an invisible bubble around it. When something risky enters the bubble (a cup near the laptop), the desk speaks up. Slide right for a bigger bubble.</p>
      <div class="form-rows">${Object.entries(zones).map(([cls, z]) => `<div class="zone" data-zone="${esc(cls)}">
          <strong>${esc(plainLabel(cls))}</strong>
          <input type="text" name="label" value="${esc(z.label || '')}" aria-label="Name for the ${esc(plainLabel(cls))} zone">
          ${range('expand', z.expand || newZonePx, sliders.zone, `Bubble size around the ${plainLabel(cls)}`)}
          <button type="button" class="round" data-remove aria-label="Remove the ${esc(plainLabel(cls))} zone">×</button></div>`).join('') || '<p class="muted">No zones yet.</p>'}
        <form class="inline-add" id="add-zone"><select aria-label="Object for a new danger zone">${(rules.available_objects || []).filter((o) => !zones[o]).map((o) => `<option value="${esc(o)}">${esc(plainLabel(o))}</option>`).join('')}</select>
          <button type="submit" class="btn-quiet">Add zone</button></form>
      </div></section>
    <section><h2>Desk edges</h2>
      <p class="hint">Tap the corners of your desk top on the picture, going around the desk. If the desk runs past the side of the picture, tap where it meets the side. Lines along the side of the picture are ignored, because the desk keeps going there. Once marked, only things on the desk can join the chat, and things near a real edge get a warning.</p>
      <div class="edge-editor">
        ${state.camOk
          ? `<div class="edge-pic" id="edge-pic"><img id="edge-img" src="/api/latest_frame_raw/${state.camIndex}?t=${Date.now()}" alt="What the desk cam sees"><svg id="edge-svg" aria-hidden="true"></svg></div>`
          : `<p class="muted edge-off">Turn on the camera to mark the desk. ${outline.length ? `(${outline.length} corners are saved.)` : ''}</p>`}
        <div class="edge-tools">
          <button type="button" class="btn-quiet" id="edge-undo">Undo</button>
          <button type="button" class="btn-quiet" id="edge-clear">Clear</button>
          ${state.camOk ? '<button type="button" class="btn-quiet" id="edge-photo">New picture</button>' : ''}
          <span class="muted" id="edge-status" role="status"></span>
        </div>
      </div>
      <p class="hint">How near the edge something may get: a warning first, then an alert when it’s nearer still. Slide right to speak up sooner.</p>
      <div class="form-rows">
        <label class="field"><span>Warn at</span>${range('warn_px', edge.warn_px, sliders.warn, 'Warn at')}</label>
        <label class="field"><span>Alert at</span>${range('danger_px', edge.danger_px, sliders.alert, 'Alert at')}</label>
      </div></section>
    <section><h2>Quiet hours</h2>
      <p class="hint">During quiet hours there are no Mac notifications at all, warnings included, and habit reminders wait until they end. Warnings still show in the chat.</p>
      <div class="form-rows">
        <label class="check"><input type="checkbox" id="dnd-on" ${draft.quiet_enabled ? 'checked' : ''}> Use quiet hours</label>
        <div class="field"><label for="dnd-start">From</label><input type="time" id="dnd-start" step="3600" value="${esc(draft.quiet_start)}"><label for="dnd-end">to</label><input type="time" id="dnd-end" step="3600" value="${esc(draft.quiet_end)}"></div>
      </div></section>
    <div class="save-row"><button type="button" class="btn" id="save-settings">Save settings</button><span class="saved" id="saved" role="status"></span></div>
    <section><h2>Chats</h2>
      <p class="hint">Your chats are saved in this browser so they survive a refresh.</p>
      <button type="button" class="btn-quiet" id="clear-chats">Clear all chats</button></section>`;

    $('#sensitivity')?.addEventListener('input', (e) => {
      const s = steps[+e.target.value];
      e.target.setAttribute('aria-valuetext', s.name);
      $('#sensitivity-name').textContent = s.name;
      $('#sensitivity-says').textContent = s.says;
    });
    page.querySelectorAll('.range input[name]').forEach((r) => r.addEventListener('input', () => {
      const z = r.closest('.zone');
      if (z) { zones[z.dataset.zone].expand = +r.value; return; }
      edge[r.name] = +r.value;
      // "Warn at" is never nearer the edge than "Alert at".
      const warn = page.querySelector('.range input[name="warn_px"]');
      if (r.name === 'warn_px' && edge.warn_px < edge.danger_px) { edge.warn_px = edge.danger_px; r.value = edge.danger_px; }
      if (r.name === 'danger_px' && edge.danger_px > edge.warn_px) { edge.warn_px = edge.danger_px; warn.value = edge.danger_px; }
    }));
    page.querySelectorAll('.zone input[name="label"]').forEach((inp) => inp.addEventListener('input', () => {
      zones[inp.closest('.zone').dataset.zone].label = inp.value;
    }));
    page.querySelectorAll('[data-remove]').forEach((b) => b.addEventListener('click', () => {
      keepTyped(); delete zones[b.closest('.zone').dataset.zone]; draw();
    }));
    $('#add-zone')?.addEventListener('submit', (e) => {
      e.preventDefault();
      const cls = e.target.querySelector('select').value;
      if (cls) { keepTyped(); zones[cls] = { expand: newZonePx, label: `${plainLabel(cls)} zone` }; draw(); }
    });
    wireEdgeEditor();
    $('#clear-chats').addEventListener('click', () => {
      if (!window.confirm('Clear every chat on this page? This can’t be undone.')) return;
      for (const id of Object.keys(state.messages)) { state.messages[id] = []; state.unread[id] = 0; }
      storage.save(HISTORY_KEY, null);
      storage.save(CLEARED_KEY, Date.now());     // so the server's recent messages don't bring them back
      renderChatList();
      toast(null, 'Chats cleared');
    });
    $('#save-settings').addEventListener('click', save);
  };

  const say = (text, error = false) => {
    const out = $('#saved');
    clearTimeout(savedTimer);
    out.className = error ? 'saved error' : 'saved';
    out.textContent = text;
    if (text === 'Saved' && ui.web.saved_note_sec) savedTimer = setTimeout(() => { out.textContent = ''; }, ui.web.saved_note_sec * MS);
  };
  const why = (err) => {
    if (err?.status >= 400 && err.status < 500) return 'Check what you typed and try again.';
    return err?.status ? 'Desk Talk had a problem. Try again in a moment.' : 'Is Desk Talk still running?';
  };

  async function save() {
    keepTyped();
    if (outline.length && outline.length < ui.desk.min_desk_corners) {
      say(`Tap at least ${ui.desk.min_desk_corners} corners of the desk, or press Clear.`, true); return;
    }
    if (!draft.wake_time || !draft.bed_time) { say('Pick a time to wake up and a time to go to bed.', true); return; }
    if (!draft.quiet_start || !draft.quiet_end) { say('Pick a start and an end for quiet hours.', true); return; }
    say('Saving…');
    const failed = [];
    let problem = null;
    try {
      const body = { danger_zones: zones, edge_proximity: { ...edge } };
      if (outlineChanged) body.desk_outline = outline;
      await api('/api/rules', { method: 'PUT', body });
      outlineChanged = false;
    } catch (err) { failed.push('the danger zones and desk edges'); problem = err; }
    try {
      const body = {
        wake_time: draft.wake_time, bed_time: draft.bed_time, quiet_enabled: draft.quiet_enabled,
        quiet_start_hour: +draft.quiet_start.slice(0, 2), quiet_end_hour: +draft.quiet_end.slice(0, 2),
      };
      if (steps.length) body.sensitivity = draft.sensitivity;
      prefs = (await api('/api/preferences', { method: 'PUT', body })).preferences;
      refreshHabits();
    } catch (err) { failed.push(steps.length ? 'your day, sensitivity and quiet hours' : 'your day and quiet hours'); problem = err; }
    if (failed.length) say(`Couldn’t save ${failed.join(' or ')}. ${why(problem)}`, true);
    else say('Saved');
  }

  // ——— Desk edge editor: tap corners on the camera picture ———
  const SNAP = ui.desk.desk_border_share;     // a tap this close to the picture's side goes onto the side
  const sameBorder = (a, b) => (a[0] <= SNAP && b[0] <= SNAP) || (a[0] >= 1 - SNAP && b[0] >= 1 - SNAP)
    || (a[1] <= SNAP && b[1] <= SNAP) || (a[1] >= 1 - SNAP && b[1] >= 1 - SNAP);

  function drawOutline() {
    const svg = $('#edge-svg');
    const status = $('#edge-status');
    const real = outline.length >= ui.desk.min_desk_corners ? outline.filter((p, i) => !sameBorder(p, outline[(i + 1) % outline.length])).length : 0;
    if (status) {
      status.textContent = outline.length < ui.desk.min_desk_corners
        ? `${outline.length} of at least ${ui.desk.min_desk_corners} corners`
        : `${outline.length} corners, ${real} real desk edge${real === 1 ? '' : 's'}${outlineChanged ? '. Press Save settings.' : '. Tap the picture to start over.'}`;
    }
    if (!svg || !picSize) return;
    const { w, h } = picSize;
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const P = (p) => `${(p[0] * w).toFixed(1)},${(p[1] * h).toFixed(1)}`;
    const sw = Math.max(2, w / 220);
    let out = '';
    if (outline.length >= ui.desk.min_desk_corners) out += `<polygon points="${outline.map(P).join(' ')}" class="edge-fill"/>`;
    outline.forEach((p, i) => {
      if (i === outline.length - 1 && outline.length < ui.desk.min_desk_corners) return;
      const q = outline[(i + 1) % outline.length];
      const ignored = sameBorder(p, q);
      out += `<line x1="${p[0] * w}" y1="${p[1] * h}" x2="${q[0] * w}" y2="${q[1] * h}" class="${ignored ? 'edge-ignored' : 'edge-real'}" stroke-width="${ignored ? sw * 0.7 : sw * 1.4}" ${ignored ? `stroke-dasharray="${sw * 3} ${sw * 3}"` : ''}/>`;
    });
    outline.forEach((p, i) => {
      out += `<circle cx="${p[0] * w}" cy="${p[1] * h}" r="${sw * 4}" class="edge-dot"/><text x="${p[0] * w}" y="${p[1] * h}" font-size="${sw * 5}" class="edge-num">${i + 1}</text>`;
    });
    svg.innerHTML = out;
  }

  function wireEdgeEditor() {
    const img = $('#edge-img');
    if (img) {
      const ready = () => { picSize = { w: img.naturalWidth || 4, h: img.naturalHeight || 3 }; drawOutline(); };
      if (img.complete && img.naturalWidth) ready(); else img.addEventListener('load', ready, { once: true });
      img.addEventListener('error', () => { $('#edge-status').textContent = 'Couldn’t load the camera picture. Try New picture.'; });
      $('#edge-pic').addEventListener('click', (e) => {
        if (startOver) { outline = []; startOver = false; }
        if (outline.length >= ui.desk.max_desk_corners) return;
        const r = img.getBoundingClientRect();
        let x = (e.clientX - r.left) / r.width;
        let y = (e.clientY - r.top) / r.height;
        if (x < 0 || x > 1 || y < 0 || y > 1) return;
        x = x <= SNAP ? 0 : x >= 1 - SNAP ? 1 : x;
        y = y <= SNAP ? 0 : y >= 1 - SNAP ? 1 : y;
        outline.push([+x.toFixed(4), +y.toFixed(4)]);
        outlineChanged = true;
        drawOutline();
      });
    }
    $('#edge-undo').addEventListener('click', () => { startOver = false; outline.pop(); outlineChanged = true; drawOutline(); });
    $('#edge-clear').addEventListener('click', () => { startOver = false; outline = []; outlineChanged = true; drawOutline(); });
    $('#edge-photo')?.addEventListener('click', () => { img.src = `/api/latest_frame_raw/${state.camIndex}?t=${Date.now()}`; });
    drawOutline();
  }
  draw();
}

// ————— Desk cam panel —————
function feedUrl() {
  return `/api/${state.feed === 'clean' ? 'video_feed_raw' : 'video_feed'}/${state.camIndex}`;
}
/** On a narrow screen the desk cam is a panel you open; the live video only loads while you can see it. */
const camShown = () => getComputedStyle($('#cam')).visibility !== 'hidden';
let videoShown = null;
function openCam(on) {
  $('#cam').classList.toggle('open', on);
  $('#cam-toggle').setAttribute('aria-expanded', String(on));
  renderCamStage();
}

function renderCamStage() {
  const stage = $('#cam-stage');
  $('#cam-caption')?.remove();
  videoShown = camShown();
  stage.classList.toggle('is-pairing', state.live && !state.camOk && state.cameraSource !== 'webcam');
  stage.classList.toggle('has-video', state.live && state.camOk && videoShown);
  if (!state.live) {
    stage.innerHTML = '<img class="scene" src="art/desk-scene.svg" alt="Illustration of the desk characters">';
    stage.insertAdjacentHTML('afterend', '<p class="cam-caption" id="cam-caption">This is a demo. Start Desk Talk on this computer and turn on the camera to see your real desk.</p>');
    return;
  }
  if (state.camOk) {
    stage.innerHTML = videoShown ? `<img src="${feedUrl()}" alt="Live view of your desk">` : '';
    return;
  }
  if (state.cameraSource === 'webcam') {
    stage.innerHTML = '<p class="cam-caption">No webcam found. Check that one is plugged in, then restart Desk Talk.</p>';
    return;
  }
  if (state.cameraSource === 'pi') {
    stage.innerHTML = `<div class="pair">
      <h3>Start the Raspberry Pi camera</h3>
      <ol>
        <li>Put the Pi on the same Wi-Fi as this computer.</li>
        <li>On the Pi, in the desk-talk folder, run:</li>
      </ol>
      ${state.piCommand
        ? `<pre class="json pi-cmd">${esc(state.piCommand)}</pre><button type="button" class="btn-quiet" id="copy-pi">Copy command</button>`
        : '<p class="muted">Open this page on the computer running Desk Talk to see the command.</p>'}
      <p class="pair-url">It takes ${ui.pi.fps} pictures a second${ui.pi.delete_every_min ? `, keeps them on the Pi, and deletes them every ${ui.pi.delete_every_min} minutes` : ' and keeps them on the Pi'}.</p></div>`;
    $('#copy-pi')?.addEventListener('click', () => {
      navigator.clipboard?.writeText(state.piCommand).then(() => toast(null, 'Copied'), () => {});
    });
    return;
  }
  const p = state.phone;
  stage.innerHTML = p?.url ? `<div class="pair">
      ${p.qr_svg ? `<div class="pair-qr">${p.qr_svg}</div>` : ''}
      <h3>Scan with your phone</h3>
      <ol>
        <li>Use the same Wi-Fi as this computer.</li>
        <li>If it says the connection is not private, tap <b>Show Details</b>, then <b>visit this website</b>.</li>
        <li>Tap <b>Start camera</b> and point it at your desk.</li>
      </ol>
      <p class="pair-url">${esc(p.url)}</p></div>`
    : '<div class="pair"><h3>Starting the phone link…</h3><p class="muted">If this doesn’t change, restart Desk Talk.</p></div>';
}

function renderOnDesk(det) {
  const list = $('#on-desk');
  const here = agentIds().filter((id) => state.agents[id].on_desk);
  const agentLine = (id) => `<li style="--tint:${tintVar(state.agents[id].color)}">${avatar(state.agents[id], 26)}${esc(state.agents[id].name)}</li>`;
  if (!state.live) {
    paint(list, here.map(agentLine).join('') || '<li class="extra">Nobody yet</li>');
    return;
  }
  const extras = [...new Set((det?.extras || []).filter((e) => !e.ignored).map((e) => plainLabel(e.yolo_class)))];
  const lines = [...here.map(agentLine), ...extras.map((e) => `<li class="extra" title="Seen, not in the chat">${esc(e)}</li>`)];
  paint(list, lines.slice(0, size('on_desk_shown')).join('')
    || `<li class="extra">${state.camOk ? 'Nothing recognized yet' : 'Waiting for the camera'}</li>`);
}

function renderRisks(safety, cameraIssues) {
  const list = $('#risks');
  if (!state.live) { paint(list, '<li>Cup is a little close to Computer.</li>'); return; }
  const issues = (cameraIssues || []).slice(0, size('camera_issues_shown')).map((i) => `<li class="lens-issue">
      ${avatar(lens(), 30)}<span><strong>${esc(i.title)}.</strong> ${esc(i.fix)}</span></li>`);
  const lines = (safety?.explanations || []).slice(0, size('risk_lines_shown')).map((l) => `<li>${esc(l)}</li>`);
  paint(list, [...issues, ...lines].join('') || `<li class="calm">${state.camOk ? 'All calm on the desk.' : 'Nothing to report yet.'}</li>`);
}

async function renderProofs() {
  const box = $('#proofs');
  if (!state.live) { paint(box, '<p class="muted" style="grid-column:1/-1">Proof photos from warnings show up here.</p>'); return; }
  const shown = size('side_photos_shown') ?? ui.web.photos_shown;
  const data = await api(`/api/snapshots${shown ? `?limit=${shown}` : ''}`).catch(() => null);
  if (!data) return;                      // keep what's there; the slow timer asks again
  const items = (data.items || []).slice(0, shown);
  const ids = items.map((i) => i.id).join(',');
  if (ids === state.snapshotIds) return;
  state.snapshotIds = ids;
  paint(box, items.length
    ? items.map((it) => `<button type="button" class="proof" data-photo="/api/snapshot/${esc(it.id)}" aria-label="${esc(it.label || 'Desk photo')}" title="${esc(it.label || '')}"><img src="/api/snapshot/${esc(it.id)}" alt="" loading="lazy"></button>`).join('')
    : '<p class="muted" style="grid-column:1/-1">None yet. Photos are saved when something gets too close.</p>');
}

/** One request every web.refresh_ms.live: camera status, agents, things seen, and risks together. When the server
 * can't be reached the page says so, and it keeps asking until the server is back. */
async function pollLive() {
  let d;
  try {
    if (state.offline) ui = await api('/api/ui_settings');     // back: the settings may have changed meanwhile
    d = await api(`/api/live${state.phone?.qr_svg ? '' : '?qr=1'}`, { timeout: ui.web.live_timeout_ms });
  } catch {
    showOffline(true);
    return;
  }
  showOffline(false);
  const wasOk = state.camOk;
  state.cameraSource = d.camera_source || state.cameraSource;
  state.camOk = !!d.camera;
  state.camIndex = d.camera_index ?? state.camIndex;
  if (d.pi_command && d.pi_command !== state.piCommand) { state.piCommand = d.pi_command; if (!state.camOk) renderCamStage(); }
  // The QR code is only sent when asked for (?qr=1): keep the one we have when the reply has none.
  if (d.phone) state.phone = { url: d.phone.url || state.phone?.url || '', qr_svg: d.phone.qr_svg || state.phone?.qr_svg || null };
  if (state.camOk !== wasOk) {
    state.camSince = Date.now();
    renderCamStage();
    renderConn();
  } else if (!state.camOk && state.phone && !$('#cam-stage .pair-qr') && state.phone.qr_svg) {
    renderCamStage();
  }
  // "Camera connected / disconnected" only once the change has lasted web.camera_note_after_sec.
  if (state.camOk !== state.camNoted && Date.now() - state.camSince >= (ui.web.camera_note_after_sec || 0) * MS) {
    state.camNoted = state.camOk;
    toast(null, state.camOk ? 'Camera connected' : 'Camera disconnected');
  }
  syncAgents(d.agents);
  renderOnDesk({ extras: d.detections?.extras ?? d.extras });
  renderRisks(d.safety, d.camera_issues);
  renderAi(d.ai);
}

/** The note at the top of the page while the server can't be reached. */
function showOffline(on) {
  if (on === state.offline) return;
  state.offline = on;
  const el = $('#offline');
  el.textContent = on ? 'Can’t reach Desk Talk. Trying again…' : '';
  el.hidden = !on;
  renderConn();
  if (!on) {
    renderCamStage();          // the live video stopped with the server: start it again
    loadRecentMessages();      // messages sent while the page was cut off
  }
}

function renderAi(ai) {
  if (!ai) return;
  let text = '';
  if (!ai.ready) text = 'The characters need an AI to chat. Set one up on this computer (the README says how), then restart Desk Talk.';
  else if (ai.problem) text = ai.problem;
  if (text === state.aiText) return;
  state.aiText = text;
  const el = $('#ai-banner');
  el.textContent = text;
  el.hidden = !text;
}

// ————— Live connection —————
/** Messages and join / leave notes from before this page opened, or sent while it was cut off (the server keeps
 * the newest few: chat.recent_kept). Ones from before "Clear all chats" stay cleared. */
async function loadRecentMessages() {
  for (const list of Object.values(state.messages)) for (const m of list) if (m.id) state.seen.add(m.id);
  let recent = [];
  try { recent = (await api('/api/messages/recent')).messages || []; } catch { return; }
  const cleared = storage.load(CLEARED_KEY, 0);
  for (const d of recent) if (!cleared || (d.ts || 0) * MS > cleared) handleServerMessage(d, true);
}

function handleServerMessage(d, old = false) {
  if (d.type === 'roster' && d.agent) {
    if (old) {
      if (!state.agents[d.agent.id]) upsertAgent({ ...d.agent, on_desk: false });
      if (!d.quiet) addMessage('group', { id: d.id, time: d.ts * MS, system: d.action, agent: d.agent.id, text: d.text });
    } else {
      rosterEvent(d);
    }
    return;
  }
  if (d.type === 'habit_update') { refreshHabitsSoon(); return; }
  if (!d.text) return;
  if (d.type === 'group_message' && d.character) {
    addMessage('group', {
      id: d.id, time: d.ts ? d.ts * MS : Date.now(), char: d.character, text: d.text, snapshot: d.snapshot,
      replyTo: d.reply_to, habit: d.habit,
      toast: !old && (d.toast || (d.character === 'lens' && d.lens === 'problem')),
    });
    if (d.snapshot && !old) renderProofs();
    if (d.habit && !old) refreshHabitsSoon();
  }
}

function listenForEvents() {
  const es = new EventSource('/api/events');
  let broken = false;
  es.onerror = () => { broken = true; };
  es.onopen = () => { if (broken) { broken = false; loadRecentMessages(); } };     // back: catch up on what we missed
  es.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }
    handleServerMessage(d);
  };
}

/** Run `job` again and again, waiting `ms()` between the end of one run and the start of the next (read each time,
 * so settings that changed while the server was away count). Skipped while the page's tab is hidden; when you
 * come back to the tab, every job runs once at once (see wire()). */
const timedJobs = [];
function repeat(ms, job) {
  timedJobs.push(job);
  const next = async () => {
    try { if (!document.hidden) await job(); } finally { setTimeout(next, ms()); }
  };
  setTimeout(next, ms());
}
function catchUp() {
  if (document.hidden) return;
  for (const job of timedJobs) Promise.resolve().then(job).catch(() => {});
}

async function startLive(status) {
  state.live = true;
  loadHistory();
  loadTodoCopy();
  loadCounters();
  if (ui.chat?.message_max_chars) $('#input').maxLength = ui.chat.message_max_chars;
  state.cameraSource = status.camera_source || 'pi';
  state.camOk = !!status.camera;
  state.camNoted = state.camOk;
  state.camIndex = status.camera_indices?.[0] ?? 0;
  try { syncAgents((await api('/api/agents')).agents); } catch { /* the poll will catch up */ }
  loadTodos();
  listenForEvents();
  await loadRecentMessages();
  renderConn();
  renderCamStage();
  await pollLive();
  repeat(() => ui.web.refresh_ms.live, pollLive);
  refreshHabits();
  repeat(() => ui.web.refresh_ms.habits, refreshHabits);
  renderProofs();
  repeat(() => ui.web.refresh_ms.photos, async () => {
    await renderProofs();
    if (state.view === 'captures') await renderCaptures(true);
  });
  repeat(() => ui.web.refresh_ms.log, async () => { if (state.view === 'log') await renderLog(true); });
  renderAi({ ready: !!status.llm });
}

// ————— Demo (no server, e.g. GitHub Pages) —————
// Everything below is the demo's made-up story. The numbers in it are only for the story: the real app takes its
// numbers from the server (GET /api/ui_settings).
const DEMO_HOW_TO_RUN = 'Start Desk Talk on this computer and open the address it shows to use your real desk.';
// The settings the demo needs to tell its story (in the real app they come from the server).
const DEMO_UI = {
  web: {
    toast_ms: 4000, chat_kept: 200, chat_saved: 60, left_chat_shown_min: 60,
    retry_start_sec: 3,       // how soon the demo asks again whether Desk Talk is there (then twice as long each time)
    palette: ['cobalt', 'teal', 'tang', 'mustard', 'coral', 'plum', 'moss', 'slate', 'ink'],
  },
  chat: { todo_max_chars: 200 },
  habits: {
    food_label_max_chars: 60, stop_before_bed_hours: 2,
    morning_minutes: { clear: 10, partly_cloudy: 20, overcast: 30 }, morning_window_min: 60, evening_dim_before_bed_min: 60,
    breaks: { break_min: 5, walk_after_meal: true },
  },
  starter_todo: 'Pick one top priority for the next hour',
  starter_counters: ['Picked up phone'],
};
const DEMO_AGENTS = [
  { id: 'computer', name: 'Computer', art: 'art/computer.svg', color: 'cobalt', tracker: 'breaks', about: 'Anxious. Watches your screen breaks and anything near the keyboard.', labels: ['laptop'] },
  { id: 'cup', name: 'Cup', art: 'art/cup.svg', color: 'teal', tracker: 'hydration', about: 'Completely innocent. Counts your sips and insists it is nowhere near the laptop.', labels: ['cup'] },
  { id: 'snack', name: 'Snack', art: 'art/snack.svg', color: 'tang', tracker: 'food', about: 'Lives for flavor. Logs what you eat and does not care about crumbs.', labels: ['pizza'] },
  { id: 'paper', name: 'Book', art: 'art/paper.svg', color: 'mustard', tracker: 'todo', about: 'A gentle worrier. Keeps your to-do list and remembers where things went.', labels: ['book'] },
];
let demoPlayed = false;
const DEMO_SCRIPT = [
  ['cup', 'Just sitting here. Minding my own business.'],
  ['computer', 'You are about four centimeters from my keyboard. I can feel the condensation.'],
  ['snack', 'Relax, Computer. Crumbs are basically confetti.'],
  ['paper', 'Could everyone please stay away from the homework? It is due tomorrow.'],
  ['computer', '@user 34 minutes of sitting! Five-minute walk? I will count it when you leave.'],
];
function demoHabits() {
  const now = Date.now() / MS;
  return {
    day: todayKey(), times: { wake_ts: now - 5 * HOUR, bed_ts: now + 9 * HOUR, sunset_ts: now + 4 * HOUR },
    hydration: { sips: 9, water_ml: 720, goal_ml: 2400, on_waking_ml: 600, pace_ml: 1200, last_sip_ts: now - 1500, ml_per_sip: 80 },
    breaks: { count: 3, last_break_ts: now - 2400, sitting_sec: 34 * MINUTE, longest_sit_sec: 51 * MINUTE, sit_limit_min: 30, log: [] },
    phone_free: { counted: true, phone_free_sec: 64 * MINUTE, phone_on_desk_sec: 10 * MINUTE, streak_sec: 22 * MINUTE, best_sec: 41 * MINUTE, phone_on_desk: false, goal_min: 120 },
    light: { sunlight_ts: now - 4 * HOUR, afternoon_light_ts: null, sunlight_minutes: 10 },
    food: { entries: [{ label: 'apple', healthy: true }, { label: 'chips', healthy: false }] },
    desk_sec: 3 * HOUR, danger_count: 2, lost_alerts: 0, hourly_activity: [], objects_seen: [],
  };
}
function demoLog(habit, action, extra) {
  const h = state.habits || demoHabits();
  const now = Date.now() / MS;
  if (habit === 'hydration') { h.hydration.sips += 1; h.hydration.water_ml += h.hydration.ml_per_sip; h.hydration.last_sip_ts = now; }
  if (habit === 'breaks') { h.breaks.count += 1; h.breaks.sitting_sec = 0; h.breaks.last_break_ts = now; }
  if (habit === 'light') h.light.sunlight_ts = now;
  if (habit === 'food' && action === 'food') h.food.entries.push({ label: extra.label, healthy: !!extra.healthy });
  if (habit === 'food' && action === 'remove') h.food.entries.splice(extra.index, 1);
  state.habits = h;
}
async function playDemoOnce() {
  if (demoPlayed || state.live) return;
  demoPlayed = true;
  addMessage('group', { note: true, text: 'Demo. With Desk Talk running, objects your camera sees join this chat.' });
  for (const a of DEMO_AGENTS) {
    await sleep(500);
    upsertAgent({ ...a, on_desk: true, joined_at: Date.now() / MS });
    addMessage('group', { system: 'joined', agent: a.id, text: `${a.name} joined the chat` });
    renderOnDesk(null);
  }
  for (const [id, text] of DEMO_SCRIPT) {
    await sleep(900);
    showTyping(id, 'group');
    await sleep(900 + text.length * 12);
    hideTyping('group');
    addMessage('group', { char: id, text });
  }
}
/** No server when the page opened: run the demo. */
function startDemo() {
  state.live = false;
  ui = DEMO_UI;
  loadTodoCopy();
  loadCounters();
  state.habits = demoHabits();
  renderConn();
  renderCamStage();
  renderOnDesk(null);
  renderRisks(null);
  renderProofs();
}
async function demoReply(chatId, text) {
  const ids = agentIds();
  const id = chatId === 'group'
    ? (ids.find((c) => text.toLowerCase().includes(`@${state.agents[c].name.toLowerCase()}`)) || pick(ids))
    : chatId;
  if (!id) return;
  showTyping(id, chatId);
  await sleep(1100);
  hideTyping(chatId);
  addMessage(chatId, { char: id, text: 'I’m only a demo right now. Start Desk Talk and I’ll actually see your desk.' });
}

// ————— Wiring —————
/** The buttons in the panels and pages that are drawn again and again: wired once, here. */
function wirePanels() {
  const tracker = $('#tracker');
  tracker.addEventListener('change', (e) => {
    const cb = e.target.closest('[data-todo]');
    if (cb) { todo.items[+cb.dataset.todo].done = cb.checked; todoChanged(state.chat); }
  });
  tracker.addEventListener('click', (e) => {
    const del = e.target.closest('[data-todo-del]');
    if (del) { todo.items.splice(+del.dataset.todoDel, 1); todoChanged(state.chat); return; }
    const log = e.target.closest('[data-log]');
    if (log) { logFromButton(log); return; }
    const food = e.target.closest('[data-food-del]');
    if (food) { logHabit('food', 'remove', { index: +food.dataset.foodDel }); return; }
    if (e.target.closest('[data-treat]')) addFood(false);
  });
  tracker.addEventListener('submit', (e) => {
    e.preventDefault();
    if (e.target.id === 'food-add') { addFood(true); return; }
    if (e.target.id !== 'todo-add') return;
    const text = e.target.querySelector('input[type="text"]').value.trim();
    if (!text) return;
    const due = e.target.querySelector('input[type="date"]').value || null;
    todo.items.push(cleanTodo({ text: text.slice(0, ui.chat.todo_max_chars), due }));
    todoChanged(state.chat);
    $('#tracker input[type="text"]')?.focus();
  });
  function addFood(healthy) {
    const input = tracker.querySelector('#food-add input');
    const label = input.value.trim().slice(0, ui.habits.food_label_max_chars);
    if (!label) { input.focus(); return; }
    logHabit('food', 'food', { label, healthy });
  }

  const habits = $('#view-habits');
  habits.addEventListener('click', (e) => {
    const log = e.target.closest('[data-log]');
    if (log) { logFromButton(log); return; }
    const btn = e.target.closest('[data-inc],[data-dec],[data-del]');
    if (btn) {
      const i = +(btn.dataset.inc ?? btn.dataset.dec ?? btn.dataset.del);
      if (btn.dataset.inc) counters.items[i].count += 1;
      if (btn.dataset.dec) counters.items[i].count = Math.max(0, counters.items[i].count - 1);
      if (btn.dataset.del) counters.items.splice(i, 1);
      saveCounters(); renderHabits();
    }
  });
  habits.addEventListener('submit', (e) => {
    if (e.target.id !== 'add-habit') return;
    e.preventDefault();
    const val = e.target.querySelector('input').value.trim();
    if (!val) return;
    counters.items.push({ label: val.slice(0, size('counter_label_chars')), count: 0 });
    saveCounters(); renderHabits();
  });

  $('#view-log').addEventListener('click', (e) => {
    const filter = e.target.closest('[data-filter]');
    if (filter) { state.logFilter = filter.dataset.filter; renderLog(); return; }
    const day = e.target.closest('[data-day]');
    if (day) { state.logDay = day.dataset.day || null; renderLog(); }
  });

  document.addEventListener('click', (e) => {
    const retry = e.target.closest('[data-retry]');
    if (!retry) return;
    if (retry.dataset.retry === 'habits') refreshHabits();
    if (retry.dataset.retry === 'log') renderLog();
    if (retry.dataset.retry === 'captures') renderCaptures();
  });
}

function wire() {
  // Until the first check is done (live or demo), clicks and the chat box do nothing: their handlers read the
  // settings (ui). Meanwhile the corner says "Connecting…" (index.html).
  for (const type of ['click', 'submit']) {
    document.addEventListener(type, (e) => { if (!ui) { e.preventDefault(); e.stopPropagation(); } }, true);
  }
  $('#chat-list').addEventListener('click', (e) => {
    const b = e.target.closest('[data-chat]');
    if (b) openChat(b.dataset.chat);
  });
  $$('.page-link').forEach((b) => b.addEventListener('click', () => b.dataset.view && openPage(b.dataset.view)));
  wirePanels();

  document.addEventListener('click', (e) => {
    const open = e.target.closest('[data-open]');
    if (open) { e.preventDefault(); openPage(open.dataset.open); return; }
    const photo = e.target.closest('[data-photo]');
    if (photo) {
      const dlg = $('#photo');
      dlg.querySelector('img').src = photo.dataset.photo;
      dlg.showModal();
    }
  });
  $('#photo-close').addEventListener('click', () => $('#photo').close());
  $('#photo').addEventListener('click', (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });

  const input = $('#input');
  let lastPing = 0;
  $('#composer').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    $('#mentions').hidden = true;
    send(text);
  });
  input.addEventListener('input', () => {
    updateMentions();
    if (state.live && Date.now() - lastPing > ui.web.activity_ping_ms) {
      lastPing = Date.now();
      navigator.sendBeacon?.('/api/activity');
    }
  });
  input.addEventListener('keydown', (e) => {
    const box = $('#mentions');
    if (box.hidden) return;
    const opts = $$('.mention-opt', box);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      mentionIndex = (mentionIndex + (e.key === 'ArrowDown' ? 1 : -1) + opts.length) % opts.length;
      updateMentions();
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      insertMention(opts[mentionIndex].dataset.id);
    } else if (e.key === 'Escape') {
      box.hidden = true;
    }
  });
  $('#mentions').addEventListener('mousedown', (e) => {
    const opt = e.target.closest('.mention-opt');
    if (opt) { e.preventDefault(); insertMention(opt.dataset.id); }
  });

  $$('.seg button').forEach((b) => b.addEventListener('click', () => {
    state.feed = b.dataset.feed;
    $$('.seg button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    if (state.live && state.camOk) renderCamStage();
  }));
  $('#cam-toggle').addEventListener('click', () => openCam(true));
  $('#cam-close').addEventListener('click', () => openCam(false));
  document.addEventListener('visibilitychange', catchUp);
  window.addEventListener('resize', () => { if (ui && state.live && camShown() !== videoShown) renderCamStage(); });
  $('#conn').addEventListener('click', () => {
    if (!state.live) toast(null, `Demo mode. ${DEMO_HOW_TO_RUN}`);
    else if (!state.camOk) openCam(true);
  });
}

/** Is Desk Talk's server there? { settings, status }, or null (opened from a file, or from GitHub Pages). */
async function firstCheck() {
  if (!location.protocol.startsWith('http')) return null;
  try {
    const wait = { timeout: FIRST_CHECK_MS };
    const [settings, status] = await Promise.all([api('/api/ui_settings', wait), api('/api/status', wait)]);
    return { settings, status };
  } catch {
    return null;
  }
}

/** The demo keeps asking (after `sec`, then twice as long each time): a server that was slow to start still gets
 * its page. When it answers, the page starts again, live. */
function askAgainLater(sec) {
  if (!location.protocol.startsWith('http') || !sec) return;
  setTimeout(async () => {
    if (await firstCheck()) location.reload();
    else askAgainLater(sec * 2);
  }, sec * MS);
}

async function init() {
  wire();
  const found = await firstCheck();
  if (found) {
    ui = found.settings;
    await startLive(found.status);
  } else {
    startDemo();
    askAgainLater(ui.web.retry_start_sec);
  }
  openChat('group');
}

document.addEventListener('DOMContentLoaded', init);
