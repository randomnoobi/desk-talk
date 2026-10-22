#!/usr/bin/env python3
"""
Claude Code hook: warn when the chat has grown long.

Past roughly 200k-250k tokens the model starts to forget things and make more
mistakes. This reads how big the last request was from the session transcript
and shows a note in Claude Code when it passes the limits below.

Runs on UserPromptSubmit and Stop (see .claude/settings.json). It never blocks
anything; it only prints a note.

Change the limits with environment variables if you like:
    DESKTALK_CONTEXT_WARN_TOKENS   (default 200000)
    DESKTALK_CONTEXT_STOP_TOKENS   (default 250000)
"""

from __future__ import annotations

import json
import os
import sys

WARN_AT = int(os.environ.get('DESKTALK_CONTEXT_WARN_TOKENS', 200_000))
STOP_AT = int(os.environ.get('DESKTALK_CONTEXT_STOP_TOKENS', 250_000))


def context_tokens(transcript_path: str) -> int:
    """Size of the last main-chat request: everything sent in, plus what came back."""
    last = 0
    with open(transcript_path, encoding='utf-8') as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get('message')
            if entry.get('type') != 'assistant' or entry.get('isSidechain') or not isinstance(msg, dict):
                continue
            usage = msg.get('usage') or {}
            total = sum(int(usage.get(k) or 0) for k in (
                'input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens'))
            if total:
                last = total
    return last


def main() -> None:
    try:
        data = json.load(sys.stdin)
        tokens = context_tokens(data['transcript_path'])
    except Exception:
        return  # a reminder is never worth breaking the session over
    k = round(tokens / 1000)
    if tokens >= STOP_AT:
        note = (f'⚠️ Context is about {k}k tokens. Past ~250k the model gets noticeably worse. '
                'Finish the current step, then run /compact or start a new session.')
    elif tokens >= WARN_AT:
        note = (f'Context is about {k}k tokens. Quality starts to drop around 200k-250k; '
                'plan a /compact or a new session soon.')
    else:
        return
    print(json.dumps({'systemMessage': note}))


if __name__ == '__main__':
    main()
