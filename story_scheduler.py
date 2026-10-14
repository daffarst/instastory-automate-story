import argparse
import json
import mimetypes
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from threading import Thread


def api_post(url, data):
    request = Request(url, data=urlencode(data).encode(), method="POST")
    return api_request(request)


def api_get(url, params):
    request = Request(f"{url}?{urlencode(params)}")
    return api_request(request)


def api_request(request):
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Instagram API request failed: {error}") from error

    if "error" in result:
        error = result["error"]
        raise RuntimeError(
            f"Instagram API error {error.get('code', '')}: "
            f"{error.get('message', 'Unknown error')}"
        )
    return result


def read_queue(path):
    urls = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not urls:
        raise ValueError(f"No video URLs found in {path}")
    for url in urls:
        if not url.startswith("https://"):
            raise ValueError(f"Video URL must use HTTPS: {url}")
    return urls


def read_local_videos(folder):
    if not folder.is_dir():
        raise ValueError(f"Video folder does not exist: {folder}")
    videos = sorted(
        (
            path
            for path in folder.iterdir()
            if not path.is_symlink()
            and path.is_file()
            and path.suffix.lower() in {".mp4", ".mov"}
        ),
        key=lambda path: path.name.casefold(),
    )
    if not videos:
        raise ValueError(f"No .mp4 or .mov videos found in {folder}")
    return videos


class TemporaryMediaServer:
    def __init__(self, media_path, public_base_url, port):
        parsed_url = urlsplit(public_base_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username
            or parsed_url.password
            or parsed_url.path not in {"", "/"}
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("IG_PUBLIC_MEDIA_BASE_URL must be a public HTTPS base URL")

        self.token = secrets.token_urlsafe(32)
        self.media_path = media_path
        self.public_url = f"{public_base_url.rstrip('/')}/{self.token}"

        class MediaHandler(BaseHTTPRequestHandler):
            def do_HEAD(handler_self):
                handler_self.respond(send_body=False)

            def do_GET(handler_self):
                handler_self.respond(send_body=True)

            def respond(handler_self, send_body):
                if handler_self.path != f"/{self.token}":
                    handler_self.send_error(404)
                    return
                try:
                    file_size = self.media_path.stat().st_size
                    handler_self.send_response(200)
                    handler_self.send_header(
                        "Content-Type",
                        mimetypes.guess_type(self.media_path.name)[0]
                        or "application/octet-stream",
                    )
                    handler_self.send_header("Content-Length", str(file_size))
                    handler_self.send_header("Cache-Control", "no-store")
                    handler_self.end_headers()
                    if send_body:
                        with self.media_path.open("rb") as video:
                            while chunk := video.read(64 * 1024):
                                handler_self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(handler_self, format_string, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", port), MediaHandler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exception_type, exception, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def publish_story(video_url, user_id, access_token, api_version):
    base_url = f"https://graph.instagram.com/{api_version}/{user_id}"
    container = api_post(
        f"{base_url}/media",
        {
            "media_type": "STORIES",
            "video_url": video_url,
            "access_token": access_token,
        },
    )
    container_id = container["id"]

    for _ in range(60):
        status = api_get(
            f"https://graph.instagram.com/{api_version}/{container_id}",
            {"fields": "status_code", "access_token": access_token},
        )
        status_code = status.get("status_code")
        if status_code == "FINISHED":
            break
        if status_code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Instagram could not process the video: {status}")
        time.sleep(10)
    else:
        raise TimeoutError("Timed out waiting for Instagram to process the video")

    result = api_post(
        f"{base_url}/media_publish",
        {"creation_id": container_id, "access_token": access_token},
    )
    return result["id"]


def main():
    parser = argparse.ArgumentParser(
        description="Publish the next queued or local video as an Instagram Story."
    )
    parser.add_argument("--folder", type=Path, default=Path("videos"))
    parser.add_argument("--queue", type=Path, help="Use a queue of public HTTPS video URLs")
    parser.add_argument("--state", type=Path, default=Path(".story_scheduler_state.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        videos = read_queue(args.queue) if args.queue else read_local_videos(args.folder)
        today = time.strftime("%Y-%m-%d")
        state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {}
        if state.get("last_published_date") == today:
            print(f"A Story has already been published today ({today}).")
            return 0

        index = int(state.get("next_video_index", 0)) % len(videos)
        video = videos[index]
        if args.dry_run:
            print(f"Would publish video {index + 1}/{len(videos)}: {video}")
            return 0

        user_id = os.environ["IG_USER_ID"]
        access_token = os.environ["IG_ACCESS_TOKEN"]
        api_version = os.environ["IG_GRAPH_API_VERSION"]
        if args.queue:
            story_id = publish_story(video, user_id, access_token, api_version)
        else:
            public_base_url = os.environ["IG_PUBLIC_MEDIA_BASE_URL"]
            port = int(os.environ.get("IG_MEDIA_SERVER_PORT", "8787"))
            with TemporaryMediaServer(video, public_base_url, port) as media_server:
                story_id = publish_story(
                    media_server.public_url, user_id, access_token, api_version
                )

        args.state.parent.mkdir(parents=True, exist_ok=True)
        temporary_state = args.state.with_name(args.state.name + ".tmp")
        temporary_state.write_text(
            json.dumps(
                {
                    "last_published_date": today,
                    "next_video_index": (index + 1) % len(videos),
                }
            ),
            encoding="utf-8",
        )
        temporary_state.replace(args.state)
        print(f"Published Instagram Story {story_id} from video {index + 1}.")
        return 0
    except (
        KeyError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
