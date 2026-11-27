# Habits

The habits come from Andrew Huberman's *Protocols*. The desk only nudges when the camera saw a reason, and the message
says what it saw. Every number is in config/protocols.yaml, next to the chapter it comes from.

- Water (Nutrition Protocol 2): What the book says: 18 to 24 oz on waking; about 80 oz (2.4 L) in the first 10 hours;
  less in the hours before bed. What the desk does: Counts a sip when the drink is lifted and put back, or leaves the
  picture and comes back moved a little. It doesn't count a sip when a cup comes back exactly where it was (the camera
  only missed it, or a hand covered it), when the camera is covered, or when a cup seems to move while nobody is at
  the desk. Nudges when you're behind and haven't sipped in 45 minutes; quiet in the 2 hours before bed.
- Breaks (Exercise Protocol 1, Learning Protocol 5): What the book says: Walk 5 minutes for every 30 sitting; 5 to 10
  minutes after meals; no phone during breaks. What the desk does: Counts sitting time, and logs a break by itself
  when you're away 5 minutes. Nudges after 30 minutes, and after food leaves the desk.
- Light, day and night (Sleep 1-3, Light 1-3, Sleep 14): What the book says: Sunlight within an hour of waking (10, 20
  or 30 minutes for clear, cloudy or overcast); light before sunset; dim lights and no screens in the last hour before
  bed. What the desk does: Asks about morning sunlight, reminds you before sunset, notices a bright room at night (the
  Pi's camera measures the light) or a dim one in the day, and says "screens off" an hour before bed.
- Phone-free time (Learning Protocol 1): What the book says: Put your phone away; even having it nearby costs focus.
  What the desk does: Tracks phone-free desk time and streaks; nudges when the phone sits on the desk while you work,
  and in the last hour before bed.

Set your wake time and bedtime in the app, under Your day in Settings. The Habits page shows today's numbers, with
buttons for what the camera can't see (a sip, a break, going outside).

## To-dos

To-dos are Book's list. Give one a due date in the app, or in the chat ("add my essay, due Friday"). Book reminds you
the day before, on the day, and each day it's late. A to-do with no date gets a check-in every 2 hours, naming a
different one each time. Desk Talk keeps the list itself (data/todos.json), and ticking one off shows in the event
log. A to-do's text never leaves the Pi, except to an AI on your own computer if you set one up
([AI_AND_CLOUD.md](AI_AND_CLOUD.md)).
