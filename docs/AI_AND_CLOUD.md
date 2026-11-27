# Chat AI and the cloud server

Both are optional. The Pi works by itself with neither.

## Chat AI

Warnings, habit reminders, the characters joining and leaving, the event log and the habits all work with no AI, in
their ready-made words. An AI adds chat with the characters, and nicer wording. No AI runs on the Pi, and AI keys
never go on it: the AI runs on your own computer or behind your cloud server.

To add one, **on the Pi**, set `llm.ai_url` in `raspberry_pi/mine/settings.mine.yaml` (your own settings file, which
`install.sh` made; it isn't in git), then restart the app:

```bash
cd ~/desk-talk/raspberry_pi && docker compose restart app
```

Pick one:

- For Ollama on your own computer, set `ai_url: http://<your computer>:11434/v1`, `ai_on_my_computer: true`, and
  `ollama_model` for the model (`ollama pull` it first). Because `ai_on_my_computer` says it's your own computer, it
  gets your chat messages and also the words the characters say by themselves (reminders, small talk, the recap),
  which can include your to-dos. Without `ai_on_my_computer: true` it gets only chat you start yourself.
- For your cloud server, set `ai_url: <its address>/v1` once the Pi is linked to it. Only chat you start yourself goes
  there, and the server doesn't store or log it. Reminders, small talk and the recap don't use it.

Only text ever goes to an AI, never pictures (`llm.send_pictures` is `false` on the Pi). If the computer is asleep,
warnings still come at once in their ready-made words, and a reminder waits at most `llm.reminder_wait_sec` for the AI
before using its own.

## The cloud server

You don't need an account. The cloud server is a program you run yourself, in Docker, that keeps your habit events
(never pictures, never to-do text) and writes a weekly report you read on its website. Until the Pi is linked to one,
nothing is sent.

- To set one up, see [cloud/README.md](../cloud/README.md).
- To link the Pi, run the install command with the code from the server's website (`--server`, see
  [the Pi guide](../raspberry_pi/README.md#install-with-one-command)). On a computer, `scripts/link_cloud.py` does it.
