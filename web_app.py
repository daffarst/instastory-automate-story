import argparse
import hmac
import html
import json
import os
import random
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from story_scheduler import TemporaryMediaServer, publish_story, read_local_videos

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_ATTEMPTS_PER_DAY = 3
RETRY_DELAY_SECONDS = 15 * 60
MAX_HISTORY = 30


class Scheduler:
    def __init__(self, folder, state_path):
        self.folder = folder
        self.state_path = state_path
        self.state_lock = threading.Lock()
        self.publish_lock = threading.Lock()
        self.running = False
        self.attempts_date = None
        self.attempts = 0
        self.next_retry = 0.0
        self.data = {
            "time": "09:00",
            "enabled": False,
            "last_published_date": None,
            "used": [],
            "history": [],
        }
        if state_path.exists():
            try:
                self.data.update(json.loads(state_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def log(self, status, message):
        with self.state_lock:
            self.data["history"].insert(
                0,
                {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "status": status, "message": message},
            )
            del self.data["history"][MAX_HISTORY:]
            self.save()

    def update_settings(self, schedule_time, enabled):
        with self.state_lock:
            self.data["time"] = schedule_time
            self.data["enabled"] = enabled
            self.save()
        self.log("info", f"Pengaturan disimpan: jam {schedule_time}, "
                         f"{'aktif' if enabled else 'nonaktif'}.")

    def snapshot(self):
        with self.state_lock:
            return json.loads(json.dumps(self.data)), self.running

    def pick_video(self, videos):
        with self.state_lock:
            used = set(self.data["used"])
        candidates = [video for video in videos if video.name not in used]
        if not candidates:
            candidates = videos
        return random.choice(candidates), len(candidates) == len(videos)

    def publish(self, dry_run=False):
        if not self.publish_lock.acquire(blocking=False):
            return False
        self.running = True
        try:
            today = time.strftime("%Y-%m-%d")
            if not dry_run and self.data["last_published_date"] == today:
                self.log("info", "Story hari ini sudah diupload, dilewati.")
                return False
            try:
                videos = read_local_videos(self.folder)
                video, cycle_reset = self.pick_video(videos)
                if dry_run:
                    self.log("info", f"Tes: video yang akan diupload adalah {video.name} "
                                     "(tidak ada yang diupload).")
                    return True
                user_id = os.environ["IG_USER_ID"]
                access_token = os.environ["IG_ACCESS_TOKEN"]
                api_version = os.environ["IG_GRAPH_API_VERSION"]
                public_base_url = os.environ["IG_PUBLIC_MEDIA_BASE_URL"]
                port = int(os.environ.get("IG_MEDIA_SERVER_PORT", "8787"))
                with TemporaryMediaServer(video, public_base_url, port) as media_server:
                    story_id = publish_story(
                        media_server.public_url, user_id, access_token, api_version
                    )
            except KeyError as error:
                self.log("error", f"Environment variable {error.args[0]} belum diset.")
                return False
            except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                self.log("error", f"Gagal upload: {error}")
                return False

            with self.state_lock:
                if cycle_reset:
                    self.data["used"] = []
                self.data["used"].append(video.name)
                self.data["last_published_date"] = today
                self.save()
            self.log("success", f"Story {story_id} berhasil diupload dari {video.name}.")
            return True
        finally:
            self.running = False
            self.publish_lock.release()

    def tick(self):
        data, running = self.snapshot()
        today = time.strftime("%Y-%m-%d")
        if running or not data["enabled"] or data["last_published_date"] == today:
            return
        if time.strftime("%H:%M") < data["time"]:
            return
        if self.attempts_date != today:
            self.attempts_date, self.attempts = today, 0
        if self.attempts >= MAX_ATTEMPTS_PER_DAY or time.time() < self.next_retry:
            return
        self.attempts += 1
        if not self.publish():
            self.next_retry = time.time() + RETRY_DELAY_SECONDS

    def run_forever(self):
        while True:
            try:
                self.tick()
            except Exception as error:  # keep the scheduler alive
                self.log("error", f"Kesalahan scheduler: {error}")
            time.sleep(20)

    def start_background(self, dry_run):
        threading.Thread(target=self.publish, args=(dry_run,), daemon=True).start()


def render_page(scheduler, csrf_token):
    data, running = scheduler.snapshot()
    try:
        video_count = len(read_local_videos(scheduler.folder))
        folder_note = f"{video_count} video ditemukan"
    except ValueError as error:
        folder_note = str(error)
    missing = [
        name
        for name in (
            "IG_USER_ID",
            "IG_ACCESS_TOKEN",
            "IG_GRAPH_API_VERSION",
            "IG_PUBLIC_MEDIA_BASE_URL",
        )
        if not os.environ.get(name)
    ]
    credentials = (
        "Lengkap"
        if not missing
        else "Belum diset: " + ", ".join(missing)
    )
    today = time.strftime("%Y-%m-%d")
    if data["last_published_date"] == today:
        today_status = "Sudah diupload hari ini"
    elif data["enabled"]:
        today_status = f"Menunggu jam {data['time']}"
    else:
        today_status = "Penjadwalan nonaktif"

    rows = "".join(
        f"<tr class='{html.escape(item['status'])}'><td>{html.escape(item['at'])}</td>"
        f"<td>{html.escape(item['message'])}</td></tr>"
        for item in data["history"]
    ) or "<tr><td colspan='2'>Belum ada aktivitas.</td></tr>"
    token = html.escape(csrf_token)
    checked = "checked" if data["enabled"] else ""
    disabled = "disabled" if running else ""
    return f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>Penjadwal Instagram Story</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
section {{ border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }}
h1 {{ font-size: 1.5rem; }} h2 {{ font-size: 1.1rem; margin-top: 0; }}
label {{ display: block; margin: .6rem 0; }}
button {{ padding: .5rem 1rem; margin-right: .5rem; cursor: pointer; }}
table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
td {{ border-top: 1px solid #eee; padding: .35rem; vertical-align: top; }}
tr.error td {{ color: #b00020; }} tr.success td {{ color: #0a7a2f; }}
dl {{ display: grid; grid-template-columns: 11rem 1fr; gap: .3rem; margin: 0; }} dd {{ margin: 0; }}
</style></head><body>
<h1>Penjadwal Instagram Story</h1>
<section><h2>Status</h2><dl>
<dt>Hari ini</dt><dd>{html.escape(today_status)}</dd>
<dt>Jam upload</dt><dd>{html.escape(data['time'])} (zona waktu komputer ini)</dd>
<dt>Folder video</dt><dd>{html.escape(str(scheduler.folder))} &mdash; {html.escape(folder_note)}</dd>
<dt>Kredensial API</dt><dd>{html.escape(credentials)}</dd>
<dt>Proses upload</dt><dd>{'Sedang berjalan...' if running else 'Idle'}</dd>
</dl></section>
<section><h2>Pengaturan</h2>
<form method="post" action="/save">
<input type="hidden" name="csrf" value="{token}">
<label>Jam upload harian
<input type="time" name="time" value="{html.escape(data['time'])}" required></label>
<label><input type="checkbox" name="enabled" value="1" {checked}> Aktifkan upload otomatis (1 video acak per hari)</label>
<button type="submit">Simpan</button>
</form></section>
<section><h2>Aksi manual</h2>
<form method="post" action="/test" style="display:inline">
<input type="hidden" name="csrf" value="{token}">
<button type="submit" {disabled}>Tes pilih video (tanpa upload)</button></form>
<form method="post" action="/run-now" style="display:inline"
 onsubmit="return confirm('Upload 1 story sekarang? Ini dihitung sebagai story hari ini.')">
<input type="hidden" name="csrf" value="{token}">
<button type="submit" {disabled}>Upload sekarang</button></form>
</section>
<section><h2>Riwayat</h2><table>{rows}</table></section>
</body></html>"""


def make_handler(scheduler, csrf_token):
    class Handler(BaseHTTPRequestHandler):
        def host_allowed(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in {"127.0.0.1", "localhost"}

        def send_text(self, status, body, content_type="text/html; charset=utf-8"):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(payload)

        def redirect_home(self):
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self.host_allowed():
                self.send_error(403)
            elif self.path != "/":
                self.send_error(404)
            else:
                self.send_text(200, render_page(scheduler, csrf_token))

        def do_POST(self):
            if not self.host_allowed():
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 <= length <= 4096:
                self.send_error(400)
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            sent_token = form.get("csrf", [""])[0]
            if not hmac.compare_digest(sent_token, csrf_token):
                self.send_error(403, "Invalid form token")
                return

            if self.path == "/save":
                schedule_time = form.get("time", [""])[0]
                if not TIME_PATTERN.match(schedule_time):
                    self.send_error(400, "Format jam harus HH:MM")
                    return
                scheduler.update_settings(schedule_time, "enabled" in form)
            elif self.path in {"/test", "/run-now"}:
                scheduler.start_background(dry_run=self.path == "/test")
            else:
                self.send_error(404)
                return
            self.redirect_home()

        def log_message(self, format_string, *args):
            return

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Web UI untuk penjadwal Instagram Story.")
    parser.add_argument("--folder", type=Path, default=Path("videos"))
    parser.add_argument("--state", type=Path, default=Path(".web_scheduler_state.json"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    args.folder.mkdir(parents=True, exist_ok=True)
    scheduler = Scheduler(args.folder, args.state)
    threading.Thread(target=scheduler.run_forever, daemon=True).start()

    csrf_token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(scheduler, csrf_token))
    print(f"Buka http://127.0.0.1:{args.port} di browser. Tekan Ctrl+C untuk berhenti.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
