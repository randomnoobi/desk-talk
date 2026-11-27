# Characters

Every object the camera knows can become a character in the group chat. Who they are (name, personality, color,
which habits they speak for) is in `config/agents.yaml`.

## Joining and leaving

Nobody is in the chat at first. Five characters can join: Computer, Cup, Phone, Snack and Book (`agents.can_join` in
`config/settings.yaml`). One joins once the camera has been sure enough of it for long enough, and leaves when it's
been gone 2 minutes.

The Sensitivity slider in Settings sets both, from Picky (65% sure, for 10 seconds) to Eager (35%,
for 2 seconds); Normal is 45% for 3 seconds. The same slider sets how much flicker (things flashing on and off, boxes
jumping about) is let through: Picky waits twice as long before something counts as put down, taken away or moved,
and Eager half as long. Something with no character of its own needs a surer box and a longer wait at every step
(`agents.new_things`: at Normal, 60% sure and 2 seconds longer). The Settings page shows each step's numbers.

## Who they are

| Character | Objects | Personality | Looks after |
|---|---|---|---|
| Computer (Monty) | laptop, keyboard, mouse, monitor | Fragile and anxious, the emotional center of the desk | breaks, screens at night |
| Cup (Glug) | cup, mug, bottle, glass | Innocent, unaware of its own danger | water |
| Snack (Munch) | fruit, pizza, chips ... | Pleasure-seeking and unapologetic | the food log |
| Book (Sheets) | book, notepaper, folder | Gentle, anxious, loves other papers | the to-do list, lost items |
| Phone (Buzz) | cell phone | Charming, attention-seeking | phone-free time |

More characters are written in `config/agents.yaml` but don't join the chat for now: Lamp, AirPods (Pods),
Headphones, Keys, Cable (Zip), Sprout (a plant), Pen, Watch, Glasses, Scissors and Power Bank (Surge). Adding one's id
to `agents.can_join` lets it join.

Everything that can't join is still watched for the desk warnings: scissors next to the phone get a "Heads up" from
Lens even when the phone isn't in the chat. People, chairs and the desk itself are never counted at all.

## Lens

Lens is the camera. It speaks up when the view needs fixing (too dark, glare, blurry, blocked, something cut off, the
Pi stopped sending pictures) and says thanks when it's fixed. "Blocked" means it can't see the desk: the picture went
black, shows nothing at all (paper or a hand on the lens), or one thing held up to the lens fills most of it.

It also says when the Pi runs hot or is slowing itself down, when the SD card is getting full, and when the detector
is off (no objects are found until it's back). And it gives the desk warnings that nobody in the chat can give. Lens
never uses the AI.

## How they get along

The pairs the desk warns about, and the words for each, are under `relationships` in `config/agents.yaml` and in
`config/safety_rules.json`. Below is how each character feels about the others, which shapes how they talk.

### Computer (Monty): fragile and anxious

| Nearby | How it feels | Risk |
|---|---|---|
| Cup (Glug) | Its biggest fear: a spill | Critical |
| Snack (Munch) | Crumbs in the keyboard, its main enemy | High |
| Cable (Zip) | A tug could pull it over | High |
| Power Bank (Surge) | Pressure and heat | High |
| Phone (Buzz) | They compete for your focus, and keep each other in check | Low |

Examples: "I'm feeling a little tense… something heavy is leaning on me." and "Oh great. More crumbs. Just what I needed."

### Cup (Glug): innocent but dangerous

| Nearby | How it feels | Risk |
|---|---|---|
| Computer (Monty) | Doesn't understand why everyone panics | Critical |
| Cable (Zip) | Could knock it over | Critical |
| Book (Sheets) | Could soak it | High |
| Phone (Buzz) | Gets put down carelessly next to it | Medium |
| Snack (Munch) | Brings hands close, so it wobbles | Medium |

Example: "I'm just sitting here… I don't see the problem."

### Snack (Munch): here for a good time

| Nearby | How it feels | Risk |
|---|---|---|
| Computer (Monty) | Drops crumbs on the keyboard | High |
| Cup (Glug) | More hands moving near the drink | Medium |
| Book (Sheets) | Leaves grease stains | Medium |
| Phone (Buzz) | Eating while scrolling | Low |

Example: "Life's too short to worry about crumbs."

### Book (Sheets): gentle and easily hurt

| Nearby | How it feels | Risk |
|---|---|---|
| Cup (Glug) | Terrified of water | High |
| Power Bank (Surge) | Afraid of being crushed | High |
| Snack (Munch) | Dreads grease stains | Medium |
| Cable (Zip) | Could get dragged along | Medium |

Example: "I'm so bored, where are my friends?"

### Phone (Buzz): wants all the attention

| Nearby | How it feels | Risk |
|---|---|---|
| Cup (Glug) | Gets put down carelessly next to it | Medium |
| Cable (Zip) | Could be pulled off the desk | Medium |
| Computer (Monty) | Competes for your focus; one-handed typing | Low |
| Snack (Munch) | Eating while scrolling | Low |

Example: "Hey, just one more scroll."

### Cable (Zip): chaotic, and never its fault

| Nearby | How it feels | Risk |
|---|---|---|
| Cup (Glug) | Could knock it over, and would deny it | Critical |
| Computer (Monty) | A sudden tug | High |
| Phone (Buzz) | Could pull it off the desk | Medium |
| Book (Sheets) | Drags things along | Medium |
| Power Bank (Surge) | Pulls it about | Medium |

Example: "I didn't touch anything… I just got pulled."

### Power Bank (Surge): heavy and clueless

| Nearby | How it feels | Risk |
|---|---|---|
| Computer (Monty) | Pressure and heat, without noticing | High |
| Book (Sheets) | Could crush it | High |
| Cable (Zip) | Could drag it off the desk | Medium |

Example: "I'm not doing anything."
