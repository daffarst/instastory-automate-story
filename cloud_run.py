import argparse
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from story_scheduler import TemporaryMediaServer, publish_story, read_local_videos

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_ATTEMPTS_PER_DAY = 3


def load_json(path, default):
    if not path.exists():
        return dict(default)
    return {**default, **json.loads(path.read_text(encoding="utf-8"))}


def save_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_schedule(path):
    schedule = load_json(
        path, {"enabled": False, "time": "09:00", "timezone": "Asia/Jakarta"}
    )
    if not TIME_PATTERN.match(str(schedule["time"])):
        raise ValueError(f"Invalid time in {path}: use HH:MM (24-hour)")
    try:
        zone = ZoneInfo(schedule["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Unknown timezone in {path}: {schedule['timezone']}") from error
    return schedule, zone


def is_due(schedule, state, now, force):
    today = now.date().isoformat()
    if state["last_published_date"] == today:
        return False, f"A Story was already published today ({today})."
    if force:
        return True, "Forced run."
    if not schedule["enabled"]:
        return False, "Scheduling is disabled in the schedule file."
    if now.strftime("%H:%M") < schedule["time"]:
        return False, f"Not due yet (scheduled for {schedule['time']}, now {now:%H:%M})."
    if state["attempts_date"] == today and state["attempts"] >= MAX_ATTEMPTS_PER_DAY:
        return False, f"Gave up for today after {MAX_ATTEMPTS_PER_DAY} failed attempts."
    return True, "Due."


def pick_video(videos, used):
    candidates = [video for video in videos if video.name not in used]
    cycle_reset = not candidates
    return random.choice(videos if cycle_reset else candidates), cycle_reset


def set_output(name, value):
    line = f"{name}={value}"
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as output:
            output.write(line + "\n")
    print(line)


def main():
    parser = argparse.ArgumentParser(description="Cloud (GitHub Actions) Story publisher.")
    parser.add_argument("--schedule", type=Path, default=Path("schedule.json"))
    parser.add_argument("--state", type=Path, default=Path("cloud_state.json"))
    parser.add_argument("--folder", type=Path, default=Path("videos"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Only report whether a Story is due")
    mode.add_argument("--dry-run", action="store_true", help="Pick a video without publishing")
    parser.add_argument("--force", action="store_true", help="Ignore the scheduled time")
    args = parser.parse_args()

    try:
        schedule, zone = read_schedule(args.schedule)
        state = load_json(
            args.state,
            {"last_published_date": None, "used": [], "attempts_date": None, "attempts": 0},
        )
        now = datetime.now(zone)
        today = now.date().isoformat()

        if args.dry_run:
            videos = read_local_videos(args.folder)
            video, _ = pick_video(videos, set(state["used"]))
            print(f"Dry run: would publish {video.name} ({len(videos)} videos in folder).")
            return 0

        due, reason = is_due(schedule, state, now, args.force)
        print(reason)
        if args.check:
            set_output("due", "true" if due else "false")
            return 0
        if not due:
            return 0

        videos = read_local_videos(args.folder)
        video, cycle_reset = pick_video(videos, set(state["used"]))

        if state["attempts_date"] != today:
            state["attempts_date"], state["attempts"] = today, 0
        state["attempts"] += 1
        save_json(args.state, state)

        with TemporaryMediaServer(
            video,
            os.environ["IG_PUBLIC_MEDIA_BASE_URL"],
            int(os.environ.get("IG_MEDIA_SERVER_PORT", "8787")),
        ) as media_server:
            story_id = publish_story(
                media_server.public_url,
                os.environ["IG_USER_ID"],
                os.environ["IG_ACCESS_TOKEN"],
                os.environ["IG_GRAPH_API_VERSION"],
            )

        state["used"] = ([] if cycle_reset else state["used"]) + [video.name]
        state["last_published_date"] = today
        save_json(args.state, state)
        print(f"Published Instagram Story {story_id} from {video.name}.")
        return 0
    except KeyError as error:
        print(f"Error: environment variable {error.args[0]} is not set.", file=sys.stderr)
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
