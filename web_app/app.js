/* ============================================
   DESK TALK — computer app
   Left: chats. Center: the open chat or page.
   Right: what the desk camera sees.
   Agents aren't fixed: when the camera sees an object it joins the chat,
   and when it's gone a while it leaves. Without a server it runs as a demo.
   Its numbers (refresh times, limits, the habit rules ...) come from the
   server: GET /api/ui_settings, i.e. config/settings.yaml on the computer.
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
const ML_PER_OZ = 29.57;
const PERCENT = 100;
const LEVEL_MAX = 255;         // a picture's brightness runs from 0 to this

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

/** fetch → JSON, with a timeout (an ordinary request's, from the settings). Throws on network errors and non-2xx.
 * The first check, which fetches the settings, passes its own (FIRST_CHECK_MS). */
async function api(path, { method = 'GET', body, timeout = ui?.web.request_timeout_ms } = {}) {
  const ctl = new AbortController();
  const t = timeout ? setTimeout(() => ctl.abort(), timeout) : null;
  try {
    const res = await fetch(path, {
      method,
      signal: ctl.signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(`${path} → ${res.status}`);
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

// ————— State —————
const state = {
  live: false,          // the Python server answered when the page opened (otherwise: the demo)
  offline: false,       // ...but can't be reached right now
  cameraSource: 'pi',   // pi | phone | webcam (config/settings.yaml)
  phoneMode: false,
  camOk: false,         // pictures are arriving right now
  camIndex: 0,
  feed: 'analysis',     // 'analysis' (boxes) or 'clean'
  phone: null,          // { url, qr_svg } for phone pairing
  piCommand: '',        // the command to run on the Pi
  view: 'chat',
  chat: 'group',
  agents: {},           // agent id → profile + on_desk / joined_at / left_at
  messages: { group: [] },
  unread: { group: 0 },
  habits: null,         // /api/habits/today
  habitFilter: 'all',
  logFilter: 'all',
  logDay: null,
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

// ————— To-do list (kept in the browser, sent to the server so Paper knows) —————
const TODO_KEY = 'desktalk_todo_v1';
const todo = storage.load(TODO_KEY, null) || { type: 'todo', items: [{ text: 'Pick one top priority for the next hour', done: false }] };
function todoChanged(id) {
  storage.save(TODO_KEY, todo);
  if (state.view === 'chat' && state.chat === id) renderTracker();
  if (state.live) api('/api/chat_todos', { method: 'POST', body: { chat: id, widget: todo } }).catch(() => {});
}
function syncTodos() {
  const byChat = Object.fromEntries(agentIds().filter((id) => state.agents[id].tracker === 'todo').map((id) => [id, todo]));
  if (state.live && Object.keys(byChat).length) api('/api/chat_todos_sync', { method: 'POST', body: { by_chat: byChat } }).catch(() => {});
}

// ————— Custom habit counters (Habits page) —————
const HABIT_KEY = 'desktalk_habits_v2';
const counters = (() => {
  const saved = storage.load(HABIT_KEY, null);
  if (saved && saved.day === todayKey() && Array.isArray(saved.items)) return saved;
  const labels = saved?.items?.map((i) => i.label) || ['Nail biting', 'Picked up phone', 'Slouching'];
  return { day: todayKey(), items: labels.map((label) => ({ label, count: 0 })) };
})();
const saveCounters = () => storage.save(HABIT_KEY, counters);

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
  if (joined && d.agent.tracker === 'todo') syncTodos();
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
  const list = $('#chat-list');
  list.innerHTML = chatOrder().map((id) => {
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
  }).join('');
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
  if (view === 'habits') renderHabits();
  if (view === 'log') renderLog();
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
    ? `<div class="quote" style="--quote-color:${colorVar(who(m.replyTo.character).color)}">${esc(who(m.replyTo.character).name)}: ${esc(m.replyTo.text.slice(0, QUOTE_CHARS))}</div>`
    : '';
  // Photos live on the server; after a restart old ones may be gone, so hide broken ones.
  const photo = m.snapshot && state.live
    ? `<img src="/api/snapshot/${esc(m.snapshot)}" alt="Photo from the desk cam" data-photo="/api/snapshot/${esc(m.snapshot)}" loading="lazy" onerror="this.remove()">`
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
  const empty = list.some((m) => !m.note)
    ? ''
    : `<div class="empty">${avatar(c, 132)}<p>${esc(c.empty || `${c.name} hasn't said anything yet. Say hi!`)}</p></div>`;
  box.innerHTML = list.map((m) => messageHtml(m, state.chat)).join('') + empty;
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
    hideTyping();
    box.insertAdjacentHTML('beforeend', messageHtml(list.at(-1), chatId));
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

function showTyping(charId, chatId) {
  hideTyping();
  if (state.view !== 'chat' || state.chat !== chatId) return;
  const c = who(charId);
  const box = $('#messages');
  box.querySelector('.empty')?.remove();
  box.insertAdjacentHTML('beforeend', `<div class="msg" id="typing" style="--tint:${tintVar(c.color)}">
    <span class="msg-art">${avatar(c, 36)}</span>
    <div class="bubble typing" aria-label="${esc(c.name)} is typing"><i></i><i></i><i></i></div></div>`);
  box.scrollTop = box.scrollHeight;
}
function hideTyping() { $('#typing')?.remove(); }

// How much text the page shows in a quoted reply and in a pop-up note (they're cut to fit the box).
const QUOTE_CHARS = 90;
const TOAST_CHARS = 140;
let toastTimer = null;
function toast(character, text) {
  const el = $('#toast');
  el.innerHTML = `${character ? avatar(character, 32) : ''}<span>${esc(text.slice(0, TOAST_CHARS))}</span>`;
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
  showTyping(chatId === 'group' ? 'group' : chatId, chatId);
  let res = null;
  try {
    res = await api('/api/chat', {
      method: 'POST',
      timeout: ui.chat_reply_timeout_ms,      // as long as the server may keep trying every AI call it makes
      body: { chat: chatId, message: text, recent_messages: historyFor(chatId), widget: who(chatId).tracker === 'todo' ? todo : undefined },
    });
  } catch {
    hideTyping();
    addMessage(chatId, { note: true, text: 'Could not reach the server. Is python server.py still running?' });
    return;
  }
  hideTyping();
  for (const r of res?.messages || []) {
    addMessage(chatId, { id: r.id, char: r.character || chatId, text: r.text, replyTo: r.reply_to, cta: r.cta });
  }
  if (res?.actions?.length && chatId !== 'group') applyTodoActions(chatId, res.actions);
  if (res?.habits_changed) refreshHabits();
  if (res?.error && !(res.messages || []).length) addMessage(chatId, { note: true, text: res.error });
}

/** The AI can change the to-do list (habit counts are changed on the server). */
function applyTodoActions(id, actions) {
  let changed = false;
  for (const a of actions) {
    const text = (a.text || '').toLowerCase();
    if (a.action === 'add_todo' && a.text && todo.items.length < ui.chat.todo_max_items) {
      todo.items.push({ text: a.text.slice(0, ui.chat.todo_max_chars), done: false }); changed = true;
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
const MENTION_MAX_CHARS = 12;     // longer than this after an @ and you're not typing a name any more

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
  if (at < 0 || (at > 0 && before[at - 1] !== ' ') || /\s/.test(query) || query.length > MENTION_MAX_CHARS) {
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
const HABIT_NAMES = { hydration: 'Water', breaks: 'Breaks', light: 'Light', phone_free: 'Phone-free', screens: 'Screens', food: 'Food', tasks: 'Tasks' };
let habitsTimer = null;
async function refreshHabits() {
  if (!state.live) { state.habits = state.habits || demoHabits(); }
  else {
    try { state.habits = await api('/api/habits/today'); } catch { return; }
  }
  if (state.view === 'chat' && who(state.chat).tracker) renderTracker();
  if (state.view === 'habits') renderHabits();
}
function refreshHabitsSoon() {
  clearTimeout(habitsTimer);
  habitsTimer = setTimeout(refreshHabits, 300);
}
async function logHabit(habit, action, extra = {}) {
  if (!state.live) {
    demoLog(habit, action, extra);
    refreshHabits();
    return;
  }
  try {
    state.habits = await api('/api/habits/log', { method: 'POST', body: { habit, action, ...extra } });
  } catch {
    toast(null, 'Could not save that. Is the server running?');
    return;
  }
  if (state.view === 'chat') renderTracker();
  if (state.view === 'habits') renderHabits();
}

function bar(value, goal, pace) {
  const pct = Math.max(0, Math.min(PERCENT, (value / Math.max(1, goal)) * PERCENT));
  const pacePct = pace != null ? Math.max(0, Math.min(PERCENT, (pace / Math.max(1, goal)) * PERCENT)) : null;
  return `<div class="bar" role="img" aria-label="${Math.round(pct)} percent"><i style="width:${pct}%"></i>${pacePct != null ? `<b style="left:${pacePct}%" title="Where the pace says you should be now"></b>` : ''}</div>`;
}

// ————— Trackers (panel on top of an agent's 1-on-1 chat) —————
function renderTracker() {
  const box = $('#tracker');
  const id = state.chat;
  const a = who(id);
  const kind = id === 'group' ? null : a.tracker;
  if (!kind) { box.hidden = true; return; }
  box.hidden = false;
  box.style.setProperty('--tint', tintVar(a.color));
  const h = state.habits;

  if (kind === 'todo') {
    box.innerHTML = `
      <ul class="todo">${todo.items.map((item, i) => `<li>
        <label><input type="checkbox" data-i="${i}" ${item.done ? 'checked' : ''}><span class="${item.done ? 'done' : ''}">${esc(item.text)}</span></label>
        <button type="button" class="x" data-del="${i}" aria-label="Remove ${esc(item.text)}">×</button></li>`).join('')}</ul>
      <form class="inline-add"><input type="text" maxlength="${ui.chat.todo_max_chars}" placeholder="Add a task" aria-label="New task"><button type="submit" class="btn-quiet">Add</button></form>`;
    box.querySelectorAll('input[type="checkbox"]').forEach((cb) => cb.addEventListener('change', () => {
      todo.items[+cb.dataset.i].done = cb.checked; todoChanged(id);
    }));
    box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
      todo.items.splice(+b.dataset.del, 1); todoChanged(id);
    }));
    box.querySelector('form').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = e.target.querySelector('input').value.trim();
      if (!v) return;
      todo.items.push({ text: v.slice(0, ui.chat.todo_max_chars), done: false });
      todoChanged(id);
      $('#tracker input[type="text"]')?.focus();
    });
    return;
  }
  if (!h) { box.innerHTML = '<p class="muted">Loading…</p>'; refreshHabits(); return; }

  if (kind === 'hydration') {
    const w = h.hydration;
    box.innerHTML = `<div class="tracker-row">
        <div class="stat"><strong>${liters(w.water_ml)}</strong><span>of ${liters(w.goal_ml)} today · ${w.sips} sips</span></div>
        <div class="tracker-grow">${bar(w.water_ml, w.goal_ml, w.pace_ml)}<span class="tracker-note">Pace for now: ${liters(w.pace_ml)}</span></div>
        <button type="button" class="btn-quiet" data-log="sip">+ Sip</button>
        <button type="button" class="btn-quiet" data-log="glass">+ Glass</button>
      </div>
      <p class="tracker-note">${w.last_sip_ts ? `Last sip ${agoText(w.last_sip_ts)}. ` : ''}A sip counts when the cup leaves the desk and comes back (about ${w.ml_per_sip} ml). <em>Protocols</em>: ${Math.round(w.on_waking_ml / ML_PER_OZ)} oz right after waking, then about ${liters(w.goal_ml)} in the first ${ui.habits.goal_window_hours} hours.</p>`;
    box.querySelector('[data-log="sip"]').addEventListener('click', () => logHabit('hydration', 'sip'));
    box.querySelector('[data-log="glass"]').addEventListener('click', () => logHabit('hydration', 'glass'));
    return;
  }

  if (kind === 'breaks') {
    const b = h.breaks;
    const v = h.vision || {};
    box.innerHTML = `<div class="tracker-row">
        <div class="tracker-stats">
          <div class="stat"><strong>${b.count}</strong><span>breaks today</span></div>
          <div class="stat"><strong>${mins(b.sitting_sec)}</strong><span>min sitting now (of ${b.sit_limit_min})</span></div>
          <div class="stat"><strong>${state.live ? mins(v.posture_bad_seconds_today) : '–'}</strong><span>min slouching</span></div>
        </div>
        <button type="button" class="btn-quiet" id="log-break">Log a break</button>
      </div>
      <p class="tracker-note">${b.last_break_ts ? `Last break ${agoText(b.last_break_ts)}. ` : ''}Breaks log themselves when you leave the desk for ${ui.habits.breaks.break_min} minutes. <em>Protocols</em>: walk ${ui.habits.breaks.break_min} minutes for every ${b.sit_limit_min} minutes of sitting, without your phone. <a class="link" data-open="habits">Habits</a></p>`;
    $('#log-break').addEventListener('click', () => logHabit('breaks', 'break'));
    return;
  }

  if (kind === 'food') {
    const entries = h.food.entries || [];
    box.innerHTML = `<div class="pills">${entries.map((e, i) => `<span class="pill ${e.healthy ? 'healthy' : 'treat'}">${esc(e.label)}<button type="button" data-del="${i}" aria-label="Remove ${esc(e.label)}">×</button></span>`).join('') || '<span class="muted">Nothing logged yet today.</span>'}</div>
      <form class="inline-add"><input type="text" maxlength="${ui.habits.food_label_max_chars}" placeholder="What did you eat?" aria-label="Food name">
        <button type="submit" class="btn-quiet" data-healthy="1">Healthy</button><button type="button" class="btn-quiet" data-healthy="0">Treat</button></form>`;
    const add = (healthy) => {
      const input = box.querySelector('input');
      const label = input.value.trim().slice(0, ui.habits.food_label_max_chars);
      if (!label) { input.focus(); return; }
      logHabit('food', 'food', { label, healthy });
    };
    box.querySelector('form').addEventListener('submit', (e) => { e.preventDefault(); add(true); });
    box.querySelector('[data-healthy="0"]').addEventListener('click', () => add(false));
    box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => logHabit('food', 'remove', { index: +b.dataset.del })));
    return;
  }

  if (kind === 'phone_free') {
    const p = h.phone_free;
    box.innerHTML = p.counted ? `<div class="tracker-row">
        <div class="stat"><strong>${mins(p.phone_free_sec)} min</strong><span>phone-free desk time (goal ${p.goal_min})</span></div>
        <div class="tracker-grow">${bar(mins(p.phone_free_sec), p.goal_min)}</div>
        <div class="stat"><strong>${mins(p.streak_sec)}</strong><span>min streak</span></div>
      </div>
      <p class="tracker-note">${p.phone_on_desk ? 'The phone is on the desk right now.' : 'No phone on the desk right now.'} <em>Protocols</em>: put your phone away while you focus; even having it nearby costs attention.</p>`
      : '<p class="tracker-note">The phone is the camera right now, so phone-free time isn’t counted.</p>';
    return;
  }

  if (kind === 'light') {
    const l = h.light;
    const sun = ui.habits.sunlight_default_min;
    const level = l.lux != null ? `${Math.round(l.lux)} lux` : (l.brightness != null ? `${Math.round(l.brightness)}/${LEVEL_MAX}` : '–');
    box.innerHTML = `<div class="tracker-row">
        <div class="stat"><strong>${l.sunlight_ts ? '✓' : '–'}</strong><span>morning sunlight</span></div>
        <div class="stat"><strong>${level}</strong><span>room light now</span></div>
        <div class="stat"><strong>${clock(h.times.sunset_ts)}</strong><span>sunset</span></div>
        <button type="button" class="btn-quiet" data-sun="${sun}">I got sunlight</button>
      </div>
      <p class="tracker-note"><em>Protocols</em>: ${morningLight()}, some again before sunset, and dim lights in the last ${duration(ui.habits.evening_dim_before_bed_min)} before bed.</p>`;
    box.querySelector('[data-sun]').addEventListener('click', () => logHabit('light', 'sunlight', { minutes: sun }));
  }
}

/** 'sunlight within an hour of waking (10 min clear, 20 cloudy, 30 overcast)', from protocols.yaml → light. */
function morningLight(extra = '') {
  const m = ui.habits.morning_minutes;
  return `sunlight within ${duration(ui.habits.morning_window_min)} of waking (${m.clear} min clear, ${m.partly_cloudy} cloudy, ${m.overcast} overcast${extra})`;
}

// ————— Habits page —————
// Your own counters live only in this browser, so their label length is the page's own.
const COUNTER_LABEL_CHARS = 60;
async function renderHabits() {
  const page = $('#view-habits');
  if (!state.habits) await refreshHabits();
  const h = state.habits || demoHabits();
  const vdata = state.live ? await api(`/api/habit_log?category=${state.habitFilter}&limit=${ui.web.habit_log_shown}`).catch(() => null) : null;
  const v = h.vision || {};
  const events = vdata?.events || [];
  const w = h.hydration; const b = h.breaks; const p = h.phone_free; const l = h.light;
  const level = l.lux != null ? `${Math.round(l.lux)} lux` : (l.brightness != null ? `brightness ${Math.round(l.brightness)}/${LEVEL_MAX}` : 'no reading yet');
  const rules = ui.habits;
  const sunButtons = [['clear', 'Got'], ['partly_cloudy', ''], ['overcast', '']].map(([sky, before]) => {
    const n = rules.morning_minutes[sky];
    return `<button class="btn-quiet" data-log="light:sunlight:${n}">${before ? `${before} ` : ''}${n} min</button>`;
  }).join('');
  page.innerHTML = `
    <header><h1 class="page-title">Habits</h1>
      <p class="page-intro">Four habits from Andrew Huberman's <em>Protocols</em>, checked by the desk camera. Your day: up at ${clock(h.times.wake_ts)}, bed at ${clock(h.times.bed_ts)} (<a class="link" data-open="settings">change</a>).</p></header>
    <section class="protocols">
      <article class="habit-card" style="--tint:var(--teal-tint)">
        <h2>Water</h2>
        <p class="big">${liters(w.water_ml)} <small>of ${liters(w.goal_ml)}</small></p>
        ${bar(w.water_ml, w.goal_ml, w.pace_ml)}
        <p class="hint">${w.sips} sips. Pace for now: ${liters(w.pace_ml)}. About ${liters(w.on_waking_ml)} right after waking, then a glass about every waking hour; less in the ${duration(rules.stop_before_bed_hours * MINUTE)} before bed.</p>
        <div class="row"><button class="btn-quiet" data-log="hydration:sip">+ Sip</button><button class="btn-quiet" data-log="hydration:glass">+ Glass</button></div>
      </article>
      <article class="habit-card" style="--tint:var(--cobalt-tint)">
        <h2>Breaks</h2>
        <p class="big">${b.count} <small>breaks today</small></p>
        ${bar(mins(b.sitting_sec), b.sit_limit_min)}
        <p class="hint">Sitting for ${mins(b.sitting_sec)} min now; longest stretch today ${mins(b.longest_sit_sec)} min. Walk ${rules.breaks.break_min} minutes for every ${b.sit_limit_min} minutes of sitting${rules.breaks.walk_after_meal ? ', and a short walk after meals' : ''}. Leave the phone behind.</p>
        <div class="row"><button class="btn-quiet" data-log="breaks:break">Log a break</button></div>
      </article>
      <article class="habit-card" style="--tint:var(--mustard-tint)">
        <h2>Light</h2>
        <p class="big">${l.sunlight_ts ? 'Sunlight ✓' : 'No sunlight yet'}</p>
        <p class="hint">Room right now: ${esc(level)}. Sunset ${clock(h.times.sunset_ts)}. Morning ${morningLight('; not through a window')}, late-afternoon light before sunset, and dim lights in the last ${duration(rules.evening_dim_before_bed_min)} before bed.</p>
        <div class="row">${sunButtons}</div>
      </article>
      <article class="habit-card" style="--tint:var(--coral-tint)">
        <h2>Phone-free</h2>
        ${p.counted ? `<p class="big">${mins(p.phone_free_sec)} min <small>of ${p.goal_min}</small></p>
        ${bar(mins(p.phone_free_sec), p.goal_min)}
        <p class="hint">Desk time with no phone in sight. Streak now ${mins(p.streak_sec)} min, best ${mins(p.best_sec)} min. ${p.phone_on_desk ? 'The phone is on the desk.' : ''} Even a phone in the same room pulls at your focus; an hour before bed, put it away.</p>`
        : '<p class="hint">The phone is the camera right now, so this isn’t counted. Use the Raspberry Pi camera to track phone-free time.</p>'}
      </article>
    </section>
    <section><h2>Today</h2>
      <div class="today">
        <div class="stat"><strong>${mins(h.desk_sec)}</strong><span>minutes at the desk</span></div>
        <div class="stat"><strong>${h.danger_count}</strong><span>near-spill alerts</span></div>
        <div class="stat"><strong>${state.live ? (v.nail_biting_count_today ?? 0) : '–'}</strong><span>nail biting</span></div>
        <div class="stat"><strong>${(h.food.entries || []).length}</strong><span>foods logged</span></div>
      </div></section>
    <section><h2>Your own counters</h2>
      <p class="hint">Tap + whenever it happens. They reset every morning.</p>
      <ul class="counter-list">${counters.items.map((c, i) => `<li><span>${esc(c.label)}</span>
        <button type="button" class="round" data-dec="${i}" aria-label="Minus one">−</button><strong>${c.count}</strong>
        <button type="button" class="round" data-inc="${i}" aria-label="Plus one">+</button>
        <button type="button" class="round" data-del="${i}" aria-label="Remove ${esc(c.label)}">×</button></li>`).join('')}</ul>
      <form class="inline-add" id="add-habit" style="max-width:520px"><input type="text" maxlength="${COUNTER_LABEL_CHARS}" placeholder="Something else to count" aria-label="New counter"><button type="submit" class="btn-quiet">Add</button></form></section>
    <section><h2>Camera log</h2>
      <div class="chips" role="group" aria-label="Filter">${[['all', 'Everything'], ['posture_bad', 'Posture'], ['nail_biting', 'Nail biting']]
        .map(([k, label]) => `<button type="button" class="chip" data-filter="${k}" aria-pressed="${state.habitFilter === k}">${label}</button>`).join('')}</div>
      ${events.length ? `<ul class="log">${events.map((ev) => `<li><time>${clock(ev.ts || 0)}</time>
          ${ev.snapshot_id ? `<img src="/api/snapshot/${esc(ev.snapshot_id)}" alt="" data-photo="/api/snapshot/${esc(ev.snapshot_id)}">` : ''}
          <p>${esc(ev.title || '')}${ev.detail ? `<small>${esc(ev.detail)}</small>` : ''}</p></li>`).join('')}</ul>`
        : `<p class="muted">${state.live ? 'Nothing yet. Posture and nail-biting entries appear here when Ollama vision is set up.' : 'Run the server to see what the camera notices.'}</p>`}
    </section>`;

  page.querySelectorAll('[data-log]').forEach((btn) => btn.addEventListener('click', () => {
    const [habit, action, minutes] = btn.dataset.log.split(':');
    logHabit(habit, action, minutes ? { minutes: +minutes } : {});
  }));
  page.querySelectorAll('[data-inc],[data-dec],[data-del]').forEach((btn) => btn.addEventListener('click', () => {
    const i = +(btn.dataset.inc ?? btn.dataset.dec ?? btn.dataset.del);
    if (btn.dataset.inc) counters.items[i].count += 1;
    if (btn.dataset.dec) counters.items[i].count = Math.max(0, counters.items[i].count - 1);
    if (btn.dataset.del) counters.items.splice(i, 1);
    saveCounters(); renderHabits();
  }));
  $('#add-habit').addEventListener('submit', (e) => {
    e.preventDefault();
    const val = e.target.querySelector('input').value.trim();
    if (!val) return;
    counters.items.push({ label: val.slice(0, COUNTER_LABEL_CHARS), count: 0 });
    saveCounters(); renderHabits();
  });
  page.querySelectorAll('[data-filter]').forEach((btn) => btn.addEventListener('click', () => {
    state.habitFilter = btn.dataset.filter; renderHabits();
  }));
}

// ————— Event log page —————
const LOG_FILTERS = [
  ['all', 'Everything', null],
  ['agents', 'Joins & leaves', 'agent_joined,agent_left'],
  ['alerts', 'Alerts', 'alert'],
  ['habits', 'Habits', 'habit,nudge'],
  ['camera', 'Camera', 'camera,compaction'],
];
function eventLine(e) {
  const a = e.agent ? who(e.agent) : null;
  const cam = lens();
  switch (e.type) {
    case 'agent_joined': return [a || cam, `${e.name} joined the chat`, `seen as “${e.label}” (${Math.round((e.conf || 0) * PERCENT)}%)`];
    case 'agent_left': return [a || cam, `${e.name} left the chat`, `was here ${mins(e.stayed_sec)} min`];
    case 'alert': return [a || cam, e.text || `${who(e.agent).name}: ${e.rule_label || 'too close'}`, e.kind === 'proximity' ? `near ${who(e.near).name || ''}` : (e.feature || '')];
    case 'habit': return [cam, e.say || `${e.habit}: ${e.action}`, HABIT_NAMES[e.habit] || e.habit];
    case 'nudge': return [a || cam, e.text, `reminder · ${e.evidence || ''}`];
    case 'camera': return [cam, e.text, e.kind];
    case 'compaction': return [cam, `Compacted ${e.frames} pictures into ${e.intervals} intervals`, e.from ? `${clock(e.from)} to ${clock(e.to)}` : ''];
    default: return [cam, e.type, ''];
  }
}
/** One detection event as JSON, one object per line (like a line of detections.jsonl, but readable). */
function compactEvent(e) {
  const objs = (e.detected_obj || []).map((o) => `    ${JSON.stringify({ label: o.label, center: o.center, conf: o.conf })}`);
  return `{
  "type": "detection", "ts": ${e.ts}, "frame_id": ${JSON.stringify(e.frame_id)}, "source": ${JSON.stringify(e.source)},
  "detected_obj": [
${objs.join(',\n')}
  ],
  "light": ${JSON.stringify(e.light || {})}
}`;
}
const TIMELINE_ROWS = 14;                                         // things shown, the longest on the desk first
const TIMELINE_STEPS = [1, 5, 15, 30, 60, 120, 180].map((m) => m * MINUTE);   // time marks: the first that gives...
const TIMELINE_MARKS = 8;                                         // ...at most this many
function timelineHtml(intervals, dayStart, dayEnd) {
  const byLabel = {};
  for (const iv of intervals) (byLabel[iv.label] = byLabel[iv.label] || []).push(iv);
  const labels = Object.keys(byLabel).sort((x, y) => byLabel[y].reduce((s, i) => s + i.interval[1] - i.interval[0], 0)
    - byLabel[x].reduce((s, i) => s + i.interval[1] - i.interval[0], 0)).slice(0, TIMELINE_ROWS);
  if (!labels.length) return '<p class="muted">Nothing on the desk yet today.</p>';
  const span = Math.max(MINUTE, dayEnd - dayStart);
  const step = TIMELINE_STEPS.find((s) => span / s <= TIMELINE_MARKS) || 4 * HOUR;
  const ticks = [];
  for (let t = Math.ceil(dayStart / step) * step; t < dayEnd; t += step) ticks.push(t);
  return `<div class="timeline">
    ${labels.map((lab) => `<div class="tl-row"><span class="tl-label">${esc(lab)}</span><div class="tl-track">${byLabel[lab].map((iv) => {
      const left = ((iv.interval[0] - dayStart) / span) * PERCENT;
      const width = Math.max(0.4, ((iv.interval[1] - iv.interval[0]) / span) * PERCENT);
      return `<i style="left:${left}%;width:${width}%" title="${esc(lab)}: ${clock(iv.interval[0])} – ${clock(iv.interval[1])} (${iv.frames} pictures)"></i>`;
    }).join('')}</div></div>`).join('')}
    <div class="tl-row tl-axis"><span class="tl-label"></span><div class="tl-track">${ticks.map((t) => `<span style="left:${((t - dayStart) / span) * PERCENT}%">${clock(t)}</span>`).join('')}</div></div>
  </div>`;
}
async function renderLog() {
  const page = $('#view-log');
  const intro = 'Everything the camera turned into events, saved on this computer in <code>data/events/</code>.';
  if (!state.live) {
    page.innerHTML = `<header><h1 class="page-title">Event log</h1><p class="page-intro">${intro}</p></header>
      <p class="muted">Run the server to start the event log.</p>`;
    return;
  }
  const { fps, compact_every_min: compactMin, delete_every_min: deleteMin } = ui.pi;
  const head = `<header><h1 class="page-title">Event log</h1>
    <p class="page-intro">${intro} The camera takes ${fps} pictures a second; each one becomes a detection event, and every ${compactMin} minutes those are compacted into “on the desk from … to …” intervals. The pictures themselves stay on the Raspberry Pi${deleteMin ? ` and are deleted every ${deleteMin} minutes` : ''}.</p></header>`;
  const filter = LOG_FILTERS.find((f) => f[0] === state.logFilter) || LOG_FILTERS[0];
  const dayQ = state.logDay ? `day=${state.logDay}&` : '';
  const [days, status, evs, pres, det] = await Promise.all([
    api('/api/log/days').catch(() => null),
    api('/api/log/status').catch(() => null),
    api(`/api/log/events?${dayQ}limit=${ui.web.log_shown}${filter[2] ? `&types=${filter[2]}` : ''}`).catch(() => null),
    api(`/api/log/presence?${dayQ}`).catch(() => null),
    api(`/api/log/detections?${dayQ}limit=1`).catch(() => null),
  ]);
  const day = evs?.day || todayKey();
  const intervals = pres?.intervals || [];
  const nowS = Date.now() / MS;
  const first = intervals.length ? Math.min(...intervals.map((i) => i.interval[0])) : nowS - HOUR;
  const last = intervals.length ? Math.max(...intervals.map((i) => i.interval[1])) : nowS;
  const cam = status?.camera || {};
  const pi = cam.pi || {};
  const lc = status?.last_compaction;
  const lastDet = det?.detections?.[0];
  page.innerHTML = `${head}
    <section class="log-status">
      <div class="stat"><strong>${cam.connected ? `${cam.fps}/s` : 'off'}</strong><span>${esc(cam.source || '')} camera</span></div>
      <div class="stat"><strong>${status?.today?.pictures_waiting ?? 0}</strong><span>picture events waiting to compact</span></div>
      <div class="stat"><strong>${lc ? agoText(lc.ts) : '–'}</strong><span>last compaction${lc && lc.frames ? ` (${lc.frames} → ${lc.intervals})` : ''}</span></div>
      ${cam.source === 'pi' ? `<div class="stat"><strong>${pi.photos_stored ?? '–'}</strong><span>pictures on the Pi${pi.last_cleanup_ts ? `, cleaned ${agoText(pi.last_cleanup_ts)}` : ''}${pi.disk_free_mb != null ? `, ${pi.disk_free_mb} MB free` : ''}</span></div>` : ''}
      <button type="button" class="btn-quiet" id="compact-now">Compact now</button>
    </section>
    <section><h2>On the desk ${day === todayKey() ? 'today' : esc(day)}</h2>
      <div class="chips" role="group" aria-label="Day">${(days?.days || [day]).slice(0, 7).map((d) => `<button type="button" class="chip" data-day="${esc(d)}" aria-pressed="${d === day}">${d === todayKey() ? 'Today' : esc(d.slice(5))}</button>`).join('')}</div>
      ${timelineHtml(intervals, first - MINUTE, Math.max(last + MINUTE, day === todayKey() ? nowS : last + MINUTE))}</section>
    <section><h2>Events</h2>
      <div class="chips" role="group" aria-label="Filter">${LOG_FILTERS.map(([k, label]) => `<button type="button" class="chip" data-filter="${k}" aria-pressed="${state.logFilter === k}">${label}</button>`).join('')}</div>
      ${(evs?.events || []).length ? `<ul class="log events">${evs.events.map((e) => {
        const [a, title, sub] = eventLine(e);
        return `<li><time>${clock(e.ts)}</time>${avatar(a, 28)}<p>${esc(title)}${sub ? `<small>${esc(sub)}</small>` : ''}</p></li>`;
      }).join('')}</ul>` : '<p class="muted">No events yet.</p>'}</section>
    ${lastDet ? `<section><h2>The newest picture's event</h2><p class="hint">What one of the ${fps}-a-second detection events looks like in <code>detections.jsonl</code>.</p>
      <pre class="json">${esc(compactEvent(lastDet))}</pre></section>` : ''}`;
  $('#compact-now').addEventListener('click', async () => {
    const r = await api('/api/log/compact', { method: 'POST' }).catch(() => null);
    toast(null, r ? `Compacted ${r.frames} pictures into ${r.intervals} intervals` : 'Could not compact');
    renderLog();
  });
  page.querySelectorAll('[data-filter]').forEach((b) => b.addEventListener('click', () => { state.logFilter = b.dataset.filter; renderLog(); }));
  page.querySelectorAll('[data-day]').forEach((b) => b.addEventListener('click', () => { state.logDay = b.dataset.day; renderLog(); }));
}

// ————— Captures page —————
async function renderCaptures() {
  const page = $('#view-captures');
  const head = `<header><h1 class="page-title">Captures</h1>
    <p class="page-intro">Photos the desk cam saved: proof for alerts, and snapshots when easy-to-lose things show up.</p></header>`;
  if (!state.live) { page.innerHTML = `${head}<p class="muted">Run the server to collect desk photos.</p>`; return; }
  const data = await api('/api/snapshots').catch(() => null);
  const items = data?.items || [];
  page.innerHTML = head + (items.length
    ? `<div class="gallery">${items.map((it) => `<button type="button" data-photo="/api/snapshot/${esc(it.id)}">
        <img src="/api/snapshot/${esc(it.id)}" alt="" loading="lazy">
        <strong>${esc(it.label || 'Capture')}</strong><span>${new Date((it.ts || 0) * MS).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</span></button>`).join('')}</div>`
    : '<p class="muted">No photos yet. They appear when something gets too close, or when your phone, keys, or wallet show up.</p>');
}

// ————— Settings page —————
// The sliders' ranges, in pixels, and where a new danger zone's slider starts.
const SLIDERS = { zone: [10, 200], warn: [20, 200], alert: [10, 120] };
const NEW_ZONE_PX = 60;
async function renderSettings() {
  const page = $('#view-settings');
  const head = `<header><h1 class="page-title">Settings</h1>
    <p class="page-intro">Your day, how close is too close, and when the desk should stay quiet. Everything else is in <code>config/settings.yaml</code>.</p></header>`;
  if (!state.live) { page.innerHTML = `${head}<p class="muted">Settings are saved by the server. Run python server.py to change them.</p>`; return; }
  let rules; let prefs;
  try {
    [rules, prefs] = await Promise.all([api('/api/rules'), api('/api/preferences')]);
  } catch {
    page.innerHTML = `${head}<p class="muted">Could not load settings from the server.</p>`;
    return;
  }
  const zones = { ...(rules.danger_zones || {}) };
  const builtIn = ui.desk.edge_proximity;
  const edge = { danger_px: builtIn.danger_px, warn_px: builtIn.warn_px, ...(rules.edge_proximity || {}) };
  let outline = (rules.desk_outline || []).map(([x, y]) => [x, y]);
  let outlineChanged = false;
  let startOver = outline.length >= ui.desk.min_desk_corners;   // a saved outline: the first tap starts a new one
  let picSize = null;
  const range = (name, value, [min, max]) => `<label class="range"><input type="range" name="${name}" min="${min}" max="${max}" value="${value}"><output>${value} px</output></label>`;

  const draw = () => {
    page.innerHTML = `${head}
    <section><h2>Your day</h2>
      <p class="hint">The habit reminders are timed from these: water right after waking, sunlight in the first hour, dim lights and no screens in the last hour before bed.</p>
      <div class="form-rows">
        <div class="field"><span>I wake up at</span><input type="time" id="wake-time" value="${esc(prefs.wake_time)}"></div>
        <div class="field"><span>I go to bed at</span><input type="time" id="bed-time" value="${esc(prefs.bed_time)}"></div>
      </div></section>
    <section><h2>Danger zones</h2>
      <p class="hint">Each object gets an invisible bubble around it. When something risky enters the bubble (a cup near the laptop), the desk speaks up.</p>
      <div class="form-rows">${Object.entries(zones).map(([cls, z]) => `<div class="zone" data-zone="${esc(cls)}">
          <strong>${esc(cls)}</strong>
          <input type="text" name="label" value="${esc(z.label || '')}" aria-label="Name for the ${esc(cls)} zone">
          ${range('expand', z.expand || NEW_ZONE_PX, SLIDERS.zone)}
          <button type="button" class="round" data-remove aria-label="Remove ${esc(cls)} zone">×</button></div>`).join('') || '<p class="muted">No zones yet.</p>'}
        <form class="inline-add" id="add-zone"><select aria-label="Object">${(rules.available_objects || []).filter((o) => !zones[o]).map((o) => `<option>${esc(o)}</option>`).join('')}</select>
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
      <div class="form-rows">
        <div class="field"><span>Warn at</span>${range('warn_px', edge.warn_px, SLIDERS.warn)}</div>
        <div class="field"><span>Alert at</span>${range('danger_px', edge.danger_px, SLIDERS.alert)}</div>
      </div></section>
    <section><h2>Quiet hours</h2>
      <p class="hint">Habit reminders wait until quiet hours end. Safety alerts still come through.</p>
      <div class="form-rows">
        <label class="check"><input type="checkbox" id="dnd-on" ${prefs.quiet_enabled ? 'checked' : ''}> Use quiet hours</label>
        <div class="field"><span>From</span><input type="number" id="dnd-start" min="0" max="23" value="${prefs.quiet_start_hour ?? ui.quiet_hours.start_hour}"><span>to</span><input type="number" id="dnd-end" min="0" max="23" value="${prefs.quiet_end_hour ?? ui.quiet_hours.end_hour}"><span class="muted">(24-hour clock)</span></div>
      </div></section>
    <div class="save-row"><button type="button" class="btn" id="save-settings">Save settings</button><span class="saved" id="saved" role="status"></span></div>
    ${state.cameraSource === 'pi' && state.piCommand ? `<section><h2>Raspberry Pi camera</h2>
      <p class="hint">On the Pi, in the desk-talk folder, run:</p><pre class="json">${esc(state.piCommand)}</pre></section>` : ''}
    <section><h2>Chats</h2>
      <p class="hint">Your chats are saved in this browser so they survive a refresh.</p>
      <button type="button" class="btn-quiet" id="clear-chats">Clear all chats</button></section>`;

    page.querySelectorAll('.range input').forEach((r) => r.addEventListener('input', () => {
      r.nextElementSibling.textContent = `${r.value} px`;
      const z = r.closest('.zone');
      if (z) zones[z.dataset.zone].expand = +r.value;
      else edge[r.name] = +r.value;
    }));
    page.querySelectorAll('.zone input[name="label"]').forEach((inp) => inp.addEventListener('input', () => {
      zones[inp.closest('.zone').dataset.zone].label = inp.value;
    }));
    page.querySelectorAll('[data-remove]').forEach((b) => b.addEventListener('click', () => {
      delete zones[b.closest('.zone').dataset.zone]; draw();
    }));
    $('#add-zone')?.addEventListener('submit', (e) => {
      e.preventDefault();
      const cls = e.target.querySelector('select').value;
      if (cls) { zones[cls] = { expand: NEW_ZONE_PX, label: `${cls} zone` }; draw(); }
    });
    wireEdgeEditor();
    $('#clear-chats').addEventListener('click', () => {
      for (const id of Object.keys(state.messages)) { state.messages[id] = []; state.unread[id] = 0; }
      storage.save(HISTORY_KEY, null);
      renderChatList();
      toast(null, 'Chats cleared');
    });
    $('#save-settings').addEventListener('click', async () => {
      const out = $('#saved');
      out.className = 'saved'; out.textContent = 'Saving…';
      if (outline.length && outline.length < ui.desk.min_desk_corners) {
        out.className = 'saved error'; out.textContent = `Tap at least ${ui.desk.min_desk_corners} corners of the desk, or press Clear.`;
        return;
      }
      try {
        const body = { danger_zones: zones, edge_proximity: { ...edge } };
        if (outlineChanged) body.desk_outline = outline;
        await api('/api/rules', { method: 'PUT', body });
        outlineChanged = false;
        prefs = (await api('/api/preferences', { method: 'PUT', body: {
          wake_time: $('#wake-time').value, bed_time: $('#bed-time').value,
          quiet_enabled: $('#dnd-on').checked,
          quiet_start_hour: +$('#dnd-start').value || 0,
          quiet_end_hour: +$('#dnd-end').value || 0,
        } })).preferences;
        out.textContent = 'Saved';
        refreshHabits();
      } catch {
        out.className = 'saved error'; out.textContent = 'Not saved. Check that the server is running and the times look like 07:30.';
      }
    });
  };

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
      img.addEventListener('error', () => { $('#edge-status').textContent = 'Could not load the camera picture. Try New picture.'; });
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

function renderCamStage() {
  const stage = $('#cam-stage');
  $('#cam-caption')?.remove();
  stage.classList.toggle('is-pairing', state.live && !state.camOk && state.cameraSource !== 'webcam');
  stage.classList.toggle('has-video', state.live && state.camOk);
  if (!state.live) {
    stage.innerHTML = '<img class="scene" src="art/desk-scene.svg" alt="Illustration of the desk characters">';
    stage.insertAdjacentHTML('afterend', '<p class="cam-caption" id="cam-caption">This is a demo. Run the server and start the camera to see your real desk.</p>');
    return;
  }
  if (state.camOk) {
    stage.innerHTML = `<img src="${feedUrl()}" alt="Live view of your desk">`;
    return;
  }
  if (state.cameraSource === 'webcam') {
    stage.innerHTML = '<p class="cam-caption">No webcam found. Check the terminal running server.py for tips.</p>';
    return;
  }
  if (state.cameraSource === 'pi') {
    stage.innerHTML = `<div class="pair">
      <h3>Start the Raspberry Pi camera</h3>
      <ol>
        <li>Put the Pi on the same Wi-Fi as this computer.</li>
        <li>On the Pi, in the desk-talk folder, run:</li>
      </ol>
      <pre class="json pi-cmd">${esc(state.piCommand || 'python3 -m raspberry_pi.camera --server … --key … (printed by server.py)')}</pre>
      ${state.piCommand ? '<button type="button" class="btn-quiet" id="copy-pi">Copy command</button>' : ''}
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
    : '<div class="pair"><h3>Starting the phone link…</h3><p class="muted">If this does not change, check the terminal running server.py.</p></div>';
}

function renderOnDesk(det) {
  const list = $('#on-desk');
  const here = agentIds().filter((id) => state.agents[id].on_desk);
  if (!state.live) {
    list.innerHTML = here.map((id) => `<li style="--tint:${tintVar(state.agents[id].color)}">${avatar(state.agents[id], 26)}${esc(state.agents[id].name)}</li>`).join('')
      || '<li class="extra">Nobody yet</li>';
    return;
  }
  const objs = det?.objects || {};
  const extras = (det?.extras || []).filter((e) => !e.ignored).slice(0, 8);
  if (!here.length && !extras.length) {
    list.innerHTML = `<li class="extra">${state.camOk ? 'Nothing recognized yet' : 'Waiting for the camera'}</li>`;
    return;
  }
  list.innerHTML = here.map((id) => {
    const a = state.agents[id];
    const o = objs[id];
    return `<li style="--tint:${tintVar(a.color)}" title="${o ? `Detected as ${esc(o.class)}` : ''}">${avatar(a, 26)}${esc(a.name)}${o ? ` <small>${Math.round((o.confidence || 0) * PERCENT)}%</small>` : ''}</li>`;
  }).join('') + extras.map((e) => `<li class="extra" title="Seen, not in the chat yet">${esc(e.yolo_class)}</li>`).join('');
}

function renderRisks(safety, cameraIssues) {
  const list = $('#risks');
  if (!state.live) { list.innerHTML = '<li>Cup is a little close to Computer.</li>'; return; }
  const lens = (cameraIssues || []).slice(0, 2).map((i) => `<li class="lens-issue">
      ${avatar(lens(), 30)}<span><strong>${esc(i.title)}.</strong> ${esc(i.fix)}</span></li>`);
  const lines = (safety?.explanations || []).slice(0, 6).map((l) => `<li>${esc(l)}</li>`);
  const html = [...lens, ...lines].join('') || `<li class="calm">${state.camOk ? 'All calm on the desk.' : 'Nothing to report yet.'}</li>`;
  if (list.innerHTML !== html) list.innerHTML = html;
}

async function renderProofs() {
  const box = $('#proofs');
  if (!state.live) { box.innerHTML = '<p class="muted" style="grid-column:1/-1">Proof photos from alerts show up here.</p>'; return; }
  const data = await api('/api/snapshots').catch(() => null);
  const items = (data?.items || []).slice(0, ui.web.photos_shown);
  const ids = items.map((i) => i.id).join(',');
  if (ids === state.snapshotIds) return;
  state.snapshotIds = ids;
  box.innerHTML = items.length
    ? items.map((it) => `<img src="/api/snapshot/${esc(it.id)}" alt="${esc(it.label || 'Desk photo')}" title="${esc(it.label || '')}" data-photo="/api/snapshot/${esc(it.id)}" loading="lazy">`).join('')
    : '<p class="muted" style="grid-column:1/-1">None yet. Photos are saved when something gets too close.</p>';
}

/** One request every web.refresh_ms.live: camera status, agents, detections, and risks together. When the server
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
  state.phoneMode = d.phone_mode;
  state.camOk = d.camera;
  state.camIndex = d.camera_index ?? 0;
  if (d.pi_command && d.pi_command !== state.piCommand) { state.piCommand = d.pi_command; if (!state.camOk) renderCamStage(); }
  if (d.phone && (d.phone.url || d.phone.qr_svg)) state.phone = { ...state.phone, ...d.phone };
  if (state.camOk !== wasOk) {
    renderCamStage();
    renderConn();
    toast(null, state.camOk ? 'Camera connected' : 'Camera disconnected');
  } else if (!state.camOk && state.phone && !$('#cam-stage .pair-qr') && state.phone.qr_svg) {
    renderCamStage();
  }
  syncAgents(d.agents);
  renderOnDesk(d.detections);
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
  if (!on) renderCamStage();          // the live video stopped with the server: start it again
}

function renderAi(ai) {
  if (!ai) return;
  let text = '';
  if (!ai.ready) text = 'The characters need an AI to chat. Add a key to .env (GEMINI_API_KEY is free) or set OLLAMA_URL, then restart the server.';
  else if (ai.problem) text = ai.problem;
  if (text === state.aiText) return;
  state.aiText = text;
  const el = $('#ai-banner');
  el.textContent = text;
  el.hidden = !text;
}

// ————— Live connection —————
/** Messages and join / leave notes from before this page opened (the server keeps the newest few: chat.recent_kept). */
async function loadRecentMessages() {
  for (const list of Object.values(state.messages)) for (const m of list) if (m.id) state.seen.add(m.id);
  let recent = [];
  try { recent = (await api('/api/messages/recent')).messages || []; } catch { return; }
  for (const d of recent) handleServerMessage(d, true);
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
  es.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }
    handleServerMessage(d);
  };
}

/** Run `job` again and again, waiting `ms()` between the end of one run and the start of the next (read each time,
 * so settings that changed while the server was away count). */
function repeat(ms, job) {
  const next = async () => {
    try { await job(); } finally { setTimeout(next, ms()); }
  };
  setTimeout(next, ms());
}

async function startLive(status) {
  state.live = true;
  loadHistory();
  state.cameraSource = status.camera_source || 'pi';
  state.phoneMode = !!status.phone_mode;
  state.camOk = !!status.camera;
  state.camIndex = status.camera_indices?.[0] ?? 0;
  try { syncAgents((await api('/api/agents')).agents); } catch { /* the poll will catch up */ }
  syncTodos();
  listenForEvents();
  await loadRecentMessages();
  renderConn();
  renderCamStage();
  await pollLive();
  repeat(() => ui.web.refresh_ms.live, pollLive);
  refreshHabits();
  repeat(() => ui.web.refresh_ms.habits, refreshHabits);
  renderProofs();
  repeat(() => ui.web.refresh_ms.photos, renderProofs);
  repeat(() => ui.web.refresh_ms.log, async () => { if (state.view === 'log') await renderLog(); });
  renderAi({ ready: !!status.llm });
}

// ————— Demo (no server, e.g. GitHub Pages) —————
// Everything below is the demo's made-up story. The numbers in it are only for the story: the real app takes its
// numbers from the server (GET /api/ui_settings).
const DEMO_HOW_TO_RUN = 'Run python server.py on this computer and open http://localhost:5000 to use your real desk.';
// The settings the demo needs to tell its story (in the real app: config/settings.yaml and protocols.yaml).
const DEMO_UI = {
  web: {
    toast_ms: 4000, chat_kept: 200, chat_saved: 60, left_chat_shown_min: 60,
    palette: ['cobalt', 'teal', 'tang', 'mustard', 'coral', 'plum', 'moss', 'slate', 'ink'],
  },
  chat: { todo_max_chars: 200 },
  habits: {
    sunlight_default_min: 10, glass_ml: 240, food_label_max_chars: 60, goal_window_hours: 10, stop_before_bed_hours: 2,
    morning_minutes: { clear: 10, partly_cloudy: 20, overcast: 30 }, morning_window_min: 60, evening_dim_before_bed_min: 60,
    breaks: { break_min: 5, walk_after_meal: true },
  },
};
const DEMO_AGENTS = [
  { id: 'computer', name: 'Computer', art: 'art/computer.svg', color: 'cobalt', tracker: 'breaks', about: 'Anxious. Watches your screen breaks, posture, and anything near the keyboard.', labels: ['laptop'] },
  { id: 'cup', name: 'Cup', art: 'art/cup.svg', color: 'teal', tracker: 'hydration', about: 'Completely innocent. Counts your sips and insists it is nowhere near the laptop.', labels: ['cup'] },
  { id: 'snack', name: 'Snack', art: 'art/snack.svg', color: 'tang', tracker: 'food', about: 'Lives for flavor. Logs what you eat and does not care about crumbs.', labels: ['pizza'] },
  { id: 'paper', name: 'Paper', art: 'art/paper.svg', color: 'mustard', tracker: 'todo', about: 'A gentle worrier. Keeps your to-do list and remembers where things went.', labels: ['book'] },
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
    light: { sunlight_ts: now - 4 * HOUR, afternoon_light_ts: null, sunlight_minutes: 10, lux: 310, brightness: 120 },
    food: { entries: [{ label: 'apple', healthy: true }, { label: 'chips', healthy: false }] },
    desk_sec: 3 * HOUR, danger_count: 2, lost_alerts: 0, hourly_activity: [], objects_seen: [], vision: {},
  };
}
function demoLog(habit, action, extra) {
  const h = state.habits || demoHabits();
  const now = Date.now() / MS;
  if (habit === 'hydration') { h.hydration.sips += 1; h.hydration.water_ml += action === 'glass' ? ui.habits.glass_ml : h.hydration.ml_per_sip; h.hydration.last_sip_ts = now; }
  if (habit === 'breaks') { h.breaks.count += 1; h.breaks.sitting_sec = 0; h.breaks.last_break_ts = now; }
  if (habit === 'light') h.light.sunlight_ts = now;
  if (habit === 'food' && action === 'food') h.food.entries.push({ label: extra.label, healthy: !!extra.healthy });
  if (habit === 'food' && action === 'remove') h.food.entries.splice(extra.index, 1);
  state.habits = h;
}
async function playDemoOnce() {
  if (demoPlayed || state.live) return;
  demoPlayed = true;
  addMessage('group', { note: true, text: 'Demo. With the server running, objects your camera sees join this chat.' });
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
    addMessage('group', { char: id, text });
  }
}
/** No server when the page opened: run the demo. */
function startDemo() {
  state.live = false;
  ui = DEMO_UI;
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
  addMessage(chatId, { char: id, text: `I'm only a demo right now. Run the server and I'll actually see your desk.` });
}

// ————— Wiring —————
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
  $('#cam-toggle').addEventListener('click', () => $('#cam').classList.add('open'));
  $('#cam-close').addEventListener('click', () => $('#cam').classList.remove('open'));
  $('#conn').addEventListener('click', () => {
    if (!state.live) toast(null, `Demo mode. ${DEMO_HOW_TO_RUN}`);
    else if (!state.camOk) $('#cam').classList.add('open');
  });
}

/** Is Desk Talk's server there? Its settings and status, or null (opened from a file, or from GitHub Pages). */
async function firstCheck() {
  if (!location.protocol.startsWith('http')) return null;
  try {
    const wait = { timeout: FIRST_CHECK_MS };
    const [settings, status] = await Promise.all([api('/api/ui_settings', wait), api('/api/status', wait)]);
    ui = settings;
    return status;
  } catch {
    return null;
  }
}

async function init() {
  wire();
  const status = await firstCheck();
  if (status) await startLive(status);
  else startDemo();
  openChat('group');
}

document.addEventListener('DOMContentLoaded', init);
