from urllib.parse import quote

try:
    import requests
except ImportError:  # pragma: no cover - requests ships in requirements
    requests = None

_WTTR_URL = "https://wttr.in/{city}?format=j1"


def _desc(block: dict) -> str:
    try:
        return block["weatherDesc"][0]["value"].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _midday(day: dict) -> dict:
    """Pick the hourly slot closest to noon for a day's headline condition."""
    hourly = day.get("hourly") or []
    if not hourly:
        return {}
    return min(hourly, key=lambda h: abs(int(h.get("time", "0") or 0) - 1200))


def summarize_weather(data: dict, city: str, when: str = "today") -> str:
    """Turn a wttr.in j1 payload into one short spoken sentence."""
    days = data.get("weather") or []
    when_l = (when or "today").lower()

    if "tomorrow" in when_l and len(days) > 1:
        day = days[1]
        cond = _desc(_midday(day)) or "mixed conditions"
        return (f"Tomorrow in {city}: {cond.lower()}, between "
                f"{day.get('mintempC')} and {day.get('maxtempC')} degrees Celsius.")

    cur = (data.get("current_condition") or [{}])[0]
    cond = _desc(cur) or "unknown conditions"
    parts = [
        f"Right now in {city} it's {cond.lower()}, {cur.get('temp_C')} degrees Celsius",
        f"feels like {cur.get('FeelsLikeC')}",
        f"humidity {cur.get('humidity')} percent",
        f"wind {cur.get('windspeedKmph')} kilometres per hour"
        + (f" from the {cur['winddir16Point']}" if cur.get("winddir16Point") else ""),
    ]
    sentence = ", ".join(parts) + "."
    if days:
        today = days[0]
        sentence += (f" Today's low is {today.get('mintempC')} and the high is "
                     f"{today.get('maxtempC')} degrees.")
    return sentence


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    parameters = parameters or {}
    city     = parameters.get("city")
    when     = parameters.get("time", "today")

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Sir, the city is missing for the weather report."
        _log(msg, player)
        return msg

    city = city.strip()
    when = (when or "today").strip() if isinstance(when, str) else "today"

    if requests is None:
        msg = "I can't fetch the weather: the 'requests' package is not installed."
        _log(msg, player)
        return msg

    try:
        resp = requests.get(_WTTR_URL.format(city=quote(city)), timeout=8,
                            headers={"User-Agent": "kurek/1.0"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        msg = f"Sir, I couldn't fetch the weather for {city}: {e}"
        _log(msg, player)
        return msg

    try:
        msg = summarize_weather(data, city, when)
    except Exception as e:
        msg = f"Sir, the weather service returned data I couldn't read for {city}: {e}"
        _log(msg, player)
        return msg

    _log(msg, player)
    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather in {city} {when}", response=msg)
        except Exception:
            pass
    return msg


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "weather_report",
    "description": "Gives the current weather report (and today's min/max, or tomorrow's forecast) for a city",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City name"
            },
            "time": {
                "type": "STRING",
                "description": "Optional: 'today' (default) or 'tomorrow'"
            }
        },
        "required": [
            "city"
        ]
    },
    "handler": weather_action,
}
