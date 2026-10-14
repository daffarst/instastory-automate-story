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

## Reposting story mentions

The official Instagram API does not provide an endpoint to retrieve another
person's mentioned Story and repost it automatically, including as a full-size
copy. This program therefore cannot auto-repost Story mentions. Reposting needs
to be done manually in Instagram, with the original creator's permission.
Publishing a video from your own folder as a new Story is supported separately.

The API can reject unsupported video formats, inaccessible URLs, expired tokens,
or accounts without the required access. Check Meta's current Instagram
publishing documentation for supported media requirements and permissions.
