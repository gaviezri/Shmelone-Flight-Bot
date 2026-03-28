import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

from airlabs import get_routes
from notifier import send_message
from state import load_state, save_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

AIRLINES = {
    "IZ": "Arkia",
    "LY": "El Al",
}

DAY_WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


# ---------------------------------------------------------------------------
# Upcoming dates helper
# ---------------------------------------------------------------------------

def upcoming_dates(days: list[str], days_ahead: int = 14) -> list[date]:
    if not days:
        return []
    today = date.today()
    target_weekdays = {DAY_WEEKDAY[d] for d in days if d in DAY_WEEKDAY}
    return [
        today + timedelta(days=i)
        for i in range(days_ahead)
        if (today + timedelta(days=i)).weekday() in target_weekdays
    ]


def fmt_dates(dates: list[date]) -> str:
    if not dates:
        return "no flights in next 2 weeks"
    return ", ".join(d.strftime("%a %b %d") for d in dates)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def detect_changes(old: dict, new: dict) -> list[dict]:
    changes = []

    for key, route in new.items():
        if key not in old:
            changes.append({"type": "new_route", "route": route})
        else:
            old_route = old[key]
            if old_route.get("arr_iata") != route.get("arr_iata"):
                changes.append({"type": "destination_changed", "route": route, "old": old_route})
            elif set(old_route.get("days") or []) != set(route.get("days") or []):
                changes.append({"type": "days_changed", "route": route, "old": old_route})

    for key, route in old.items():
        if key not in new:
            changes.append({"type": "route_removed", "route": route})

    return changes


# ---------------------------------------------------------------------------
# Message formatters
# ---------------------------------------------------------------------------

def _route_summary(route: dict) -> str:
    flight = route.get("flight_iata") or route.get("flight_number", "?")
    dep = route.get("dep_iata", "?")
    arr = route.get("arr_iata", "?")
    dep_time = route.get("dep_time", "?")
    days = route.get("days") or []
    dates = upcoming_dates(days)
    return (
        f"<b>{flight}</b>  {dep} → {arr}  [{dep_time}]\n"
        f"  {fmt_dates(dates)}"
    )


def format_change_message(airline_name: str, change: dict) -> str:
    route = change["route"]
    flight = route.get("flight_iata") or route.get("flight_number", "?")
    dep = route.get("dep_iata", "?")
    arr = route.get("arr_iata", "?")
    dep_time = route.get("dep_time", "?")
    days = route.get("days") or []
    duration = route.get("duration")
    duration_str = f"{duration} min" if duration else "?"
    dates = upcoming_dates(days)

    if change["type"] == "new_route":
        return (
            f"✈️ <b>New {airline_name} Flight!</b>\n"
            f"Flight: {flight}\n"
            f"Route:  {dep} → {arr}\n"
            f"Departs: {dep_time}  ({duration_str})\n"
            f"Next 2 weeks: {fmt_dates(dates)}"
        )

    if change["type"] == "destination_changed":
        old_arr = change["old"].get("arr_iata", "?")
        return (
            f"🔄 <b>{airline_name} Route Changed</b>\n"
            f"Flight: {flight}\n"
            f"Was: {dep} → {old_arr}\n"
            f"Now: {dep} → {arr}\n"
            f"Next 2 weeks: {fmt_dates(dates)}"
        )

    if change["type"] == "days_changed":
        old_days = ", ".join(change["old"].get("days") or []) or "?"
        new_days = ", ".join(days) or "?"
        return (
            f"📅 <b>{airline_name} Schedule Updated</b>\n"
            f"Flight: {flight}  {dep} → {arr}\n"
            f"Old days: {old_days}\n"
            f"New days: {new_days}\n"
            f"Next 2 weeks: {fmt_dates(dates)}"
        )

    if change["type"] == "route_removed":
        return (
            f"🚫 <b>{airline_name} Route Removed</b>\n"
            f"Flight: {flight}\n"
            f"Route: {dep} → {arr}"
        )

    return ""


def send_genesis_messages(bot_token: str, chat_ids: list[str], airline_name: str, routes: dict):
    """Send all current routes on first run, split into Telegram-safe chunks."""
    # Only include routes that actually fly in the next 2 weeks
    active = {
        k: r for k, r in routes.items()
        if upcoming_dates(r.get("days") or [])
    }

    header = (
        f"✈️ <b>{airline_name} monitoring started</b>\n"
        f"{len(routes)} routes tracked • "
        f"{len(active)} with flights in the next 2 weeks\n"
        f"{'─' * 30}\n"
    )

    # Sort by next departure date
    def next_dep(r):
        dates = upcoming_dates(r.get("days") or [])
        return dates[0] if dates else date.max

    sorted_routes = sorted(active.values(), key=next_dep)

    lines = [header]
    for route in sorted_routes:
        lines.append(_route_summary(route) + "\n")

    # Split into ≤4000-char chunks (Telegram limit is 4096)
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) > 4000:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)

    for chat_id in chat_ids:
        for chunk in chunks:
            send_message(bot_token, chat_id, chunk)
            time.sleep(0.3)


# ---------------------------------------------------------------------------
# Main check logic
# ---------------------------------------------------------------------------

def check_once(config: dict, state: dict):
    api_key = config["airlabs_api_key"]
    bot_token = config["telegram_bot_token"]

    # Support telegram_chat_id as a string or a list
    raw_ids = config["telegram_chat_id"]
    chat_ids = raw_ids if isinstance(raw_ids, list) else [raw_ids]

    departure_airports = config.get("departure_airports", ["TLV"])

    for iata, name in AIRLINES.items():
        try:
            current_routes = get_routes(api_key, iata, departure_airports)
            airline_state = state.get(iata, {})
            saved_routes = airline_state.get("routes", {})
            is_first_run = not airline_state.get("initialized", False)

            if is_first_run:
                logging.info("%s: first run — %d routes found.", name, len(current_routes))
                send_genesis_messages(bot_token, chat_ids, name, current_routes)
            else:
                changes = detect_changes(saved_routes, current_routes)
                if changes:
                    logging.info("%s: %d change(s) detected.", name, len(changes))
                    for change in changes:
                        msg = format_change_message(name, change)
                        if msg:
                            for chat_id in chat_ids:
                                send_message(bot_token, chat_id, msg)
                            time.sleep(0.5)
                else:
                    logging.info("%s: no changes (%d routes).", name, len(current_routes))

            state[iata] = {"initialized": True, "routes": current_routes}

        except Exception as exc:
            logging.error("%s: error — %s", name, exc)


def main():
    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    interval = config.get("check_interval_seconds", 7200)

    logging.info("Flight bot started. Interval: %d seconds.", interval)

    while True:
        state = load_state()
        check_once(config, state)
        save_state(state)
        logging.info("Next check in %d seconds.", interval)
        time.sleep(interval)


if __name__ == "__main__":
    main()
