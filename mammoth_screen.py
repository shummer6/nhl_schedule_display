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
UPCOMING_STATES = {"FUT", "PRE", "LIVE", "CRIT"}
LIVE_STATES = {"LIVE", "CRIT"}

BLACK, DARK, MID, WHITE = 0, 55, 120, 255

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

def load_games(json_path):
    if json_path:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        req = urllib.request.Request(
            SCHEDULE_URL
            , headers={"User-Agent": "mammoth-kindle-screen/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    return data["games"]


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


def fmt_short_date(dt):
    return f"{dt:%a} {dt:%b} {dt.day}".upper()


def fmt_time(dt):
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def day_phrase(start_local, now_local):
    delta = (start_local.date() - now_local.date()).days
    if delta <= 0:
        return "TODAY"
    if delta == 1:
        return "TOMORROW"
    return f"IN {delta} DAYS"


# ---------- drawing ----------

def fit_font(draw, text, max_w, size, min_size=18):
    while size > min_size and draw.textlength(text, font=load_font(size)) > max_w:
        size -= 2
    return load_font(size)


def centered(draw, width, y, text, size, fill, max_w):
    font = fit_font(draw, text, max_w, size)
    draw.text((width / 2, y), text, font=font, fill=fill, anchor="mt")
    return y + int(font.size * 1.12)


def render(games, now_utc, size):
    width, height = size
    pad = 40
    inner = width - 2 * pad
    img = Image.new("L", size, WHITE)
    d = ImageDraw.Draw(img)
    now_local = now_utc.astimezone(LOCAL_TZ)

    # header band
    d.rectangle((0, 0, width, 110), fill=BLACK)
    header_font = fit_font(d, "UTAH MAMMOTH", inner, 64)
    d.text((width / 2, 55), "UTAH MAMMOTH", font=header_font, fill=WHITE, anchor="mm")

    footer = f"Updated {now_local:%b} {now_local.day}"

    if not games:
        y = centered(d, width, 380, "NO UPCOMING", 76, BLACK, inner)
        centered(d, width, y, "GAMES FOUND", 76, BLACK, inner)
        d.text((width / 2, height - 30), footer, font=load_font(20), fill=MID, anchor="mm")
        return img

    game, rest = games[0], games[1:4]
    start_local = start_utc(game).astimezone(LOCAL_TZ)
    prefix, opp = side_and_opponent(game)
    live = game.get("gameState") in LIVE_STATES

    y = 128
    y = centered(d, width, y, "NEXT GAME", 28, MID, inner)
    y = centered(d, width, y + 4, prefix, 54, DARK, inner)
    y = centered(d, width, y, opp["placeName"]["default"].upper(), 104, BLACK, inner)
    y = centered(d, width, y, opp["commonName"]["default"].upper(), 84, BLACK, inner)

    y += 14
    d.line((pad, y, width - pad, y), fill=BLACK, width=3)
    y += 22

    y = centered(d, width, y, fmt_date(start_local), 84, BLACK, inner)
    y = centered(d, width, y + 4, f"{fmt_time(start_local)} MT", 66, BLACK, inner)

    # countdown chip
    chip_text = "LIVE NOW" if live else day_phrase(start_local, now_local)
    chip_font = fit_font(d, chip_text, inner - 60, 46)
    text_w = d.textlength(chip_text, font=chip_font)
    chip_h = int(chip_font.size * 1.5)
    x0 = (width - text_w) / 2 - 32
    y += 14
    d.rounded_rectangle((x0, y, width - x0, y + chip_h), radius=chip_h // 2, fill=BLACK)
    d.text((width / 2, y + chip_h / 2), chip_text, font=chip_font, fill=WHITE, anchor="mm")
    y += chip_h + 16

    venue = game.get("venue", {}).get("default", "")
    if venue:
        y = centered(d, width, y, venue, 32, DARK, inner)
    tv = tv_line(game)
    if tv:
        y = centered(d, width, y + 2, f"TV: {tv}", 28, MID, inner)

    # up next list
    if rest:
        y += 10
        d.line((pad, y, width - pad, y), fill=MID, width=2)
        y += 12
        d.text((pad, y), "UP NEXT", font=load_font(24), fill=MID, anchor="lt")
        y += 36
        small = load_font(32)
        for g in rest:
            g_local = start_utc(g).astimezone(LOCAL_TZ)
            g_prefix, g_opp = side_and_opponent(g)
            d.text((pad, y), fmt_short_date(g_local), font=small, fill=BLACK, anchor="lt")
            d.text((330, y), fmt_time(g_local), font=small, fill=DARK, anchor="lt")
            d.text(
                (width - pad, y)
                , f"{g_prefix} {g_opp['abbrev']}"
                , font=small
                , fill=BLACK
                , anchor="rt"
            )
            y += 40

    d.text((width / 2, height - 18), footer, font=load_font(20), fill=MID, anchor="mm")
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="mammoth.png")
    ap.add_argument("--json", help="read schedule from a local file instead of the NHL API")
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
    except Exception as exc:  # leave any existing image untouched on failure
        print(f"Could not load schedule: {exc}", file=sys.stderr)
        return 1

    upcoming = upcoming_games(games, now)
    img = render(upcoming, now, (width, height))
    img.save(args.out, format="PNG")
    nxt = upcoming[0]["startTimeUTC"] if upcoming else "none"
    print(f"Wrote {args.out} ({width}x{height}); next game start (UTC): {nxt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
