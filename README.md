# Instagram Story Scheduler

This Python program publishes one video as an Instagram Story each time it runs.
By default it selects videos from a local folder. It uses Meta's official
Instagram API; it does not automate the Instagram app or ask for your Instagram
password.

## Requirements

- An Instagram **Business or Creator** account with access to Meta's content
  publishing API and the required permissions.
- An HTTPS tunnel from a public hostname to local port `8787` (for example, a
  configured Cloudflare Tunnel). Instagram's API fetches video from a public
  URL, so it cannot access a local file or `localhost` directly.
- Python 3.9 or newer.

## Setup

1. Put `.mp4` or `.mov` files in the `videos/` folder. The program sorts by
   filename, moves through them in order, and repeats after the last video.
2. Configure a persistent public HTTPS tunnel to forward requests to
   `http://127.0.0.1:8787`. Set its HTTPS origin and the API credentials in the
   environment where the scheduled job will run:

   ```sh
   export IG_USER_ID="your-instagram-professional-account-id"
   export IG_ACCESS_TOKEN="your-meta-access-token"
   export IG_GRAPH_API_VERSION="vXX.0"
   export IG_PUBLIC_MEDIA_BASE_URL="https://your-public-tunnel.example"
   ```

   Replace `vXX.0` with a currently supported version. The tunnel must be
   running when the scheduler runs and must not require authentication for
   Instagram to fetch the video. Keep the access token private and do not commit
   it to the repository.
3. Check the queue without publishing:

   ```sh
   python3 story_scheduler.py --dry-run
   ```

4. Run once to publish:

   ```sh
   python3 story_scheduler.py
   ```

The scheduler serves only the selected file through a random, hard-to-guess URL
while Instagram processes and publishes it. The local server binds to loopback,
rejects all other paths, and shuts down after the API operation. The selected
file is nevertheless publicly reachable during that time through your tunnel;
only put media there that you are comfortable publishing.

To use the older list of already-hosted videos instead, run
`python3 story_scheduler.py --queue videos.txt`.

The `.story_scheduler_state.json` file records the last successful publication
date and which URL to use next. Keep this file between runs. The date uses the
machine's local timezone; run the job at the time you want the Story to appear.

## Daily scheduling

Use cron, Task Scheduler, or another operating-system scheduler to run
`python3 /absolute/path/to/story_scheduler.py` once per day. Configure the three
API environment variables and `IG_PUBLIC_MEDIA_BASE_URL` in that scheduled job
as well; a cron job may not inherit variables from an interactive terminal.
Keep the tunnel active. The script will skip a second run on the same local
calendar day.

## Web UI (set the upload time in a browser)

`web_app.py` runs a small local web page and a built-in scheduler. Each day, at
the time you choose, it picks **one random video** from the `videos/` folder and
publishes it as a Story. Videos are not repeated until every video in the folder
has been used once.

1. Put videos in `videos/` and set the four `IG_*` environment variables
   described above, then start the tunnel.
2. Start the app and keep it running:

   ```sh
   python web_app.py
   ```

   Options: `--folder videos`, `--port 8000`, `--state .web_scheduler_state.json`.
3. Open <http://127.0.0.1:8000>, choose the upload time, tick "Aktifkan", and
   press **Simpan**.

The page also has **Tes pilih video** (shows which video would be chosen,
without uploading) and **Upload sekarang** (publishes immediately and counts as
today's Story). Only one Story is published per local calendar day. If the app
starts after the scheduled time and today's Story has not been published, it
publishes right away; a failed upload is retried every 15 minutes, up to 3 times
per day. The page listens on `127.0.0.1` only and the API token is read from
environment variables, never stored by the web page. The computer and app must
be running at the scheduled time.

## Run in the cloud (computer can be off)

The workflow [daily-story.yml](.github/workflows/daily-story.yml) runs on GitHub's
servers, so your computer and `web_app.py` do not need to be on. Every 15 minutes
it checks [schedule.json](schedule.json); once the scheduled time has passed and
no Story has been published today, it opens a temporary Cloudflare tunnel,
publishes one random video from `videos/` (no repeats until all are used) and
commits `cloud_state.json` back to the repo.

Setup:

1. Commit your videos into `videos/` (each under 100 MB) and push. Use a
   **private** repository: anyone who can see the repo can see the videos.
2. In the repo go to *Settings → Secrets and variables → Actions* and add the
   secrets `IG_USER_ID`, `IG_ACCESS_TOKEN` and `IG_GRAPH_API_VERSION`.
   (`IG_PUBLIC_MEDIA_BASE_URL` is created automatically by the workflow.)
3. Edit `schedule.json` (you can do it on github.com) to change the time:

   ```json
   {"enabled": true, "time": "09:00", "timezone": "Asia/Jakarta"}
   ```

4. Test under *Actions → Daily Instagram Story → Run workflow* with `dry-run`
   (shows the chosen video, uploads nothing). `publish-now` uploads one Story
   immediately and counts as today's Story.

Notes:

- GitHub schedules are best effort: the Story can appear 5-30 minutes after the
  chosen time. A failed run is retried on the next 15-minute run, up to 3
  attempts per day, and failed runs show up as red runs in the Actions tab.
- Do not run the cloud workflow and `web_app.py` scheduling at the same time, or
  two Stories will be published per day.
- Instagram access tokens expire (long-lived tokens after about 60 days); update
  the `IG_ACCESS_TOKEN` secret when that happens.
- GitHub may pause scheduled workflows in repositories with no activity for 60
  days; re-enable it in the Actions tab if that happens.

## Reposting story mentions

The official Instagram API does not provide an endpoint to retrieve another
person's mentioned Story and repost it automatically, including as a full-size
copy. This program therefore cannot auto-repost Story mentions. Reposting needs
to be done manually in Instagram, with the original creator's permission.
Publishing a video from your own folder as a new Story is supported separately.

The API can reject unsupported video formats, inaccessible URLs, expired tokens,
or accounts without the required access. Check Meta's current Instagram
publishing documentation for supported media requirements and permissions.
