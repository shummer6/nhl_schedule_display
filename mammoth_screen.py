#!/usr/bin/env python3
"""Render the next Utah Mammoth game as a Kindle-ready PNG.

Output is an 8-bit grayscale PNG with no alpha channel, which is what the
Kindle Online Screensaver plugin expects. Default size is 758x1024
(Kindle Paperwhite 1st gen, portrait).

Usage:
    python mammoth_screen.py --out mammoth.png
    python mammoth_screen.py --json sample_schedule.json \
        --now 2026-10-06T16:35:00Z --out test.png

Needs: pip install pillow tzdata
"""
import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

TEAM = "UTA"
LOCAL_TZ = ZoneInfo("America/Denver")
SCHEDULE_URL = f"https://api-web.nhle.com/v1/club-schedule-season/{TEAM}/now"
STANDINGS_URL = "https://api-web.nhle.com/v1/standings/now"
UPCOMING_STATES = {"FUT", "PRE", "LIVE", "CRIT"}
LIVE_STATES = {"LIVE", "CRIT"}
COMPLETED_STATES = {"OFF", "FINAL"}
REGULAR_SEASON_GAME_TYPE = 2

BLACK, DARK, MID, LIGHT, WHITE = 0, 55, 120, 228, 255

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    , "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"
    , "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    , "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    , "C:/Windows/Fonts/arialbd.ttf"
]


@lru_cache(maxsize=None)
def load_font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


# ---------- data ----------

def load_json(url, json_path):
    if json_path:
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)
    req = urllib.request.Request(
        url
        , headers={"User-Agent": "mammoth-kindle-screen/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def load_games(json_path):
    return load_json(SCHEDULE_URL, json_path)["games"]


def load_standings(json_path):
    return load_json(STANDINGS_URL, json_path)["standings"]


def start_utc(game):
    return datetime.fromisoformat(game["startTimeUTC"].replace("Z", "+00:00"))


def upcoming_games(games, now):
    """Games not yet finished, soonest first.

    A game still marked as upcoming but whose start was over 6 hours ago is
    treated as stale data and skipped, unless it is actually live.
    """
    out = []
    for g in games:
        state = g.get("gameState")
        if state not in UPCOMING_STATES:
            continue
        hours_ago = (now - start_utc(g)).total_seconds() / 3600
        if state not in LIVE_STATES and hours_ago > 6:
            continue
        out.append(g)
    out.sort(key=start_utc)
    return out


def season_series(games, opponent_abbrev, now):
    """Return completed regular-season wins for Utah and its next opponent."""
    utah_wins = opponent_wins = 0
    for game in games:
        if game.get("gameType") != REGULAR_SEASON_GAME_TYPE:
            continue
        if game.get("gameState") not in COMPLETED_STATES:
            continue
        if start_utc(game) > now:
            continue

        away = game.get("awayTeam", {})
        home = game.get("homeTeam", {})
        if away.get("abbrev") == TEAM:
            utah, opponent = away, home
        elif home.get("abbrev") == TEAM:
            utah, opponent = home, away
        else:
            continue
        if opponent.get("abbrev") != opponent_abbrev:
            continue
        if "score" not in utah or "score" not in opponent:
            continue

        if utah["score"] > opponent["score"]:
            utah_wins += 1
        else:
            opponent_wins += 1
    return utah_wins, opponent_wins


def utah_division(standings):
    utah = next(
        (row for row in standings if row.get("teamAbbrev", {}).get("default") == TEAM)
        , None
    )
    if not utah:
        return "DIVISION", []
    division = utah.get("divisionName", "Division")
    rows = [row for row in standings if row.get("divisionName") == division]
    rows.sort(key=lambda row: row.get("divisionSequence", 99))
    return division, rows


def side_and_opponent(game):
    """Return ('AT' or 'VS', opponent team dict) from Utah's point of view."""
    if game["awayTeam"]["abbrev"] == TEAM:
        return "AT", game["homeTeam"]
    return "VS", game["awayTeam"]


def tv_line(game):
    nets = []
    for b in game.get("tvBroadcasts", []):
        name = b.get("network", "").strip()
        if b.get("countryCode") == "US" and name and name not in nets:
            nets.append(name)
    nets.sort(key=lambda n: n != "Utah16")  # stable: local channel first
    return ", ".join(nets[:2])


# ---------- formatting ----------

def fmt_date(dt):
    return f"{dt:%a}, {dt:%b} {dt.day}".upper()


def fmt_time(dt):
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def day_phrase(start_local, now_local):
    delta = (start_local.date() - now_local.date()).days
    if delta <= 0:
        return "TODAY"
    if delta == 1:
        return "TOMORROW"
    return f"IN {delta} DAYS"


def ordinal(number):
    if 10 <= number % 100 <= 20:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(number % 10, "TH")
    return f"{number}{suffix}"


# ---------- drawing ----------

def fit_font(draw, text, max_w, size, min_size=18):
    while size > min_size and draw.textlength(text, font=load_font(size)) > max_w:
        size -= 2
    return load_font(size)


def centered(draw, width, y, text, size, fill, max_w):
    font = fit_font(draw, text, max_w, size)
    draw.text((width / 2, y), text, font=font, fill=fill, anchor="mt")
    return y + int(font.size * 1.12)


def render(upcoming, all_games, standings, now_utc, size):
    width, height = size
    pad = 40
    inner = width - 2 * pad
    img = Image.new("L", size, WHITE)
    d = ImageDraw.Draw(img)
    now_local = now_utc.astimezone(LOCAL_TZ)

    game = upcoming[0] if upcoming else None
    if game:
        prefix, opp = side_and_opponent(game)
        opponent_abbrev = opp["abbrev"]
        utah_wins, opponent_wins = season_series(all_games, opponent_abbrev, now_utc)
        if utah_wins + opponent_wins:
            matchup = f"SEASON SERIES   UTA {utah_wins} - {opponent_wins} {opponent_abbrev}"
        else:
            matchup = "FIRST MEETING THIS SEASON"
    else:
        matchup = "REGULAR SEASON"

    # The division table already contains Utah's record, so the banner carries
    # the more opponent-specific season-series context instead.
    d.rectangle((0, 0, width, 124), fill=BLACK)
    header_font = fit_font(d, "UTAH MAMMOTH", inner, 52)
    d.text((width / 2, 39), "UTAH MAMMOTH", font=header_font, fill=WHITE, anchor="mm")
    matchup_font = fit_font(d, matchup, inner, 28)
    d.text((width / 2, 94), matchup, font=matchup_font, fill=WHITE, anchor="mm")

    footer = f"Updated {now_local:%b} {now_local.day}"

    if game:
        start_local = start_utc(game).astimezone(LOCAL_TZ)
        live = game.get("gameState") in LIVE_STATES
        chip_text = "LIVE NOW" if live else day_phrase(start_local, now_local)

        d.text((pad, 151), "NEXT GAME", font=load_font(23), fill=MID, anchor="lt")
        chip_font = fit_font(d, chip_text, 210, 24)
        chip_w = d.textlength(chip_text, font=chip_font) + 36
        chip_x = width - pad - chip_w
        d.rounded_rectangle((chip_x, 144, width - pad, 194), radius=25, fill=BLACK)
        d.text((chip_x + chip_w / 2, 169), chip_text, font=chip_font, fill=WHITE, anchor="mm")

        opponent_name = (
            f"{prefix} {opp['placeName']['default']} {opp['commonName']['default']}".upper()
        )
        opponent_font = fit_font(d, opponent_name, inner, 53)
        d.text((pad, 190), opponent_name, font=opponent_font, fill=BLACK, anchor="lt")
        date_time = f"{fmt_date(start_local)}  •  {fmt_time(start_local)} MT"
        date_font = fit_font(d, date_time, inner, 34)
        d.text((pad, 250), date_time, font=date_font, fill=DARK, anchor="lt")

        details = []
        venue = game.get("venue", {}).get("default", "")
        if venue:
            details.append(venue)
        tv = tv_line(game)
        if tv:
            details.append(f"TV: {tv}")
        if details:
            details_text = "  •  ".join(details)
            details_font = fit_font(d, details_text, inner, 23)
            d.text((pad, 298), details_text, font=details_font, fill=MID, anchor="lt")
    else:
        d.text((pad, 165), "NO UPCOMING GAMES FOUND", font=load_font(34), fill=BLACK, anchor="lt")

    d.line((pad, 335, width - pad, 335), fill=BLACK, width=3)

    division_name, division_rows = utah_division(standings)
    d.text((pad, 358), f"{division_name.upper()} DIVISION", font=load_font(34), fill=BLACK, anchor="lt")
    utah_row = next(
        (row for row in division_rows if row.get("teamAbbrev", {}).get("default") == TEAM)
        , None
    )
    if utah_row:
        place_text = f"{ordinal(utah_row.get('divisionSequence', 0))} PLACE"
        place_font = load_font(24)
        place_w = d.textlength(place_text, font=place_font) + 36
        d.rounded_rectangle(
            (width - pad - place_w, 354, width - pad, 400), radius=22, fill=BLACK
        )
        d.text(
            (width - pad - place_w / 2, 377), place_text
            , font=place_font, fill=WHITE, anchor="mm"
        )

    y = 420
    d.rectangle((pad, y, width - pad, y + 42), fill=LIGHT)
    columns = [(54, "#", "lm"), (105, "TEAM", "lm"), (390, "GP", "mm"),
               (520, "W-L-OT", "mm"), (695, "PTS", "mm")]
    for x, label, anchor in columns:
        d.text((x, y + 21), label, font=load_font(21), fill=DARK, anchor=anchor)

    y += 42
    row_h = 61
    for row in division_rows[:8]:
        abbrev = row.get("teamAbbrev", {}).get("default", "")
        rank = row.get("divisionSequence", 0)
        selected = abbrev == TEAM
        if selected:
            d.rectangle((pad, y, width - pad, y + row_h), fill=BLACK)
        elif rank % 2 == 0:
            d.rectangle((pad, y, width - pad, y + row_h), fill=242)
        color = WHITE if selected else BLACK
        font = load_font(30 if selected else 27)
        record_text = f"{row.get('wins', 0)}-{row.get('losses', 0)}-{row.get('otLosses', 0)}"
        values = [(60, str(rank), "lm"), (105, abbrev, "lm"),
                  (390, str(row.get("gamesPlayed", 0)), "mm"),
                  (520, record_text, "mm"), (695, str(row.get("points", 0)), "mm")]
        for x, value, anchor in values:
            d.text((x, y + row_h / 2), value, font=font, fill=color, anchor=anchor)
        y += row_h

    d.text((width - pad, height - 18), footer, font=load_font(17), fill=MID, anchor="rm")
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="mammoth.png")
    ap.add_argument("--json", help="read schedule from a local file instead of the NHL API")
    ap.add_argument(
        "--standings-json", help="read standings from a local file instead of the NHL API"
    )
    ap.add_argument("--now", help="override the clock, e.g. 2026-10-06T16:35:00Z (for testing)")
    ap.add_argument("--size", default="758x1024", help="WIDTHxHEIGHT of the output image")
    args = ap.parse_args()

    width, height = (int(v) for v in args.size.lower().split("x"))
    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)

    try:
        games = load_games(args.json)
        standings = load_standings(args.standings_json)
    except Exception as exc:  # leave any existing image untouched on failure
        print(f"Could not load schedule: {exc}", file=sys.stderr)
        return 1

    upcoming = upcoming_games(games, now)
    img = render(upcoming, games, standings, now, (width, height))
    img.save(args.out, format="PNG")
    nxt = upcoming[0]["startTimeUTC"] if upcoming else "none"
    print(
        f"Wrote {args.out} ({width}x{height}); next game start (UTC): {nxt}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
