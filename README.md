# OGFlix TV Automated Music Video Posting

This package searches recent YouTube Rap, Hip-Hop, and R&B music-video uploads
from approved channels, generates original cinematic cover art, uploads the art
to Cloudinary, builds your OGFlix TV Blogger HTML, and emails it to
`1063oglive.posts@blogger.com`.

## What it does

1. Searches recent music-video uploads through YouTube Data API v3.
2. Filters results to channel IDs you approve in `.env`.
3. Skips video IDs already recorded in a local SQLite database.
4. Generates a vertical 2:3 original movie-poster-style image with OpenAI.
5. Uploads the cover to Cloudinary and creates poster/backdrop image URLs.
6. Builds the OGFlix TV HTML with the YouTube embed ID and Watch Now button.
7. Emails the HTML and attaches the generated cover to Blogger's email-to-post address.

## Important limitations

- YouTube search cannot guarantee every new release will be found.
- The approved channel list must be populated with official artist/label channel IDs.
- Artist/title parsing is best-effort; inspect Blogger drafts before enabling auto-publishing.
- This uses Blogger email-to-post, not the Blogger API. Test how your blog parses
  the HTML and attachment before switching from drafts to immediate publishing.
- API usage and image hosting may incur charges or quotas.
- The artwork is original concept art and does not attempt to reproduce the
  real artist's likeness.
- The image model defaults to `gpt-image-1`; set `OPENAI_IMAGE_MODEL` to a model
  currently enabled for your API account if needed.
- Keep `.env` private. The Blogger email-to-post address is sensitive.

## Requirements

- Python 3.10+
- YouTube Data API v3 key
- OpenAI API key with image-generation access
- Cloudinary account
- Gmail SMTP with a Google App Password (or another compatible SMTP provider)
- Blogger email-to-post configured with the destination address

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, activate with:

```powershell
.venv\\Scripts\\Activate.ps1
```

Edit `.env` and enter your credentials and approved channel IDs.

## Find official channel IDs

Use the official artist or record-label YouTube channel. Obtain its channel ID
(usually starts with `UC`) from the channel details or channel URL. Do not use
a video ID, handle, or display name. Add IDs separated by commas.

## Configure Blogger

In Blogger Settings, configure email-to-post and initially choose the option
to save incoming emails as drafts. Verify that the secret posting address in
your Blogger account is exactly `1063oglive.posts@blogger.com` before running.
The address in this package is the destination you requested; it is not proof
that this address is already configured on your account.

## Test

```bash
python ogflix_automated_music_videos.py
```

Check terminal output, Blogger drafts, cover images, and the Watch Now button.
Confirm your theme recognizes the HTML and the video ID is correct. Only after
testing should you switch Blogger email-to-post to publish immediately.

## Run daily on macOS/Linux

Run `crontab -e` and add this example (replace paths with your actual folder):

```cron
0 9 * * * cd /absolute/path/ogflix_music_video_automation && /absolute/path/ogflix_music_video_automation/.venv/bin/python ogflix_automated_music_videos.py >> automation.log 2>&1
```

The computer must be awake and online at run time. For always-on operation,
deploy to a suitable server or scheduled cloud job.

## Duplicate prevention

The script creates `ogflix_posted_videos.db` and records a video ID only after
the SMTP server accepts the email. Keep this database file between runs.

## Package contents

- `ogflix_automated_music_videos.py` — main script
- `.env.example` — configuration template
- `requirements.txt` — dependencies
- `README.md` — setup and operating instructions
