# Notifications

Warnings, habit reminders and the sips the camera sees come out of the Pi's speaker, and can also come to your phone.
Both wait during your quiet hours (the app's Settings page), and each kind can be switched off in the
`notifications:` section of `config/settings.yaml`.

## The speaker

You can plug a USB speaker into the Pi at any time, without a restart. With none plugged in, the Pi says so once in its
log and carries on. A Mac running the app for development shows them as its own pop-ups instead. If the Pi picks the
wrong sound card, see [Listen to the speaker](RUNBOOK.md#listen-to-the-speaker).

## Phone notifications

The same notifications can come to your phone as push notifications, through [ntfy](https://ntfy.sh). They're off
until you pick a topic.

Keep the topic secret. Anyone who knows it can read your notifications and send you some, so make it long and random,
never a word like `desktalk`.

1. **On the Pi**, make a topic:

   ```bash
   python3 -c "import secrets; print('desktalk-' + secrets.token_urlsafe(18))"
   ```

2. **On the Pi**, put it in `raspberry_pi/.env` as `NTFY_TOPIC=desktalk-...`.
3. On your phone, install the ntfy app and subscribe to that topic (on `ntfy.sh`, or on your own server, set in
   `notifications.phone_server`).
4. **On the Pi**, restart so the app reads the topic:

   ```bash
   cd ~/desk-talk/raspberry_pi && docker compose up -d
   ```

What goes to the phone:

- Only ready-made words, never an AI's (an AI may have seen your to-do list). An AI-written warning comes as its plain
  fixed line, and a lost-item alert, whose words an AI may write, doesn't come at all.
- Never a to-do's text. A to-do reminder comes as "A to-do is due. Open Desk Talk to see it."

With no internet the Pi carries on. Each notification is tried once, within one time limit. The first failure is
logged, and for a minute after a failure (`notifications.phone_retry_after_sec`) the next ones are dropped without
trying. The chat and the speaker never wait for it. `notifications.phone_server` holds only the server's address; the
topic goes in `NTFY_TOPIC`, never in the address.
