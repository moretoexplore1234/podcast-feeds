#!/usr/bin/env python3
"""Osint613 Daily Briefings podcast feed.

Scans ~/workspace/osint613-voice-note/runs/ for new briefing MP3s, uploads
them to catbox.moe, appends them as episodes to feed.xml, and pushes to the
podcast-feeds GitHub repo (public URL:
https://moretoexplore1234.github.io/podcast-feeds/osint613-briefings/feed.xml).

Idempotent: episodes are keyed by run-dir + MP3 name in state.json; re-running
only adds MP3s that are not already recorded.

Usage:
  add_episode.py          # pick up every new MP3 (cron calls this after synthesis)
"""
import datetime
import glob
import html
import json
import os
import re
import subprocess
import time
from zoneinfo import ZoneInfo

# ---------------- show config ----------------
SHOW_TITLE = "Osint613 Daily Briefings"
SHOW_DESC = ("Breaking news, read to you twice a day. Every @Osint613 post — "
             "every photo and video described — narrated like a human news anchor. "
             "Morning briefings cover the overnight hours, 9 PM–7 AM Eastern; "
             "evening briefings cover the day, 7 AM–9 PM Eastern. Never miss a story.")
HANDLE = "@Osint613"
RUNS_DIR = os.path.expanduser("~/workspace/osint613-voice-note/runs")
FEED_DIR = os.path.expanduser("~/workspace/podcast-feeds/osint613-briefings")
REPO_DIR = os.path.expanduser("~/workspace/podcast-feeds")
FEED_FILE = "feed.xml"
SITE_PATH = "osint613-briefings"
GUID_PREFIX = "osint613"
ET = ZoneInfo("America/New_York")
SLOT_TIME = {"am": (7, 0), "pm": (21, 0)}          # approximate run time, ET
SLOT_LABEL = {"am": "Morning briefing", "pm": "Evening briefing"}
# ---------------------------------------------

STATE_PATH = os.path.join(FEED_DIR, "state.json")
ART_PATH = os.path.join(FEED_DIR, "artwork.jpg")


def esc(s):
    return html.escape(s or "", quote=True)


def rfc2822(ts):
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S GMT")


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"episodes": {}, "artwork": None}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_PATH)


def catbox_upload(path, filename, tries=5):
    last = ""
    for i in range(tries):
        cmd = ["curl", "-sS", "--http1.1", "--retry", "2", "--retry-all-errors",
               "-m", "900", "-F", "reqtype=fileupload",
               "-F", f"fileToUpload=@{path};filename={filename}",
               "https://catbox.moe/user/api.php"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=960)
        url = (r.stdout or "").strip()
        if url.startswith("https://files.catbox.moe/"):
            return url
        last = (r.stdout + r.stderr)[-200:]
        print(f"  upload attempt {i + 1} failed: {last}", flush=True)
        time.sleep(10)
    raise RuntimeError(f"catbox upload failed after {tries} tries: {last}")


def ffprobe_duration(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of",
                            "default=noprint_wrappers=1:nokey=1", path],
                           capture_output=True, text=True, timeout=60)
        return int(float((r.stdout or "0").strip() or 0))
    except Exception:
        return 0


def dur_str(secs):
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ensure_artwork(state):
    if state.get("artwork"):
        return state["artwork"]
    if not os.path.exists(ART_PATH):
        print("no artwork.jpg found, skipping artwork", flush=True)
        return None
    url = catbox_upload(ART_PATH, "osint613-briefings-artwork.jpg")
    state["artwork"] = url
    save_state(state)
    print(f"artwork -> {url}", flush=True)
    return url


def scan_new_mp3s(state):
    """Yield (key, mp3_path, run_date, slot, addendum) for MP3s not in state."""
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)$")
    for mp3 in sorted(glob.glob(os.path.join(RUNS_DIR, "*", "*.mp3"))):
        if os.path.getsize(mp3) == 0:
            print(f"skip zero-byte: {mp3}", flush=True)
            continue
        rundir = os.path.basename(os.path.dirname(mp3))
        m = pattern.match(rundir)
        if not m:
            print(f"skip (unrecognized run dir): {mp3}", flush=True)
            continue
        date_s, slot = m.groups()
        stem = os.path.splitext(os.path.basename(mp3))[0]
        key = f"{rundir}_{stem}"
        if key in state["episodes"]:
            continue
        addendum = "addendum" in stem
        yield key, mp3, date_s, slot, addendum


def add_episode(state, key, mp3, date_s, slot, addendum):
    print(f"NEW: {mp3}", flush=True)
    hour, minute = SLOT_TIME[slot]
    local = datetime.datetime.strptime(date_s, "%Y-%m-%d").replace(
        hour=hour, minute=minute, tzinfo=ET)
    ts = int(local.timestamp())
    pretty = local.strftime("%B %-d, %Y")
    label = SLOT_LABEL[slot] + (" (addendum)" if addendum else "")
    title = f"{label} — {pretty}"
    url = catbox_upload(mp3, f"osint613-{date_s}-{slot}.mp3")
    size = os.path.getsize(mp3)
    duration = ffprobe_duration(mp3)
    state["episodes"][key] = {
        "title": title,
        "description": (f"{label} of every {HANDLE} post ({'9 PM to 7 AM' if slot == 'am' else '7 AM to 9 PM'} "
                        f"Eastern, {pretty}), narrated with photos and videos described."),
        "published": ts,
        "duration": duration,
        "url": url,
        "size": size,
        "guid": f"{GUID_PREFIX}:{key}",
    }
    save_state(state)
    print(f"  -> {url} ({dur_str(duration)})", flush=True)
    return "added"


def build_feed(state):
    eps = state["episodes"]
    items = sorted(eps.values(), key=lambda e: int(e["published"]), reverse=True)
    art = state.get("artwork") or ""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
             "<channel>",
             f"<title>{esc(SHOW_TITLE)}</title>",
             "<link>https://x.com/Osint613</link>",
             f"<description>{esc(SHOW_DESC)}</description>",
             "<language>en</language>",
             "<itunes:author>Carl Meiselman</itunes:author>",
             "<managingEditor>carlmeiselman@gmail.com</managingEditor>",
             "<itunes:owner><itunes:name>Carl Meiselman</itunes:name><itunes:email>carlmeiselman@gmail.com</itunes:email></itunes:owner>",
             "<itunes:category text=\"News\"/>",
             "<explicit>no</explicit>",
             f"<itunes:image href=\"{esc(art)}\"/>" if art else "",
             (f"<image><url>{esc(art)}</url><title>{esc(SHOW_TITLE)}</title>"
              f"<link>https://x.com/Osint613</link></image>") if art else ""]
    for e in items:
        parts.append("<item>")
        parts.append(f"<title>{esc(e['title'])}</title>")
        parts.append(f"<description>{esc(e['description'])}</description>")
        parts.append(f"<itunes:summary>{esc(e['description'])}</itunes:summary>")
        parts.append(f"<pubDate>{rfc2822(e['published'])}</pubDate>")
        parts.append(f"<guid isPermaLink=\"false\">{esc(e['guid'])}</guid>")
        parts.append(f"<enclosure url=\"{esc(e['url'])}\" length=\"{e['size']}\" type=\"audio/mpeg\"/>")
        parts.append(f"<itunes:duration>{dur_str(e['duration'])}</itunes:duration>")
        parts.append(f"<link>https://x.com/{HANDLE.lstrip('@')}</link>")
        parts.append("</item>")
    parts += ["</channel>", "</rss>"]
    out = os.path.join(REPO_DIR, SITE_PATH, FEED_FILE)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write("\n".join(p for p in parts if p != "") + "\n")
    print(f"feed.xml written: {len(items)} episodes", flush=True)


def git_push():
    r = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR,
                       capture_output=True, text=True, timeout=60)
    if not r.stdout.strip():
        print("nothing to commit", flush=True)
        return
    n = len([l for l in r.stdout.splitlines() if l.strip()])
    subprocess.run(["git", "add", SITE_PATH], cwd=REPO_DIR, check=True, timeout=60)
    subprocess.run(["git", "commit", "-m",
                    f"Osint613 briefings: feed update ({n} changed)"],
                   cwd=REPO_DIR, check=True, timeout=60)
    subprocess.run(["git", "push"], cwd=REPO_DIR, check=True, timeout=300)
    print("pushed to GitHub", flush=True)


def main():
    os.makedirs(FEED_DIR, exist_ok=True)
    state = load_state()
    added = 0
    for key, mp3, date_s, slot, addendum in scan_new_mp3s(state):
        try:
            if add_episode(state, key, mp3, date_s, slot, addendum) == "added":
                added += 1
        except Exception as e:
            print(f"  FAILED {mp3}: {e}", flush=True)
    ensure_artwork(state)
    build_feed(state)
    git_push()
    print(f"done: {added} new episodes, {len(state['episodes'])} total", flush=True)


if __name__ == "__main__":
    main()
