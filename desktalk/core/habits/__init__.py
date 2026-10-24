"""
Habits: the Protocols rules, today's habit numbers, the reminders, your preferences and the desk status.

    habit_rules.py   the habit rules from config/protocols.yaml, as plain functions returning Nudges
    tracker.py       HabitTracker, the front: today's numbers, the buttons, the nudges (HabitsView, HabitLog)
    habit_reader.py  HabitReader: sips, breaks, phone-free time, light and food from the camera
    habit_days.py    HabitDay (one day's numbers, as in data/habits/<day>.json) and HabitDays (saving, midnight)
    reminders.py     HabitReminders: a background job that sends one nudge at a time
    preferences.py   PreferencesFile: wake time, bedtime and quiet hours chosen in the app
    desk_status.py   DeskStatusService: "is the camera on, is someone at the desk?"
    sun_times.py     sunrise and sunset for the light habits
"""
