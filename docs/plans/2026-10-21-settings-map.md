# Where the old code's numbers live now

The typed settings (`desktalk/config.py`, `load_config()` → `AppConfig`) load `config/settings.yaml`,
`config/protocols.yaml` and `config/agents.yaml`. Every number the old modules wrote into code now has a
setting. New code reads the setting, never the number. Each part gets its typed section passed in, e.g.
`Roster(clock, bus, profiles, config.agents)`.

## By old file

| Old file:line | Setting |
|---|---|
| `vision/producer.py` 60, 69, 78, 143, 31, 92 | `camera.wait_for_picture_sec`, `pause_after_error_sec`, `jpeg_quality`, `boxes_jpeg_quality`, `light_measure_width`, `moved_within_sec` |
| `vision/camera_sources.py` 64/235, 66/236, 151, 172, 181, 192-193 | `camera.fps_gap_max_sec`, `fps_smoothing`, `phone_tilt_max_age_sec`, `webcam_warmup_reads`, `webcam_max_index`, `webcam_width` / `webcam_height` |
| `vision/camera_sources.py` 243 (webcam's own `3`) | use `camera.disconnect_after_sec` |
| `chat/lens.py` 28 | `lens.check_every_sec` |
| `vision/camera_health.py` 26, 36, 40, 41, 45-53, 124, 133, 203, 241, 298 | `lens.measure_width`, `edge_touch_px`, `fixed_after_sec`, `edge_tip_after_sec`, `wait_before_saying_sec.{lost, covered, dark, glare, blurry, bumped, empty, cutoff, slow}`, `white_level`, `fills_picture`, `reading_fresh_sec`, `cutoff_forget_sec`, `cutoff_repeat_times` |
| `vision/relationships.py` 43-166, 279, 349, 759, 792-794; `app.py` 80; `web/routes.py` 526 | the `safety:` section: `edge_proximity`, `danger_zones`, `liquids` / `foods` / `sharp`, `zone_dangers`, `harmless_pairs`, `chain_reaction`, `temporal` (incl. `repeated_window_sec`), `risk_weights`, `risk_level_steps`, `desk_border_share`, `footprint_share` / `footprint_min_px`, `max_desk_corners` |
| `vision/desk_monitor.py` 18-19, 42, 50, 102 | `alerts.pet_labels` / `plant_labels`, `cooldown_max_times`, `recent_kept`, `dangerous_from_risk` |
| `app.py` 102, 103, 154 | `event_log.prune_every_min`, `habit_tracker.save_check_every_sec`, `reminders.someone_here_sec` |
| `events/stream.py` 76 | `event_stream.queue_size` |
| `chat/llm.py` 55, 89, 98, 117/124, 161, 164, 171-172/188, 184, 214, 230 | `chat.min_reply_words`, `llm.gemini_url`, `groq_url`, `ollama_keepalive_start_sec` / `ollama_keepalive_sec`, `ollama_timeout_sec`, `gemini_thinking_budget`, `ollama_retry_sec` / `ollama_warmer_per_retry`, `rate_limit_retry_sec`, `llm.replies.vision`, `vision_retry_sec` |
| reply sizes in `message_generator.py`, `idle_chat.py`, `lost_items.py`, `daily_summary.py`, `llm.py` | `llm.replies.{chat, alert, nudge, personality, idle_topic, idle_line, idle_director, lost_alert, lost_answer, daily_recap, vision}` = `{max_tokens, temperature}` (no temperature = the agent's own) |
| `chat/message_generator.py` 42, 74, 100, 105, 130, 275, 277 | `chat.recent_kept`, `web.live_queue_size`, `chat.memory_reply_chars`, `memory_user_chars`, `personality_min_words`, `memory_match_chars`, `ollama_memory_lines` |
| `chat/context.py` 32, 41, 45, 58, 65, 68, 198, 223, 242, 249 | `chat.history_from_app`, `message_max_chars`, `name_max_chars`, `ollama_history_turns`, `summary_turns`, `summary_chars`, `recent_events_kept`, `other_objects_shown`, `insights_shown`, `recent_events_shown` |
| `chat/tracker_actions.py` 28, 30, 39, 41, 165 | `chat.todo_max_items`, `todo_max_chars`, `panel_views_window_sec`, `panel_views_min`, `habit_tracker.sunlight_default_min` |
| `chat/idle_chat.py` 58, 60, 71, 87, 143 | `idle_chat.memory_per_agent`, `memory_lines`, `topic_min_words`, `line_min_words`, `pause_between_lines_sec` + `pause_random_sec` |
| `chat/vision_habits.py` 28, 129-133, 145 | `vision_habits.min_every_sec`, `repeat_after_sec.{too_close, slouch, phone}`, `start_delay_sec` |
| `chat/lost_items.py` 40, 109, 119, 137, 193, 232, 267, 314 | `lost_items.compare_size`, `min_confidence`, `nearby_kept`, `start_delay_sec`, `detector_fresh_sec`, `nearby_mentioned`, `answer_min_words`, `just_seen_sec` |
| `chat/daily_summary.py` 42, 54, 60 | `daily_summary.objects_listed`, `start_delay_sec`, `check_every_sec` |
| `desk_state.py` 120, 124, 126 | `snapshots.crop_padding`, `crop_min_px`, `jpeg_quality` |
| `habits/tracker.py` 169, 264, 276, 287, 304, 306, 309, 311, 318, 402 | `habit_tracker.gap_counted_max_sec`, `save_every_sec`, `breaks_kept`, `drinks_kept`, `glass_ml`, `button_break_min`, `sunlight_default_min`, `afternoon_light_hours`, `food_label_max_chars`, `breaks_shown` |
| `habits/protocols.py` 78, 132, 139, 174, 180, 207, 209, 221, 238, 262, 297 | in `config/protocols.yaml`: `hydration.on_waking_window_min` / `on_waking_done_share`, `breaks.walk_window_after_meal_min`, `walk_repeat_after_hours`, `light.sunset_guess_before_bed_hours`, `morning_grace_min`, `evening_bright_min`, `bed_soon_min`, `day_dim_margin_min`, `screens_until_after_bed_hours`, `phone_free.before_bed_until_after_bed_hours` / `before_bed_on_desk_min`, `food.repeat_after_hours` |
| `web/routes.py` 41, 140, 271, 274, 384, 419, 470, 479; `phone_server.py` 20; `video_stream.py` 40, 45 | `web.max_request_mb`, `photo_cache_sec`, `live_retry_ms`, `live_keepalive_sec`, `chat.panel_views_kept`, `web.list_sizes.{habit_log, log_events, log_detections}`, `video_resend_sec`, `video_check_every_sec` |

## New settings for the refactor

| Setting | What |
|---|---|
| `pi.detector.*` | `model`, `format` (ncnn / pt), `image_size`, `confidence`, `half`, `url` (127.0.0.1:8100), `wait_per_picture_sec`, `failures_before_pause`, `pause_start_sec`, `pause_max_sec`, `health_check_every_sec` |
| `pi.upload_retry_start_sec` / `upload_retry_max_sec` | the camera program's back-off when the computer is away |
| `pi.hot_warning_c`, `pi.clock_off_warning_sec` | when Lens mentions a hot Pi or a Pi clock that's off |
| `pi.restart_after_missed_pictures` | the camera program's watchdog: no picture for this many picture times, so it stops and systemd starts it again. `pi.delete_every_min: 0` means never, as in the old program |
| `supervisor.restart_start_sec` / `restart_max_sec` | the Supervisor's `first_retry_sec` / `max_retry_sec` |
| `llm.timeout_sec`, `retry_start_sec`, `retry_max_sec`, `breaker_failures`, `breaker_open_sec` (`max_retries` = tries) | `ResilientChatModel`. Cloud calls had no time limit before; 30 s is new behavior |
| `event_stream.queue_size` | per consumer (today's 2000) |
| `web.pi_upload_max_mb` | biggest `/api/pi/frame` upload |
| `logging.folder`, `level`, `file_size_mb`, `files_kept` | `setup_logging` |

## Things to keep the same as the old code

- **Safety rules on top of the defaults.** The `safety:` section holds the old built-in values;
  `config/safety_rules.json` (saved by the Settings page) is laid on top, as today. It sets the laptop zone to
  134, for example.
- **Food labels.** The food categories are `safety.foods` plus the labels of agents with the `food` habit (and
  the same for `hydration` → liquids), as the old `relationships.set_categories()` did.
- **Two "someone is at the desk" windows.** The app's `user_active()` uses `reminders.someone_here_sec` (600 s);
  the habit tracker uses `presence.user_active_window_sec` (90 s). Keep both.
- **Recent events in prompts.** `context.py` says "RECENT EVENTS (last 10)" but lists 5
  (`recent_events_kept` / `recent_events_shown`). Keep it as is.
- **Left in code on purpose.** Drawing details, how long a log line may get, sun-position maths, and
  certificate sizes.
