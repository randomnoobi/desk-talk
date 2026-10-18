"""
The AI connection (chat + vision), through the OpenAI-compatible API.

Which AI answers is decided by .env: the first of Gemini → OpenAI → Groq →
Ollama that has a key (Ollama needs only OLLAMA_URL). Model names and limits
are in config/settings.yaml → llm.

Background features (idle chatter, reminders, alerts) pause after a rate
limit, so the free quota is saved for replying to you.
"""

from __future__ import annotations

import base64
import threading
import time

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:          # the app still runs; characters just can't chat
    OpenAI = None
    HAS_OPENAI = False

_TRAILING_WORDS = ('the', 'a', 'an', 'is', 'are', 'to', 'and', 'but', 'or', 'in', 'of', 'that', 'this')


def is_rate_limit(err) -> bool:
    e = str(err).lower()
    return '429' in e or 'quota' in e or 'rate limit' in e or 'resource_exhausted' in e or 'exhausted' in e


def friendly_ai_error(err, label: str = '') -> str:
    """Turn an AI error into one plain sentence the user can act on."""
    e = str(err).lower()
    who = (label or 'the AI').split(' ')[0]
    if is_rate_limit(e):
        return (f'{who} hit its free usage limit. Free plans allow only a few messages per minute '
                f'and a set number per day. Wait a minute and try again; if it keeps happening, '
                f'the daily limit resets tomorrow.')
    if any(s in e for s in ('401', '403', 'api key', 'api_key', 'unauthorized', 'permission')):
        return f'{who} rejected the key in .env. Copy the key again, paste it into .env, and restart the server.'
    if '404' in e or 'not found' in e or 'does not exist' in e:
        return (f'{who} could not find the model named in the settings. Put a current model name in '
                f'config/settings.yaml (llm:) and restart the server.')
    if any(s in e for s in ('connect', 'timed out', 'timeout', 'unreachable', 'name or service')):
        if 'ollama' in (label or '').lower():
            return 'Could not reach Ollama. Open the Ollama app, then try again.'
        return 'Could not reach the AI service. Check the internet connection and try again.'
    return f'The AI returned an error: {str(err)[:160]}'


def looks_cut_off(text: str) -> bool:
    """Too short, or ends mid-sentence ("...move it to the")."""
    return len(text.split()) < 4 or text.rstrip().endswith(_TRAILING_WORDS)


class LLMClient:
    def __init__(self, llm_settings, secrets, client_factory=None):
        s = llm_settings
        self.min_interval = float(s.get('min_interval_sec', 6))
        self.max_retries = int(s.get('max_retries', 3))
        self.pause_on_limit = float(s.get('pause_on_limit_sec', 600))
        self.vision_model = s.get('vision_model', 'llava')
        factory = client_factory or (OpenAI if HAS_OPENAI else None)
        self.client = None
        self.vision_client = None
        self.model_name = None
        self.label = 'No AI (add a key to .env)'
        self.is_gemini = False
        self.is_ollama = False
        self._vision_unsupported = False
        self._last_call = 0.0
        self._lock = threading.Lock()
        self.problem_text: str | None = None
        self.problem_ts = 0.0
        self.background_paused_until = 0.0
        if factory is None:
            return
        ollama_url = secrets.get('ollama_url', '')
        if ollama_url:
            try:
                # Separate client for local vision so chat can run on Gemini while vision uses Ollama.
                self.vision_client = factory(api_key='ollama', base_url=ollama_url)
            except Exception:
                self.vision_client = None
        if secrets.get('gemini_api_key'):
            self.client = factory(api_key=secrets['gemini_api_key'],
                                  base_url='https://generativelanguage.googleapis.com/v1beta/openai/')
            self.model_name = s.get('gemini_model')
            self.label = f'Gemini {self.model_name}'
            self.is_gemini = True
        elif secrets.get('openai_api_key'):
            self.client = factory(api_key=secrets['openai_api_key'])
            self.model_name = s.get('openai_model')
            self.label = f'OpenAI {self.model_name}'
        elif secrets.get('groq_api_key'):
            self.client = factory(api_key=secrets['groq_api_key'], base_url='https://api.groq.com/openai/v1')
            self.model_name = s.get('groq_model')
            self.label = f'Groq {self.model_name}'
        elif ollama_url:
            self.client = factory(api_key='ollama', base_url=ollama_url)
            self.model_name = s.get('ollama_model')
            self.label = f'Ollama {self.model_name}'
            self.is_ollama = True

    @property
    def ready(self) -> bool:
        return self.client is not None

    def start_ollama_keepalive(self):
        """Ping Ollama every 4 minutes to keep the model loaded in memory."""
        if not self.is_ollama:
            return

        def _ping():
            time.sleep(10)
            while True:
                try:
                    self.client.chat.completions.create(model=self.model_name,
                                                        messages=[{'role': 'user', 'content': 'hi'}], max_tokens=1)
                except Exception:
                    pass
                time.sleep(240)
        threading.Thread(target=_ping, daemon=True, name='OllamaKeepAlive').start()

    # ── health ──
    def _failed(self, exc):
        err = str(exc)
        self.problem_text = friendly_ai_error(err, self.label)
        self.problem_ts = time.time()
        if is_rate_limit(err):
            self.background_paused_until = time.time() + self.pause_on_limit
            print(f'  [llm] Rate limit hit: background AI paused for {int(self.pause_on_limit // 60)} min')

    def problem(self) -> str | None:
        """The current AI problem in plain words, or None if the last call worked."""
        return self.problem_text

    def background_ok(self) -> bool:
        return bool(self.client) and time.time() >= self.background_paused_until

    def _wait_turn(self):
        with self._lock:
            elapsed = time.time() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.time()

    # ── calls ──
    def chat(self, messages, max_tokens=120, temperature=0.85, background=False):
        """Text reply, or None (no AI, AI resting after a limit, or an error)."""
        if not self.client:
            return None
        if background and not self.background_ok():
            return None
        self._wait_turn()
        kwargs = {'model': self.model_name, 'messages': messages, 'max_tokens': max_tokens,
                  'temperature': temperature}
        if self.is_ollama:
            kwargs['timeout'] = 90
            kwargs.setdefault('extra_body', {})['num_predict'] = max_tokens
        if self.is_gemini:
            kwargs['extra_body'] = {'extra_body': {'google': {'thinking_config': {'thinking_budget': 0}}}}
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                text = (resp.choices[0].message.content or '').strip().strip('"\'')
                if not text and self.is_ollama and attempt < self.max_retries - 1:
                    print(f'  [llm] Ollama empty response, retry {attempt + 1}...')
                    time.sleep(2.0)
                    kwargs['temperature'] = min(temperature + 0.15 * (attempt + 1), 1.0)
                    if kwargs['messages'] and kwargs['messages'][-1]['role'] == 'user':
                        kwargs['messages'][-1]['content'] += '\n(Please respond with at least one sentence.)'
                    continue
                self.problem_text = None     # a call worked, so any earlier problem is over
                return text
            except Exception as exc:
                err = str(exc)
                # One short retry for a per-minute limit when you're waiting on a reply.
                # Background calls don't retry: retries just use up more of the quota.
                if is_rate_limit(err) and not background and attempt == 0:
                    print('  [llm] Rate limited, retrying once in 5s...')
                    time.sleep(5)
                    continue
                if self.is_ollama and not is_rate_limit(err) and attempt < self.max_retries - 1:
                    print(f'  [llm] Ollama error, retry {attempt + 1}: {exc}')
                    time.sleep(2.0)
                    continue
                print(f'  [llm] Error (attempt {attempt + 1}): {str(exc)[:200]}')
                self._failed(exc)
                return None
        return None

    def vision(self, jpeg_bytes: bytes, user_text: str, model: str | None = None):
        """Ollama / OpenAI-compatible vision call (lost items, posture). None if unavailable."""
        client = self.vision_client or self.client
        if not client or not jpeg_bytes or self._vision_unsupported:
            return None
        if not self.vision_client and not self.background_ok():
            return None   # vision on a cloud AI shares its quota, so it pauses with the rest
        # A vision model name like "llava" only exists on Ollama; on a cloud AI use its own model.
        m = (model or self.vision_model) if self.vision_client else self.model_name
        b64 = base64.b64encode(jpeg_bytes).decode('ascii')
        for attempt in range(self.max_retries):
            try:
                self._wait_turn()
                resp = client.chat.completions.create(
                    model=m,
                    messages=[{'role': 'user', 'content': [
                        {'type': 'text', 'text': user_text},
                        {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}},
                    ]}],
                    max_tokens=400, temperature=0.2,
                )
                return (resp.choices[0].message.content or '').strip()
            except Exception as exc:
                err = str(exc).lower()
                if is_rate_limit(err):
                    self._failed(exc)     # out of quota: don't burn more by retrying
                    return None
                # A text-only model can't accept images — retrying won't help.
                if 'multimodal' in err or 'does not support' in err or 'not support multimodal' in err:
                    self._vision_unsupported = True
                    print(f'  [vision] DISABLED: model "{m}" cannot accept images. Set llm.vision_model '
                          f'in config/settings.yaml to a vision model (e.g. `ollama pull llava`).')
                    return None
                print(f'  [vision] attempt {attempt + 1}: {str(exc)[:160]}')
                if attempt < self.max_retries - 1:
                    time.sleep(1.0 * (attempt + 1))
        return None
