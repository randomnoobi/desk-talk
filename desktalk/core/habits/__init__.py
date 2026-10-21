"""
Habits: the Protocols rules, today's habit numbers, the reminders, your preferences and the desk status.

    protocols.py     the habit rules from config/protocols.yaml, as plain functions returning Nudges
    tracker.py       HabitTracker: sips, breaks, phone-free time, light and food from the camera (HabitsView)
    reminders.py     HabitReminders: a background job that sends one nudge at a time
    preferences.py   Preferences: wake time, bedtime and quiet hours chosen in the app
    desk_status.py   DeskStatus: "is the camera on, is someone at the desk?"
    sun_times.py     sunrise and sunset for the light habits
"""
