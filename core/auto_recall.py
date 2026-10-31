"""
core/auto_recall.py — past conversations pulled in before the model is asked.

When a prompt points at the past ("yesterday", "two days ago", "what did I say
about…"), the daemon searches the daily logs itself and adds the hits to the
user's message. The model no longer has to decide to call recall, and the
answer costs no extra tool round. Hits are what was said, not what is true now
(WHO_WINS rank 5), and the block says so.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from memory import recall_index

MAX_CHARS = 3000
TURN_CHARS = 400
HEADER = ("[RECALLED FROM DAILY LOGS: this is already the recall search for this message, so do not "
          "search past conversations again. It is what was said then, not proof of what is true now. "
          "Where it disagrees with [LEARNED MEMORY], memory is newer and wins: say what was said then and what is true now.]")

_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "dva": 2, "dve": 2, "dvema": 2, "tri": 3, "tremi": 3, "štiri": 4, "štirimi": 4, "pet": 5, "petimi": 5,
    "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
}
_NUM = r"(\d+|" + "|".join(_NUMBERS) + r")"

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Order matters: the day before yesterday must win over yesterday.
_SPANS: list[tuple[re.Pattern, object]] = [
    (re.compile(r"\b(day before yesterday|predvčerajšnjim|l'altro ieri|altroieri)\b"), (2, 2)),
    (re.compile(r"\b(yesterday|včeraj|ieri)\b"), (1, 1)),
    (re.compile(rf"\b{_NUM} days? ago\b|\bpred {_NUM} dnev|\b{_NUM} giorni fa\b"), "n_days"),
    (re.compile(r"\b(last week|past week|prejšnji teden|prejšnjem tednu|la settimana scorsa)\b"), (7, 1)),
    (re.compile(r"\b(earlier today|this morning|danes zjutraj|stamattina)\b"), (0, 0)),
]

# Prompts about past conversation that name no date: search every day.
_CUES = re.compile(
    r"\b(what did i (say|tell you|ask)|did i (tell|mention|say)|remember when|we (talked|spoke) about"
    r"|last time we|you said|kaj sem (rekel|povedal)|ti povedal|cosa ho detto|ti ho detto)\b"
)

# Words that place the question in time, or are Slovenian/Italian filler, and say nothing
# about its subject (English filler is already dropped by memory_tool._keywords).
_NOT_SUBJECT = frozenset(
    "yesterday days day ago last past week weeks today earlier morning this time remember talked "
    "spoke said say tell told mention mentioned ask asked discuss discussed conversation "
    "talk talking speak chat chatted work worked working do doing done "
    "pogovarjala pogovarjali pogovor sva smo delal delala "
    "kaj se si sem je so ste in na za da ne ali ki mi ti to ta tem tega dan dneva dnevoma dnevom dneve "
    "che cosa di il la lo le gli un una ho hai ha abbiamo del della per con su giorno parlato "
    "včeraj pred dnevi teden zjutraj danes rekel povedal ieri giorni fa settimana scorsa oggi detto".split()
    + list(_NUMBERS) + list(_WEEKDAYS)
)


def window(prompt: str, today: date | None = None) -> list[str] | None:
    """The YYYY-MM-DD days a prompt points at; [] for an undated memory question; None if neither."""
    today = today or date.today()
    text = prompt.casefold()
    for pattern, span in _SPANS:
        m = pattern.search(text)
        if not m:
            continue
        if span == "n_days":
            raw = next(g for g in m.groups() if g)
            n = int(raw) if raw.isdigit() else _NUMBERS[raw]
            span = (n, n)
        oldest, newest = span
        return [(today - timedelta(days=d)).isoformat() for d in range(newest, oldest + 1)]
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b(on|last) {name}\b", text):
            back = (today.weekday() - i) % 7 or 7
            return [(today - timedelta(days=back)).isoformat()]
    if _CUES.search(text):
        return []
    return None


def _subject_words(prompt: str) -> set[str]:
    from actions.memory_tool import _keywords
    return {w for w in _keywords(prompt) if w not in _NOT_SUBJECT}


def recall_block(prompt: str, today: date | None = None) -> str | None:
    """Matching past turns for a prompt that points at the past, or None."""
    days = window(prompt, today)
    if days is None:
        return None
    words = _subject_words(prompt)
    if words:
        # A question with a subject the logs don't mention ("F1 results last week")
        # gets nothing rather than a pile of unrelated turns.
        hits = recall_index.search(words, limit=6, days=days or None)
    else:
        hits = recall_index.recent(days, limit=6) if days else []
    if not hits:
        return None
    lines, used, dropped = [], len(HEADER), 0
    for day, text in hits:
        line = f"• [Said on {day}] {text[:TURN_CHARS]}"
        if used + len(line) + 1 > MAX_CHARS:
            dropped += 1
            continue
        lines.append(line)
        used += len(line) + 1
    if dropped:
        print(f"[Kurek Recall] WARNING: {dropped} recalled turn(s) left out to stay under "
              f"{MAX_CHARS} chars.", flush=True)
    return HEADER + "\n" + "\n".join(lines)
