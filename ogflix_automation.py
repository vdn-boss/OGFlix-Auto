
import os
import re
import io
import json
import html
import base64
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
import cloudinary
import cloudinary.uploader
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SETTINGS_FILE = DATA / "settings.json"
CHANNELS_FILE = DATA / "approved_channels.json"
POSTED_FILE = DATA / "posted_videos.json"

YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")

BLOGGER_CLIENT_ID = os.getenv("BLOGGER_CLIENT_ID")
BLOGGER_CLIENT_SECRET = os.getenv("BLOGGER_CLIENT_SECRET")
BLOGGER_REFRESH_TOKEN = os.getenv("BLOGGER_REFRESH_TOKEN")
BLOGGER_BLOG_ID = os.getenv("BLOGGER_BLOG_ID")

IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")

for name, value in {
    "YOUTUBE_API_KEY": YOUTUBE_KEY,
    "OPENAI_API_KEY": OPENAI_KEY,
    "CLOUDINARY_URL": CLOUDINARY_URL,
    "BLOGGER_CLIENT_ID": BLOGGER_CLIENT_ID,
    "BLOGGER_CLIENT_SECRET": BLOGGER_CLIENT_SECRET,
    "BLOGGER_REFRESH_TOKEN": BLOGGER_REFRESH_TOKEN,
    "BLOGGER_BLOG_ID": BLOGGER_BLOG_ID,
}.items():
    if not value:
        raise RuntimeError(f"Missing GitHub secret: {name}")

client = OpenAI(api_key=OPENAI_KEY)
cloudinary.config(cloudinary_url=CLOUDINARY_URL)


def read_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_settings():
    return read_json(SETTINGS_FILE, {
        "blog_name": "OGFlix TV",
        "blog_url": "https://ogflixtv.blogspot.com/",
        "music_categories": ["Rap", "Hip-Hop", "R&B"],
        "post_mode": "draft",
        "videos_per_run": 10,
        "lookback_hours": 48
    })




def load_approved_channels():
    entries = read_json(CHANNELS_FILE, [])
    resolved = set()

    print(f"Reading {len(entries)} approved channel entries.")

    for entry in entries:
        value = str(entry).strip()

        if not value:
            continue

        if value.startswith("UC") and "/" not in value:
            resolved.add(value)
            print(f"Existing channel ID: {value}")
            continue

        match = re.search(r"youtube\.com/@([^/?]+)", value)
        handle = match.group(1) if match else value.lstrip("@")

        try:
            response = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={
                    "part": "id",
                    "forHandle": handle,
                    "key": YOUTUBE_KEY
                },
                timeout=30
            )
            response.raise_for_status()

            items = response.json().get("items", [])

            if items:
                channel_id = items[0]["id"]
                resolved.add(channel_id)
                print(f"APPROVED: @{handle} -> {channel_id}")
            else:
                print(f"NOT FOUND: @{handle}")

        except requests.RequestException as exc:
            print(f"YOUTUBE API ERROR for @{handle}: {exc}")

    print(f"Successfully resolved {len(resolved)} approved channels.")
    return resolved


    for entry in entries:
        value = str(entry).strip()

        if not value:
            continue

        if value.startswith("UC") and "/" not in value:
            resolved.add(value)
            continue

        match = re.search(r"youtube\.com/@([^/?]+)", value)
        handle = match.group(1) if match else value.lstrip("@")

        try:
            response = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={
                    "part": "id",
                    "forHandle": handle,
                    "key": YOUTUBE_KEY
                },
                timeout=30
            )
            response.raise_for_status()

            items = response.json().get("items", [])

            if items:
                channel_id = items[0]["id"]
                resolved.add(channel_id)
                print(f"APPROVED: @{handle} -> {channel_id}")
            else:
                print(f"WARNING: Channel not found: @{handle}")

        except requests.RequestException as exc:
            print(f"ERROR resolving @{handle}: {exc}")

    print(f"Successfully resolved {len(resolved)} approved channels.")
    return resolved


    for entry in entries:
        value = str(entry).strip()

        if not value:
            continue

        # Accept existing YouTube channel IDs.
        if value.startswith("UC") and "/" not in value:
            resolved.add(value)
            continue

        # Accept @handles and YouTube channel URLs.
        match = re.search(r"youtube\.com/@([^/?]+)", value)

        if match:
            handle = match.group(1)


def load_posted():
    return set(read_json(POSTED_FILE, []))


def save_posted(posted):
    DATA.mkdir(parents=True, exist_ok=True)
    with POSTED_FILE.open("w", encoding="utf-8") as file:
        json.dump(sorted(posted), file, indent=2)


def discover_videos(settings, approved, posted):
    if not approved:
        raise RuntimeError(
            "No approved YouTube channels found in data/approved_channels.json"
        )

    hours = int(settings.get("lookback_hours", 48))
    maximum = int(settings.get("videos_per_run", 10))

    published_after = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")

    queries = [
        "official rap music video",
        "official hip hop music video",
        "official R&B music video"
    ]

    found = {}

    for query in queries:
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "order": "date",
                "publishedAfter": published_after,
                "videoCategoryId": "10",
                "maxResults": 50,
                "key": YOUTUBE_KEY
            },
            timeout=30
        )
        response.raise_for_status()

        for item in response.json().get("items", []):
            video_id = item.get("id", {}).get("videoId")
            snippet = item.get("snippet", {})
            channel_id = snippet.get("channelId", "")
            title = snippet.get("title", "")

            if not video_id or channel_id not in approved:
                continue

            if video_id in posted:
                continue

            lowered = title.lower()

            if not any(term in lowered for term in (
                "official music video",
                "official video"
            )):
                continue

            if any(term in lowered for term in (
                "lyrics", "lyric video", "reaction",
                "live performance", "audio only",
                "visualizer", "fan made"
            )):
                continue

            found[video_id] = {
                "video_id": video_id,
                "title": title,
                "channel": snippet.get("channelTitle", ""),
                "channel_id": channel_id,
                "published": snippet.get("publishedAt", "")
            }

    videos = sorted(
        found.values(),
        key=lambda video: video["published"],
        reverse=True
    )

    return videos[:maximum]


def extract_metadata(video):
    title = video["title"]

    clean = re.sub(
        r"\s*[\(\[]\s*official.*?[\)\]]",
        "",
        title,
        flags=re.IGNORECASE
    )

    clean = re.sub(
        r"\s*(official music video|official video)\s*",
        "",
        clean,
        flags=re.IGNORECASE
    ).strip(" -|()[]")

    if " - " in clean:
        artist, song = clean.split(" - ", 1)
    elif " – " in clean:
        artist, song = clean.split(" – ", 1)
    else:
        artist = video["channel"]
        song = clean

    year = video["published"][:4] if video["published"] else ""

    return {
        "artist": artist.strip(),
        "song": song.strip(),
        "year": year
    }


def generate_cover(artist, song):
    prompt = f"""
Create original cinematic theatrical movie-poster artwork
for a music video.

Song: {song}
Artist: {artist}

Create a premium Hollywood-style movie poster in a vertical
2:3 composition. Use dramatic cinematic lighting, rich color
grading, atmospheric depth, detailed visual storytelling,
and a powerful central visual concept inspired by the song.

Make it look professionally commissioned and visually
astonishing. Do not imitate existing album or movie artwork.
Do not depict identifiable real artists or existing movie
characters. Do not add logos, watermarks, or text.
"""

    result = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size="1024x1536",
        quality="high",
        output_format="jpeg"
    )

    encoded = result.data[0].b64_json

    if not encoded:
        raise RuntimeError("Image generation returned no image data.")

    return base64.b64decode(encoded)


def upload_cover(image_bytes, artist, song, video_id):
    slug = re.sub(
        r"[^a-zA-Z0-9]+",
        "-",
        f"{artist}-{song}"
    ).strip("-").lower()[:90]

    public_id = f"{slug}-{video_id}"

    uploaded = cloudinary.uploader.upload(
        io.BytesIO(image_bytes),
        folder="ogflix/music-video-covers",
        public_id=public_id,
        overwrite=False,
        resource_type="image"
    )

    url = uploaded["secure_url"]

    poster = url.replace(
        "/upload/",
        "/upload/c_fill,w_600,h_900,q_auto,f_auto/"
    )

    backdrop = url.replace(
        "/upload/",
        "/upload/c_fill,w_1600,h_900,q_auto,f_auto/"
    )

    return poster, backdrop


def get_blogger_token():
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": BLOGGER_CLIENT_ID,
            "client_secret": BLOGGER_CLIENT_SECRET,
            "refresh_token": BLOGGER_REFRESH_TOKEN,
            "grant_type": "refresh_token"
        },
        timeout=30
    )

    response.raise_for_status()
    return response.json()["access_token"]


def build_blogger_html(video, metadata, poster, backdrop):
    artist = html.escape(metadata["artist"], quote=True)
    song = html.escape(metadata["song"], quote=True)
    year = html.escape(metadata["year"], quote=True)
    video_id = html.escape(video["video_id"], quote=True)

    youtube_embed = (
        f"https://www.youtube.com/embed/{video_id}"
    )

    return f'''<div id="post-body-raw">
<div class="separator" style="clear: both; text-align: center;">
<img src="{poster}" />
</div>

<div class="poster-url" style="display: none;">{poster}</div>
<div class="backdrop-url" style="display: none;">{backdrop}</div>
<div class="data-title">{song}</div>
<div class="data-meta">Artist: {artist} | <br />{year} • Official Music Video</div>
<div class="data-quality">Official Music Video</div>
<div class="data-lang">Original Audio</div>
<div class="data-backdrop">{backdrop}</div>
<div class="data-desc"></div>
<div class="data-video-id" style="display: none;">{video_id}</div>

<div class="data-season" data-title="{song}">
  <div class="movie-row">
    <button class="ep-play" onclick="playUrl('{youtube_embed}')">Watch Now</button>
  </div>
</div>
</div>'''


def publish_to_blogger(video, metadata, poster, backdrop, token, settings):
    blog_id = BLOGGER_BLOG_ID

    title = f"{metadata['song']} - {metadata['artist']}"
    content = build_blogger_html(
        video, metadata, poster, backdrop
    )

    post_mode = settings.get("post_mode", "draft").lower()
    is_draft = post_mode != "publish"

    url = (
        f"https://www.googleapis.com/blogger/v3/"
        f"blogs/{blog_id}/posts"
    )

    response = requests.post(
        url,
        params={"isDraft": str(is_draft).lower()},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        json={
            "kind": "blogger#post",
            "title": title,
            "content": content
        },
        timeout=60
    )

    response.raise_for_status()
    result = response.json()

    print(
        f"BLOGGER {'DRAFT' if is_draft else 'PUBLISHED'}: "
        f"{result.get('url', result.get('id'))}"
    )

    return result


def main():
    print("OGFlix TV automation started.")

    settings = load_settings()
    approved = load_approved_channels()
    posted = load_posted()

    print(f"Approved channels: {len(approved)}")

    videos = discover_videos(settings, approved, posted)

    print(f"New eligible videos found: {len(videos)}")

    if not videos:
        print("No new eligible videos. Finished.")
        return

    token = get_blogger_token()

    for video in videos:
        try:
            metadata = extract_metadata(video)

            if not metadata["artist"] or not metadata["song"]:
                print("Skipping video with incomplete metadata.")
                continue

            print(
                f"Processing: {metadata['artist']} - "
                f"{metadata['song']}"
            )

            image_bytes = generate_cover(
                metadata["artist"],
                metadata["song"]
            )

            poster, backdrop = upload_cover(
                image_bytes,
                metadata["artist"],
                metadata["song"],
                video["video_id"]
            )

            publish_to_blogger(
                video,
                metadata,
                poster,
                backdrop,
                token,
                settings
            )

            posted.add(video["video_id"])
            save_posted(posted)

            print(
                f"SUCCESS: {metadata['song']} | "
                f"https://www.youtube.com/watch?v="
                f"{video['video_id']}"
            )

        except Exception as exc:
            print(
                f"ERROR processing {video.get('title', 'unknown')}: "
                f"{exc}"
            )

    print("OGFlix TV automation finished.")


if __name__ == "__main__":
    main()
