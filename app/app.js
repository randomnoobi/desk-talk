/* ============================================
   DESK TALK — computer app
   Left: chats. Center: the open chat or page.
   Right: what the phone camera sees.
   Without a server (e.g. GitHub Pages) it runs as a demo.
   ============================================ */
'use strict';

// ————— Characters (ids match backend/server.py) —————
const CHARACTERS = {
  monty: {
    name: 'Computer', art: 'art/computer.svg', color: 'var(--cobalt)', tint: 'var(--cobalt-tint)',
    about: 'Anxious. Watches your screen breaks, posture, and anything near the keyboard.',
    empty: 'Computer speaks up when something gets too close or you skip a break.',
  },
  glug: {
    name: 'Cup', art: 'art/cup.svg', color: 'var(--teal)', tint: 'var(--teal-tint)',
    about: 'Completely innocent. Counts your sips and insists it is nowhere near the laptop.',
    empty: 'Cup checks in about water. It notices when you pick it up and put it back.',
  },
  munch: {
    name: 'Snack', art: 'art/snack.svg', color: 'var(--tang)', tint: 'var(--tang-tint)',
    about: 'Lives for flavor. Logs what you eat and does not care about crumbs.',
    empty: 'Put food on your desk and Snack will have opinions.',
  },
  sheets: {
    name: 'Paper', art: 'art/paper.svg', color: 'var(--mustard)', tint: 'var(--mustard-tint)',
    about: 'A gentle worrier. Keeps your to-do list and remembers where things went.',
    empty: 'Ask Paper where you left your keys, or add a task above.',
  },
};
const GROUP = {
  name: 'The desk', art: 'art/desk.svg', color: 'var(--ink)', tint: 'var(--mist)',
  about: 'Everyone on your desk, talking to you and to each other.',
  empty: 'When the camera sees something worth talking about, the desk starts chatting here. Type @ to talk to one of them.',
};
// Lens is the phone camera. It only talks in the group chat, about the camera view.
const LENS = {
  name: 'Lens', art: 'art/lens.svg', color: 'var(--coral-ink)', tint: 'var(--coral-tint)',
  about: 'The phone camera. Speaks up when the view needs fixing.',
};
const CHAT_IDS = ['group', 'monty', 'glug', 'munch', 'sheets'];
const CHAR_IDS = CHAT_IDS.slice(1);
const who = (id) => (id === 'group' ? GROUP : CHARACTERS[id]);
/** Whoever wrote a message: a character, Lens, or the desk as a whole. */
const speaker = (id) => CHARACTERS[id] || (id === 'lens' ? LENS : GROUP);

// Words people type for each character in @mentions.
const ALIASES = {
  computer: 'monty', laptop: 'monty', keyboard: 'monty', monty: 'monty',
  cup: 'glug', water: 'glug', bottle: 'glug', glug: 'glug',
  snack: 'munch', food: 'munch', munch: 'munch',
  paper: 'sheets', homework: 'sheets', sheets: 'sheets',
};

// ————— Small helpers —————
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const todayKey = () => new Date().toISOString().slice(0, 10);

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function clock(tsSeconds) {
  return new Date(tsSeconds * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}
function timeAgo(iso) {
  if (!iso) return '';
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

/** fetch → JSON, with a timeout. Throws on network errors and non-2xx. */
async function api(path, { method = 'GET', body, timeout = 8000 } = {}) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
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
  live: false,          // the Python server is reachable
  phoneMode: false,     // the server expects the phone as its camera
  camOk: false,         // frames are arriving right now
  camIndex: 0,
  feed: 'analysis',     // 'analysis' (YOLO boxes) or 'clean'
  phone: null,          // { url, qr_svg } for pairing
  view: 'chat',
  chat: 'group',
  messages: Object.fromEntries(CHAT_IDS.map((id) => [id, []])),
  unread: Object.fromEntries(CHAT_IDS.map((id) => [id, 0])),
  mentionOptions: [],
  habitFilter: 'all',
  snapshotIds: null,     // ids of the proof photos on screen (null = not drawn yet)
  seen: new Set(),       // message ids already shown (replies can arrive twice)
  aiText: '',            // what the AI banner says right now
};

// ————— Chat history (kept in this browser so a refresh doesn't wipe the chats) —————
const HISTORY_KEY = 'desktalk_chats_v1';
let historyTimer = null;
function saveHistory() {
  if (!state.live) return;            // the demo never saves
  clearTimeout(historyTimer);
  historyTimer = setTimeout(() => {
    const chats = Object.fromEntries(CHAT_IDS.map((id) => [id, state.messages[id].filter((m) => !m.note).slice(-60)]));
    storage.save(HISTORY_KEY, { saved: Date.now(), chats });
  }, 400);
}
function loadHistory() {
  const d = storage.load(HISTORY_KEY, null);
  if (!d?.chats) return;
  for (const id of CHAT_IDS) {
    if (Array.isArray(d.chats[id])) state.messages[id] = d.chats[id].filter((m) => m && typeof m.text === 'string');
  }
}

// ————— Trackers (per-character widgets, saved in the browser, synced to the server) —————
const TRACKER_KEY = 'desktalk_trackers_v3';
function freshTracker(id) {
  const day = todayKey();
  if (id === 'sheets') return { type: 'todo', items: [{ text: 'Pick one top priority for the next hour', done: false }] };
  if (id === 'monty') return { type: 'break_timer', last_break: null, breaks_today: 0, day };
  if (id === 'glug') return { type: 'sip_counter', sips: 0, goal: 8, last_sip: null, day };
  return { type: 'snack_log', entries: [], day };
}
const trackers = (() => {
  const saved = storage.load(TRACKER_KEY, {});
  const out = {};
  for (const id of CHAR_IDS) {
    const fresh = freshTracker(id);
    const t = saved[id];
    // Daily counters reset each day; the to-do list carries over.
    out[id] = t && t.type === fresh.type && (t.type === 'todo' || t.day === fresh.day) ? t : fresh;
  }
  return out;
})();
const saveTrackers = () => storage.save(TRACKER_KEY, trackers);

/** The shape server.py's _normalize_widget() expects. */
function trackerPayload(id) {
  const t = trackers[id];
  if (!t) return null;
  if (t.type === 'todo') return { type: 'todo', items: t.items };
  if (t.type === 'break_timer') return { type: 'break_timer', last_break: t.last_break, breaks_today: t.breaks_today };
  if (t.type === 'sip_counter') return { type: 'sip_counter', sips: t.sips, goal: t.goal, last_sip: t.last_sip };
  return { type: 'snack_log', entries: t.entries };
}
function trackerChanged(id) {
  saveTrackers();
  if (state.chat === id && state.view === 'chat') renderTracker();
  if (state.live) api('/api/chat_todos', { method: 'POST', body: { chat: id, widget: trackerPayload(id) } }).catch(() => {});
}

// ————— Custom habit counters (Habits page) —————
const HABIT_KEY = 'desktalk_habits_v2';
const habits = (() => {
  const saved = storage.load(HABIT_KEY, null);
  if (saved && saved.day === todayKey() && Array.isArray(saved.items)) return saved;
  const labels = saved?.items?.map((i) => i.label) || ['Nail biting', 'Picked up phone', 'Slouching'];
  return { day: todayKey(), items: labels.map((label) => ({ label, count: 0 })) };
})();
const saveHabits = () => storage.save(HABIT_KEY, habits);

// ————— Sidebar —————
function renderChatList() {
  const list = $('#chat-list');
  list.innerHTML = CHAT_IDS.map((id) => {
    const c = who(id);
    const last = state.messages[id].findLast((m) => !m.note);
    let preview = id === 'group' ? 'Everyone' : c.about.split('.')[0];
    if (last?.mine) preview = `You: ${last.text}`;
    else if (last) preview = id === 'group' ? `${speaker(last.char).name}: ${last.text}` : last.text;
    const unread = state.unread[id];
    const current = state.view === 'chat' && state.chat === id;
    return `<button type="button" class="chat-link" data-chat="${id}" aria-current="${current}">
      <img src="${c.art}" alt="" width="44" height="44">
      <span class="chat-link-text"><span class="chat-link-name">${esc(c.name)}</span>
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
  if (state.live) {
    if (!state.phoneMode) { st = state.camOk ? 'live' : 'waiting'; text = state.camOk ? 'Webcam on' : 'Webcam not found'; }
    else if (state.camOk) { st = 'live'; text = 'Phone camera on'; }
    else { st = 'waiting'; text = 'Connect your phone'; }
  }
  el.dataset.state = st;
  $('#conn-text').textContent = text;
  el.title = st === 'demo'
    ? 'Run python server.py on this computer and open http://localhost:5000 to use your real desk.'
    : '';
}

// ————— Navigation —————
function openChat(id) {
  state.view = 'chat';
  state.chat = id;
  state.unread[id] = 0;
  $$('.view').forEach((v) => { v.hidden = v.id !== 'view-chat'; });
  const c = who(id);
  $('#chat-art').src = c.art;
  $('#chat-name').textContent = c.name;
  $('#chat-about').textContent = c.about;
  renderTracker();
  renderMessages();
  renderChatList();
  renderPageLinks();
  $('#input').focus();
  if (state.live && id !== 'group') {
    api('/api/widget_event', { method: 'POST', body: { chat: id, action: 'view' } }).catch(() => {});
  }
  if (!state.live && id === 'group') playDemoOnce();
}

function openPage(view) {
  state.view = view;
  $$('.view').forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  renderChatList();
  renderPageLinks();
  if (view === 'habits') renderHabits();
  if (view === 'captures') renderCaptures();
  if (view === 'settings') renderSettings();
}

// ————— Messages —————
function formatText(text) {
  let s = esc(text);
  s = s.replace(/@user\b/g, '<span class="mention-you">@you</span>');
  s = s.replace(/@(\w+)/g, (m, word) => {
    const id = ALIASES[word.toLowerCase()];
    if (!id) return m;
    return `<span class="mention" style="--mention-color:${CHARACTERS[id].color}">@${esc(CHARACTERS[id].name)}</span>`;
  });
  return s.replace(/\n/g, '<br>');
}

function messageHtml(m, chatId) {
  if (m.note) return `<p class="note">${esc(m.text)}</p>`;
  if (m.mine) return `<div class="msg mine"><div class="msg-body"><div class="bubble">${formatText(m.text)}</div></div></div>`;
  const c = speaker(m.char);
  const quote = m.replyTo && m.replyTo.text
    ? `<div class="quote" style="--quote-color:${speaker(m.replyTo.character).color}">${esc(speaker(m.replyTo.character).name)}: ${esc(m.replyTo.text.slice(0, 90))}</div>`
    : '';
  // Photos live on the server; after a restart old ones may be gone, so hide broken ones.
  const photo = m.snapshot && state.live
    ? `<img src="/api/snapshot/${esc(m.snapshot)}" alt="Photo from the desk cam" data-photo="/api/snapshot/${esc(m.snapshot)}" loading="lazy" onerror="this.remove()">`
    : '';
  const cta = m.cta && m.cta.action === 'captures'
    ? `<button type="button" class="btn-quiet msg-cta" data-open="captures">${esc(m.cta.label || 'Desk captures')}</button>`
    : '';
  return `<div class="msg" style="--tint:${c.tint}">
    <img class="msg-art" src="${c.art}" alt="" width="36" height="36">
    <div class="msg-body">
      ${chatId === 'group' ? `<span class="msg-name" style="color:${c.color}">${esc(c.name)}</span>` : ''}
      ${quote}
      <div class="bubble">${formatText(m.text)}${photo}</div>
      ${cta}
    </div>
  </div>`;
}

function renderMessages() {
  const box = $('#messages');
  const list = state.messages[state.chat];
  const c = who(state.chat);
  const empty = list.some((m) => !m.note)
    ? ''
    : `<div class="empty"><img src="${c.art}" alt="" width="132" height="132"><p>${esc(c.empty)}</p></div>`;
  box.innerHTML = list.map((m) => messageHtml(m, state.chat)).join('') + empty;
  box.scrollTop = box.scrollHeight;
}

function addMessage(chatId, msg) {
  if (msg.id) {
    if (state.seen.has(msg.id)) return;     // already shown (came over /api/events too)
    state.seen.add(msg.id);
  }
  const list = state.messages[chatId];
  list.push({ time: Date.now(), ...msg });
  if (list.length > 200) list.splice(0, list.length - 200);
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
  if (!msg.mine && !msg.note && !visible && (msg.toast || msg.text.includes('@user'))) {
    toast(speaker(msg.char), msg.text.replace(/@user/g, '@you'));
  }
  renderChatList();
  if (!msg.note) saveHistory();
}

function showTyping(charId, chatId) {
  hideTyping();
  if (state.view !== 'chat' || state.chat !== chatId) return;   // only in the chat being answered
  const c = speaker(charId);
  const box = $('#messages');
  box.querySelector('.empty')?.remove();
  box.insertAdjacentHTML('beforeend', `<div class="msg" id="typing" style="--tint:${c.tint}">
    <img class="msg-art" src="${c.art}" alt="" width="36" height="36">
    <div class="bubble typing" aria-label="${esc(c.name)} is typing"><i></i><i></i><i></i></div></div>`);
  box.scrollTop = box.scrollHeight;
}
function hideTyping() { $('#typing')?.remove(); }

let toastTimer = null;
function toast(character, text) {
  const el = $('#toast');
  el.innerHTML = `${character ? `<img src="${character.art}" alt="">` : ''}<span>${esc(text.slice(0, 140))}</span>`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

// ————— Sending —————
function historyFor(chatId) {
  return state.messages[chatId].filter((m) => !m.note).slice(-11, -1).map((m) => ({
    role: m.mine ? 'user' : 'assistant',
    name: m.mine ? undefined : speaker(m.char).name,
    text: m.text.slice(0, 1200),
  }));
}

async function send(text) {
  const chatId = state.chat;
  addMessage(chatId, { mine: true, text });
  if (!state.live) return demoReply(chatId, text);

  // In the group the server decides who answers, so show the desk typing.
  showTyping(chatId, chatId);
  let res = null;
  try {
    res = await api('/api/chat', {
      method: 'POST',
      timeout: 60000,
      body: { chat: chatId, message: text, recent_messages: historyFor(chatId), widget: chatId === 'group' ? undefined : trackerPayload(chatId) },
    });
  } catch {
    hideTyping();
    addMessage(chatId, { note: true, text: 'Could not reach the server. Is python server.py still running?' });
    return;
  }
  hideTyping();
  // Group replies also arrive one by one over /api/events; the ids stop doubles.
  for (const r of res?.messages || []) {
    addMessage(chatId, { id: r.id, char: r.character || chatId, text: r.text, replyTo: r.reply_to, cta: r.cta });
  }
  if (res?.actions && chatId !== 'group') applyActions(chatId, res.actions);
  // Nobody answered: say why instead of staying silent.
  if (res?.error && !(res.messages || []).length) addMessage(chatId, { note: true, text: res.error });
}

/** The LLM can ask to change a tracker (add a to-do, log a sip…). */
function applyActions(id, actions) {
  const t = trackers[id];
  let changed = false;
  for (const a of actions) {
    const text = (a.text || '').toLowerCase();
    if (t.type === 'todo' && a.action === 'add_todo' && a.text && t.items.length < 20) {
      t.items.push({ text: a.text.slice(0, 200), done: false }); changed = true;
    } else if (t.type === 'todo' && a.action === 'done_todo' && text) {
      const item = t.items.find((i) => !i.done && i.text.toLowerCase().includes(text));
      if (item) { item.done = true; changed = true; }
    } else if (t.type === 'todo' && a.action === 'del_todo' && text) {
      const i = t.items.findIndex((x) => x.text.toLowerCase().includes(text));
      if (i >= 0) { t.items.splice(i, 1); changed = true; }
    } else if (t.type === 'break_timer' && a.action === 'log_break') {
      t.breaks_today += 1; t.last_break = new Date().toISOString(); changed = true;
    } else if (t.type === 'sip_counter' && a.action === 'log_sip') {
      t.sips += 1; t.last_sip = new Date().toISOString(); changed = true;
    } else if (t.type === 'snack_log' && a.action === 'log_snack' && a.label) {
      t.entries.push({ label: a.label.slice(0, 60), healthy: !!a.healthy, time: new Date().toISOString() }); changed = true;
    }
  }
  if (changed) trackerChanged(id);
}

// ————— @mentions —————
let mentionStart = -1;
let mentionIndex = 0;

function mentionMatches(query) {
  const q = query.toLowerCase();
  return CHAR_IDS.filter((id) => !q || CHARACTERS[id].name.toLowerCase().startsWith(q)
    || Object.entries(ALIASES).some(([word, cid]) => cid === id && word.startsWith(q)));
}

function updateMentions() {
  const input = $('#input');
  const before = input.value.slice(0, input.selectionStart);
  const at = before.lastIndexOf('@');
  const query = at >= 0 ? before.slice(at + 1) : '';
  const box = $('#mentions');
  if (at < 0 || (at > 0 && before[at - 1] !== ' ') || /\s/.test(query) || query.length > 12) {
    box.hidden = true; mentionStart = -1; return;
  }
  const ids = mentionMatches(query);
  if (!ids.length) { box.hidden = true; mentionStart = -1; return; }
  mentionStart = at;
  mentionIndex = Math.min(mentionIndex, ids.length - 1);
  box.innerHTML = ids.map((id, i) => {
    const onDesk = state.mentionOptions.find((o) => o.id === id)?.detected;
    return `<button type="button" class="mention-opt" role="option" data-id="${id}" aria-selected="${i === mentionIndex}">
      <img src="${CHARACTERS[id].art}" alt="">${esc(CHARACTERS[id].name)}${onDesk ? '<small>on the desk</small>' : ''}</button>`;
  }).join('');
  box.hidden = false;
}

function insertMention(id) {
  const input = $('#input');
  const after = input.value.slice(input.selectionStart);
  const word = `@${CHARACTERS[id].name.toLowerCase()} `;
  input.value = input.value.slice(0, mentionStart) + word + after;
  const pos = mentionStart + word.length;
  input.setSelectionRange(pos, pos);
  $('#mentions').hidden = true;
  mentionStart = -1;
  input.focus();
}

// ————— Trackers UI —————
function renderTracker() {
  const box = $('#tracker');
  const id = state.chat;
  const t = trackers[id];
  if (!t) { box.hidden = true; return; }
  box.hidden = false;
  box.style.setProperty('--tint', CHARACTERS[id].tint);

  if (t.type === 'todo') {
    box.innerHTML = `
      <ul class="todo">${t.items.map((item, i) => `<li>
        <label><input type="checkbox" data-i="${i}" ${item.done ? 'checked' : ''}><span class="${item.done ? 'done' : ''}">${esc(item.text)}</span></label>
        <button type="button" class="x" data-del="${i}" aria-label="Remove ${esc(item.text)}">×</button></li>`).join('')}</ul>
      <form class="inline-add"><input type="text" maxlength="200" placeholder="Add a task" aria-label="New task"><button type="submit" class="btn-quiet">Add</button></form>`;
    box.querySelectorAll('input[type="checkbox"]').forEach((cb) => cb.addEventListener('change', () => {
      t.items[+cb.dataset.i].done = cb.checked; trackerChanged(id);
    }));
    box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
      t.items.splice(+b.dataset.del, 1); trackerChanged(id);
    }));
    box.querySelector('form').addEventListener('submit', (e) => {
      e.preventDefault();
      const v = e.target.querySelector('input').value.trim();
      if (!v) return;
      t.items.push({ text: v.slice(0, 200), done: false });
      trackerChanged(id);
      $('#tracker input[type="text"]')?.focus();
    });
    return;
  }

  if (t.type === 'break_timer') {
    box.innerHTML = `<div class="tracker-row">
        <div class="tracker-stats">
          <div class="stat"><strong>${t.breaks_today}</strong><span>breaks today</span></div>
          <div class="stat"><strong id="slouch-min">–</strong><span>slouching (min)</span></div>
          <div class="stat"><strong id="nail-cues">–</strong><span>nail biting</span></div>
        </div>
        <button type="button" class="btn-quiet" id="log-break">Log a break</button>
      </div>
      <p class="tracker-note">${t.last_break ? `Last break ${timeAgo(t.last_break)}. ` : ''}Breaks log themselves when you leave the desk for 5 minutes. <a class="link" data-open="habits">Habit log</a></p>`;
    $('#log-break').addEventListener('click', () => {
      t.breaks_today += 1; t.last_break = new Date().toISOString(); trackerChanged(id);
      addMessage(id, { char: id, text: pick(['Break logged. Roll your shoulders while you are at it.', 'Thank you. Look at something far away for 20 seconds.', 'Logged. My fans are calming down already.']) });
    });
    if (state.live) {
      api('/api/habit_log?limit=1').then((d) => {
        const s = d?.summary || {};
        const sm = $('#slouch-min'); const nc = $('#nail-cues');
        if (sm) sm.textContent = Math.round((s.posture_bad_seconds_today || 0) / 60);
        if (nc) nc.textContent = s.nail_biting_count_today ?? 0;
      }).catch(() => {});
    }
    return;
  }

  if (t.type === 'sip_counter') {
    box.innerHTML = `<div class="tracker-row">
        <div class="stat"><strong>${t.sips} of ${t.goal}</strong><span>sips today</span></div>
        <div class="drops" aria-hidden="true">${Array.from({ length: t.goal }, (_, i) => `<span class="drop ${i < t.sips ? 'full' : ''}"></span>`).join('')}</div>
        <button type="button" class="btn-quiet" id="log-sip">Log a sip</button>
      </div>
      <p class="tracker-note">${t.last_sip ? `Last sip ${timeAgo(t.last_sip)}. ` : ''}A sip counts when the cup leaves the desk and comes back.</p>`;
    $('#log-sip').addEventListener('click', () => {
      t.sips += 1; t.last_sip = new Date().toISOString(); trackerChanged(id);
      addMessage(id, { char: id, text: t.sips >= t.goal ? 'That is your goal! I am so proud and also slightly empty.' : pick(['Sip logged!', 'Refreshing. For you, I mean.', 'One more down.']) });
    });
    return;
  }

  // snack log
  box.innerHTML = `<div class="pills">${t.entries.map((e, i) => `<span class="pill ${e.healthy ? 'healthy' : 'treat'}">${esc(e.label)}<button type="button" data-del="${i}" aria-label="Remove ${esc(e.label)}">×</button></span>`).join('') || '<span class="muted">Nothing logged yet today.</span>'}</div>
    <form class="inline-add"><input type="text" maxlength="60" placeholder="What did you eat?" aria-label="Snack name">
      <button type="submit" class="btn-quiet" data-healthy="1">Healthy</button><button type="button" class="btn-quiet" data-healthy="0">Treat</button></form>`;
  const add = (healthy) => {
    const input = box.querySelector('input');
    const label = input.value.trim().slice(0, 60);
    if (!label) { input.focus(); return; }
    t.entries.push({ label, healthy, time: new Date().toISOString() });
    trackerChanged(id);
    addMessage(id, { char: id, text: healthy ? pick(['Respectable.', 'Fine. Very nutritious. Very responsible.']) : pick(['Now we are talking.', 'Life is short. Napkin is optional.']) });
  };
  box.querySelector('form').addEventListener('submit', (e) => { e.preventDefault(); add(true); });
  box.querySelector('[data-healthy="0"]').addEventListener('click', () => add(false));
  box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => {
    t.entries.splice(+b.dataset.del, 1); trackerChanged(id);
  }));
}

/** After an auto-detected break/sip/snack, pull the server's counts into the tracker. */
async function refreshTrackerFromServer(id) {
  if (!state.live || !trackers[id]) return;
  try {
    const { widget } = await api(`/api/chat_widget/${id}`);
    const t = trackers[id];
    if (!widget || widget.type !== t.type) return;
    if (t.type === 'break_timer') { t.breaks_today = widget.breaks_today ?? t.breaks_today; t.last_break = widget.last_break ?? t.last_break; }
    if (t.type === 'sip_counter') { t.sips = widget.sips ?? t.sips; t.last_sip = widget.last_sip ?? t.last_sip; }
    if (t.type === 'snack_log' && widget.entries) t.entries = widget.entries;
    saveTrackers();
    if (state.chat === id && state.view === 'chat') renderTracker();
  } catch { /* keep local counts */ }
}

// ————— Habits page —————
async function renderHabits() {
  const page = $('#view-habits');
  const data = state.live ? await api(`/api/habit_log?category=${state.habitFilter}&limit=40`).catch(() => null) : null;
  const s = data?.summary || {};
  const events = data?.events || [];
  page.innerHTML = `
    <header><h1 class="page-title">Habits</h1>
      <p class="page-intro">What the camera noticed today, plus anything you want to count yourself.</p></header>
    <section><h2>Today</h2>
      <div class="today">
        <div class="stat"><strong>${s.breaks_today ?? trackers.monty.breaks_today}</strong><span>screen breaks</span></div>
        <div class="stat"><strong>${state.live ? Math.round((s.posture_bad_seconds_today || 0) / 60) : '–'}</strong><span>minutes slouching</span></div>
        <div class="stat"><strong>${state.live ? (s.nail_biting_count_today ?? 0) : '–'}</strong><span>nail biting</span></div>
        <div class="stat"><strong>${trackers.glug.sips}</strong><span>sips of water</span></div>
      </div></section>
    <section><h2>Your own counters</h2>
      <p class="hint">Tap + whenever it happens. They reset every morning.</p>
      <ul class="counter-list">${habits.items.map((h, i) => `<li><span>${esc(h.label)}</span>
        <button type="button" class="round" data-dec="${i}" aria-label="Minus one">−</button><strong>${h.count}</strong>
        <button type="button" class="round" data-inc="${i}" aria-label="Plus one">+</button>
        <button type="button" class="round" data-del="${i}" aria-label="Remove ${esc(h.label)}">×</button></li>`).join('')}</ul>
      <form class="inline-add" id="add-habit" style="max-width:520px"><input type="text" maxlength="60" placeholder="Something else to count" aria-label="New counter"><button type="submit" class="btn-quiet">Add</button></form></section>
    <section><h2>Camera log</h2>
      <div class="chips" role="group" aria-label="Filter">${[['all', 'Everything'], ['posture_bad', 'Posture'], ['nail_biting', 'Nail biting']]
        .map(([k, label]) => `<button type="button" class="chip" data-filter="${k}" aria-pressed="${state.habitFilter === k}">${label}</button>`).join('')}</div>
      ${events.length ? `<ul class="log">${events.map((ev) => `<li><time>${clock(ev.ts || 0)}</time>
          ${ev.snapshot_id ? `<img src="/api/snapshot/${esc(ev.snapshot_id)}" alt="" data-photo="/api/snapshot/${esc(ev.snapshot_id)}">` : ''}
          <p>${esc(ev.title || '')}${ev.detail ? `<small>${esc(ev.detail)}</small>` : ''}</p></li>`).join('')}</ul>`
        : `<p class="muted">${state.live ? 'Nothing yet. Posture and nail-biting entries appear here when Ollama vision is set up in .env.' : 'Run the server to see what the camera notices.'}</p>`}
    </section>`;

  page.querySelectorAll('[data-inc],[data-dec],[data-del]').forEach((b) => b.addEventListener('click', () => {
    const i = +(b.dataset.inc ?? b.dataset.dec ?? b.dataset.del);
    if (b.dataset.inc) habits.items[i].count += 1;
    if (b.dataset.dec) habits.items[i].count = Math.max(0, habits.items[i].count - 1);
    if (b.dataset.del) habits.items.splice(i, 1);
    saveHabits(); renderHabits();
  }));
  $('#add-habit').addEventListener('submit', (e) => {
    e.preventDefault();
    const v = e.target.querySelector('input').value.trim();
    if (!v) return;
    habits.items.push({ label: v.slice(0, 60), count: 0 });
    saveHabits(); renderHabits();
  });
  page.querySelectorAll('[data-filter]').forEach((b) => b.addEventListener('click', () => {
    state.habitFilter = b.dataset.filter; renderHabits();
  }));
}

// ————— Captures page —————
async function renderCaptures() {
  const page = $('#view-captures');
  const head = `<header><h1 class="page-title">Captures</h1>
    <p class="page-intro">Photos the desk cam saved: proof for alerts, and snapshots when easy-to-lose things show up.</p></header>`;
  if (!state.live) {
    page.innerHTML = `${head}<p class="muted">Run the server to collect desk photos.</p>`;
    return;
  }
  const data = await api('/api/snapshots').catch(() => null);
  const items = data?.items || [];
  page.innerHTML = head + (items.length
    ? `<div class="gallery">${items.map((it) => `<button type="button" data-photo="/api/snapshot/${esc(it.id)}">
        <img src="/api/snapshot/${esc(it.id)}" alt="" loading="lazy">
        <strong>${esc(it.label || 'Capture')}</strong><span>${new Date((it.ts || 0) * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</span></button>`).join('')}</div>`
    : '<p class="muted">No photos yet. They appear when something gets too close, or when your phone, keys, or wallet show up.</p>');
}

// ————— Settings page —————
async function renderSettings() {
  const page = $('#view-settings');
  const head = `<header><h1 class="page-title">Settings</h1>
    <p class="page-intro">How close is too close, and when the desk should stay quiet.</p></header>`;
  if (!state.live) {
    page.innerHTML = `${head}<p class="muted">Settings are saved by the server. Run python server.py to change them.</p>`;
    return;
  }
  let rules; let policy;
  try {
    [rules, policy] = await Promise.all([api('/api/rules'), api('/api/habit_policy')]);
  } catch {
    page.innerHTML = `${head}<p class="muted">Could not load settings from the server.</p>`;
    return;
  }
  const zones = { ...(rules.danger_zones || {}) };
  const edge = { danger_px: 35, warn_px: 75, ...(rules.edge_proximity || {}) };
  let outline = (rules.desk_outline || []).map(([x, y]) => [x, y]);
  let outlineChanged = false;
  let startOver = outline.length >= 3;   // a saved outline: the first tap starts a new one
  let picSize = null;     // natural size of the camera picture, once it loads
  const range = (name, value, min, max) => `<label class="range"><input type="range" name="${name}" min="${min}" max="${max}" value="${value}"><output>${value} px</output></label>`;

  const draw = () => {
    page.innerHTML = `${head}
    <section><h2>Danger zones</h2>
      <p class="hint">Each object gets an invisible bubble around it. When something risky enters the bubble (a cup near the laptop), the desk speaks up.</p>
      <div class="form-rows">${Object.entries(zones).map(([cls, z]) => `<div class="zone" data-zone="${esc(cls)}">
          <strong>${esc(cls)}</strong>
          <input type="text" name="label" value="${esc(z.label || '')}" aria-label="Name for the ${esc(cls)} zone">
          ${range('expand', z.expand || 60, 10, 200)}
          <button type="button" class="round" data-remove aria-label="Remove ${esc(cls)} zone">×</button></div>`).join('') || '<p class="muted">No zones yet.</p>'}
        <form class="inline-add" id="add-zone"><select aria-label="Object">${(rules.available_objects || []).filter((o) => !zones[o]).map((o) => `<option>${esc(o)}</option>`).join('')}</select>
          <button type="submit" class="btn-quiet">Add zone</button></form>
      </div></section>
    <section><h2>Desk edges</h2>
      <p class="hint">Tap the corners of your desk top on the picture, going around the desk. If the desk runs past the side of the picture, tap where it meets the side. Lines along the side of the picture are ignored, because the desk keeps going there. Until you do this, nobody warns about falling off.</p>
      <div class="edge-editor">
        ${state.camOk
          ? `<div class="edge-pic" id="edge-pic"><img id="edge-img" src="/api/latest_frame_raw/${state.camIndex}?t=${Date.now()}" alt="What the desk cam sees"><svg id="edge-svg" aria-hidden="true"></svg></div>`
          : `<p class="muted edge-off">Turn on the phone camera to mark the desk. ${outline.length ? `(${outline.length} corners are saved.)` : ''}</p>`}
        <div class="edge-tools">
          <button type="button" class="btn-quiet" id="edge-undo">Undo</button>
          <button type="button" class="btn-quiet" id="edge-clear">Clear</button>
          ${state.camOk ? '<button type="button" class="btn-quiet" id="edge-photo">New picture</button>' : ''}
          <span class="muted" id="edge-status" role="status"></span>
        </div>
      </div>
      <div class="form-rows">
        <div class="field"><span>Warn at</span>${range('warn_px', edge.warn_px, 20, 200)}</div>
        <div class="field"><span>Alert at</span>${range('danger_px', edge.danger_px, 10, 120)}</div>
      </div></section>
    <section><h2>Quiet hours</h2>
      <p class="hint">Habit reminders wait until quiet hours end. Safety alerts still come through.</p>
      <div class="form-rows">
        <label class="check"><input type="checkbox" id="dnd-on" ${policy.dnd_enabled ? 'checked' : ''}> Use quiet hours</label>
        <div class="field"><span>From</span><input type="number" id="dnd-start" min="0" max="23" value="${policy.dnd_start_hour ?? 22}"><span>to</span><input type="number" id="dnd-end" min="0" max="23" value="${policy.dnd_end_hour ?? 7}"><span class="muted">(24-hour clock)</span></div>
      </div></section>
    <div class="save-row"><button type="button" class="btn" id="save-settings">Save settings</button><span class="saved" id="saved" role="status"></span></div>
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
      if (cls) { zones[cls] = { expand: 60, label: `${cls} zone` }; draw(); }
    });
    wireEdgeEditor();
    $('#clear-chats').addEventListener('click', () => {
      for (const id of CHAT_IDS) { state.messages[id] = []; state.unread[id] = 0; }
      storage.save(HISTORY_KEY, null);
      renderChatList();
      toast(null, 'Chats cleared');
    });
    $('#save-settings').addEventListener('click', async () => {
      const out = $('#saved');
      out.className = 'saved'; out.textContent = 'Saving…';
      if (outline.length && outline.length < 3) {
        out.className = 'saved error'; out.textContent = 'Tap at least 3 corners of the desk, or press Clear.';
        return;
      }
      try {
        const body = { danger_zones: zones, edge_proximity: { ...edge } };
        if (outlineChanged) body.desk_outline = outline;   // only when you changed it (it also resets Lens's "bumped" check)
        await api('/api/rules', { method: 'PUT', body });
        outlineChanged = false;
        await api('/api/habit_policy', { method: 'PUT', body: {
          dnd_enabled: $('#dnd-on').checked,
          dnd_start_hour: +$('#dnd-start').value || 0,
          dnd_end_hour: +$('#dnd-end').value || 0,
        } });
        out.textContent = 'Saved';
      } catch {
        out.className = 'saved error'; out.textContent = 'Not saved. Check that the server is running.';
      }
    });
  };

  // ——— Desk edge editor: tap corners on the camera picture ———
  const SNAP = 0.03;   // taps this close to the picture border snap onto it
  const sameBorder = (a, b) => (a[0] <= SNAP && b[0] <= SNAP) || (a[0] >= 1 - SNAP && b[0] >= 1 - SNAP)
    || (a[1] <= SNAP && b[1] <= SNAP) || (a[1] >= 1 - SNAP && b[1] >= 1 - SNAP);

  function drawOutline() {
    const svg = $('#edge-svg');
    const status = $('#edge-status');
    const real = outline.length >= 3
      ? outline.filter((p, i) => !sameBorder(p, outline[(i + 1) % outline.length])).length : 0;
    if (status) {
      status.textContent = outline.length < 3
        ? `${outline.length} of at least 3 corners`
        : `${outline.length} corners, ${real} real desk edge${real === 1 ? '' : 's'}${outlineChanged ? '. Press Save settings.' : '. Tap the picture to start over.'}`;
    }
    if (!svg || !picSize) return;
    const { w, h } = picSize;
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const P = (p) => `${(p[0] * w).toFixed(1)},${(p[1] * h).toFixed(1)}`;
    const sw = Math.max(2, w / 220);
    let out = '';
    if (outline.length >= 3) out += `<polygon points="${outline.map(P).join(' ')}" class="edge-fill"/>`;
    outline.forEach((p, i) => {
      if (i === outline.length - 1 && outline.length < 3) return;
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
        if (outline.length >= 12) return;
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
  stage.classList.toggle('is-pairing', state.live && state.phoneMode && !state.camOk);
  stage.classList.toggle('has-video', state.live && state.camOk);
  if (!state.live) {
    stage.innerHTML = '<img class="scene" src="art/desk-scene.svg" alt="Illustration of the desk characters">';
    stage.insertAdjacentHTML('afterend', '<p class="cam-caption" id="cam-caption">This is a demo. Run the server and connect your phone to see your real desk.</p>');
    return;
  }
  if (state.camOk) {
    // One MJPEG stream; the server only sends a frame when a new one is ready.
    stage.innerHTML = `<img src="${feedUrl()}" alt="Live view of your desk">`;
    return;
  }
  if (!state.phoneMode) {
    stage.innerHTML = '<p class="cam-caption">No webcam found. Check the terminal running server.py for tips.</p>';
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
  if (!state.live) {
    list.innerHTML = CHAR_IDS.map((id) => `<li style="--tint:${CHARACTERS[id].tint}"><img src="${CHARACTERS[id].art}" alt="">${CHARACTERS[id].name}</li>`).join('');
    return;
  }
  const objs = Object.entries(det?.objects || {});
  const extras = (det?.extras || []).filter((e) => !objs.some(([, o]) => o.class === e.yolo_class));
  if (!objs.length && !extras.length) {
    list.innerHTML = `<li class="extra">${state.camOk ? 'Nothing recognized yet' : 'Waiting for the camera'}</li>`;
    return;
  }
  list.innerHTML = objs.map(([id, o]) => {
    const c = CHARACTERS[id];
    return c ? `<li style="--tint:${c.tint}" title="Detected as ${esc(o.class)}"><img src="${c.art}" alt="">${c.name} <small>${Math.round((o.confidence || 0) * 100)}%</small></li>` : '';
  }).join('') + extras.map((e) => `<li class="extra">${esc(e.label || e.yolo_class)}</li>`).join('');
}

function renderRisks(safety, cameraIssues) {
  const list = $('#risks');
  if (!state.live) {
    list.innerHTML = '<li>Cup is a little close to Computer.</li>';
    return;
  }
  // Camera problems first (from Lens), then things on the desk.
  const lens = (cameraIssues || []).slice(0, 2).map((i) => `<li class="lens-issue">
      <img src="${LENS.art}" alt="Lens"><span><strong>${esc(i.title)}.</strong> ${esc(i.fix)}</span></li>`);
  const lines = (safety?.explanations || []).slice(0, 6).map((l) => `<li>${esc(l)}</li>`);
  const html = [...lens, ...lines].join('')
    || `<li class="calm">${state.camOk ? 'All calm on the desk.' : 'Nothing to report yet.'}</li>`;
  if (list.innerHTML !== html) list.innerHTML = html;
}

async function renderProofs() {
  const box = $('#proofs');
  if (!state.live) { box.innerHTML = '<p class="muted" style="grid-column:1/-1">Proof photos from alerts show up here.</p>'; return; }
  const data = await api('/api/snapshots').catch(() => null);
  const items = (data?.items || []).slice(0, 6);
  const ids = items.map((i) => i.id).join(',');
  if (ids === state.snapshotIds) return;    // nothing new → don't reload images
  state.snapshotIds = ids;
  box.innerHTML = items.length
    ? items.map((it) => `<img src="/api/snapshot/${esc(it.id)}" alt="${esc(it.label || 'Desk photo')}" title="${esc(it.label || '')}" data-photo="/api/snapshot/${esc(it.id)}" loading="lazy">`).join('')
    : '<p class="muted" style="grid-column:1/-1">None yet. Photos are saved when something gets too close.</p>';
}

/** One request every 1.5 s: camera status, detections, and risks together. */
async function pollLive() {
  let d;
  try {
    d = await api(`/api/live${state.phone?.qr_svg ? '' : '?qr=1'}`, { timeout: 5000 });
  } catch {
    return;   // a missed poll is fine; the next one will catch up
  }
  const wasOk = state.camOk;
  state.phoneMode = d.phone_mode;
  state.camOk = d.camera;
  state.camIndex = d.camera_index ?? 0;
  if (d.phone && (d.phone.url || d.phone.qr_svg)) state.phone = { ...state.phone, ...d.phone };
  if (state.camOk !== wasOk) {
    renderCamStage();
    renderConn();
    if (state.phoneMode) toast(null, state.camOk ? 'Phone camera connected' : 'Phone camera disconnected');
  } else if (!state.camOk && state.phone && !$('#cam-stage .pair-qr') && state.phone.qr_svg) {
    renderCamStage();   // pairing link just arrived
  }
  renderOnDesk(d.detections);
  renderRisks(d.safety, d.camera_issues);
  renderAi(d.ai);
}

/** A line above the message box when the AI can't answer (no key, quota used up…). */
function renderAi(ai) {
  if (!ai) return;
  let text = '';
  if (!ai.ready) text = 'The characters need an AI to chat. Add a key to .env (GEMINI_API_KEY is free) and restart the server.';
  else if (ai.problem) text = ai.problem;
  if (text === state.aiText) return;
  state.aiText = text;
  const el = $('#ai-banner');
  el.textContent = text;
  el.hidden = !text;
}

// ————— Live connection —————
function listenForEvents() {
  const es = new EventSource('/api/events');
  es.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }
    if (!d.text) return;
    if (d.type === 'group_message' && d.character) {
      addMessage('group', {
        id: d.id, char: d.character, text: d.text, snapshot: d.snapshot, replyTo: d.reply_to,
        toast: d.character === 'lens' && d.lens === 'problem',   // camera problems pop up anywhere
      });
      if (d.snapshot) renderProofs();
    } else if (d.type === 'habit_reminder' && CHARACTERS[d.character]) {
      addMessage(d.character, { char: d.character, text: d.text, toast: true });
      refreshTrackerFromServer(d.character);
    } else if (d.type === 'scenario_alert') {
      const id = CHARACTERS[d.character] ? d.character : 'group';
      addMessage(id, { char: id === 'group' ? 'sheets' : id, text: d.text });
    }
  };
}

async function startLive(status) {
  state.live = true;
  loadHistory();
  state.phoneMode = !!status.phone_mode;
  state.camOk = !!status.camera;
  state.camIndex = status.camera_indices?.[0] ?? 0;
  // Let the characters know what's on your trackers (to-dos, sips…).
  const byChat = Object.fromEntries(CHAR_IDS.map((id) => [id, trackerPayload(id)]));
  api('/api/chat_todos_sync', { method: 'POST', body: { by_chat: byChat } }).catch(() => {});
  listenForEvents();
  renderConn();
  renderCamStage();
  await pollLive();
  setInterval(pollLive, 1500);
  renderProofs();
  setInterval(renderProofs, 10000);
  setInterval(() => {
    api('/api/context').then((c) => { state.mentionOptions = c.mention_options || []; }).catch(() => {});
  }, 10000);
  renderAi({ ready: !!status.llm });
}

// ————— Demo (no server, e.g. GitHub Pages) —————
let demoPlayed = false;
const DEMO_SCRIPT = [
  ['glug', 'Just sitting here. Minding my own business.'],
  ['monty', 'You are about four centimeters from my keyboard. I can feel the condensation.'],
  ['munch', 'Relax, Computer. Crumbs are basically confetti.'],
  ['sheets', 'Could everyone please stay away from the homework? It is due tomorrow.'],
  ['monty', '@user could you move the cup? Asking for a friend. The friend is me.'],
];
async function playDemoOnce() {
  if (demoPlayed || state.live) return;
  demoPlayed = true;
  addMessage('group', { note: true, text: 'Demo conversation. With the server running, this chat reacts to your real desk.' });
  for (const [id, text] of DEMO_SCRIPT) {
    await sleep(900);
    showTyping(id, 'group');
    await sleep(900 + text.length * 12);
    addMessage('group', { char: id, text });
  }
}
const DEMO_REPLIES = {
  monty: ['I would answer properly, but I am a demo right now. Run the server and I will actually see your desk.'],
  glug: ['In the real app I count your sips. Right now I am just a very calm drawing.'],
  munch: ['Demo snack here. Start the server and I will judge your real food.'],
  sheets: ['I am only a demo for now. The to-do list above still works, though.'],
};
async function demoReply(chatId, text) {
  const id = chatId === 'group'
    ? (CHAR_IDS.find((c) => text.toLowerCase().includes(`@${CHARACTERS[c].name.toLowerCase()}`)) || pick(CHAR_IDS))
    : chatId;
  showTyping(id, chatId);
  await sleep(1100);
  addMessage(chatId, { char: id, text: pick(DEMO_REPLIES[id]) });
}

// ————— Wiring —————
function wire() {
  $('#chat-list').addEventListener('click', (e) => {
    const b = e.target.closest('[data-chat]');
    if (b) openChat(b.dataset.chat);
  });
  $$('.page-link').forEach((b) => b.addEventListener('click', () => openPage(b.dataset.view)));

  // Links inside messages/trackers that open a page, and photos that open full size.
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

  // Composer
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
    if (state.live && Date.now() - lastPing > 3000) {      // hold off idle chatter while you type
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

  // Desk cam panel
  $$('.seg button').forEach((b) => b.addEventListener('click', () => {
    state.feed = b.dataset.feed;
    $$('.seg button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    if (state.live && state.camOk) renderCamStage();
  }));
  $('#cam-toggle').addEventListener('click', () => $('#cam').classList.add('open'));
  $('#cam-close').addEventListener('click', () => $('#cam').classList.remove('open'));
  $('#conn').addEventListener('click', () => {
    if (!state.live) toast(null, 'Demo mode. Run python server.py and open http://localhost:5000 to use your real desk.');
    else if (state.phoneMode && !state.camOk) $('#cam').classList.add('open');
  });
}

async function init() {
  wire();
  renderConn();
  let status = null;
  if (location.protocol.startsWith('http')) {
    status = await api('/api/status', { timeout: 4000 }).catch(() => null);
  }
  if (status) {
    await startLive(status);
  } else {
    state.live = false;
    $('#about-link').hidden = false;   // the landing page sits next to the demo on GitHub Pages
    renderConn();
    renderCamStage();
    renderOnDesk(null);
    renderRisks(null);
    renderProofs();
  }
  openChat('group');
}

document.addEventListener('DOMContentLoaded', init);
