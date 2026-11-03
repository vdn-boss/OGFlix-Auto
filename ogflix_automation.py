import os
import re
import io
import html
import base64
import sqlite3
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.header import Header

import requests
from dotenv import load_dotenv
from openai import OpenAI
import cloudinary
import cloudinary.uploader

load_dotenv()

YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
BLOGGER_EMAIL = os.getenv("BLOGGER_EMAIL", "1063oglive.posts@blogger.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")
APPROVED_CHANNELS = {
    x.strip() for x in os.getenv("APPROVED_CHANNEL_IDS", "").split(",") if x.strip()
}
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "48"))
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "10"))
DB = os.getenv("DATABASE_FILE", "ogflix_posted_videos.db")

required = {
    "YOUTUBE_API_KEY": YOUTUBE_KEY,
    "OPENAI_API_KEY": OPENAI_KEY,
    "CLOUDINARY_URL": os.getenv("CLOUDINARY_URL"),
    "SMTP_USER": SMTP_USER,
    "SMTP_APP_PASSWORD": SMTP_PASSWORD,
}
missing = [k for k, v in required.items() if not v]
if missing:
    raise RuntimeError("Missing .env values: " + ", ".join(missing))
if not APPROVED_CHANNELS:
    raise RuntimeError("Add official YouTube channel IDs to APPROVED_CHANNEL_IDS.")

client = OpenAI(api_key=OPENAI_KEY)
cloudinary.config(cloudinary_url=os.getenv("CLOUDINARY_URL"))


def initialize_database():
    with sqlite3.connect(DB) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS posted (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                posted_at TEXT NOT NULL
            )"""
        )


def already_posted(video_id):
    with sqlite3.connect(DB) as conn:
        return conn.execute(
            "SELECT 1 FROM posted WHERE video_id = ?", (video_id,)
        ).fetchone() is not None


def mark_posted(video_id, title):
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO posted VALUES (?, ?, ?)",
            (video_id, title, datetime.now(timezone.utc).isoformat()),
        )


def discover_videos():
    published_after = (
        datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    queries = [
        "official rap music video",
        "official hip hop music video",
        "official R&B music video",
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
                "key": YOUTUBE_KEY,
            },
            timeout=30,
        )
        response.raise_for_status()

        for item in response.json().get("items", []):
            video_id = item.get("id", {}).get("videoId")
            snippet = item.get("snippet", {})
            channel_id = snippet.get("channelId", "")
            title = snippet.get("title", "")
            if not video_id or channel_id not in APPROVED_CHANNELS:
                continue
            if already_posted(video_id):
                continue

            lowered = title.lower()
            if not any(x in lowered for x in ("official music video", "official video")):
                continue
            if any(x in lowered for x in (
                "lyrics", "lyric video", "reaction", "live performance",
                "audio only", "visualizer"
            )):
                continue

            found[video_id] = {
                "video_id": video_id,
                "title": title,
                "channel": snippet.get("channelTitle", ""),
                "channel_id": channel_id,
                "published": snippet.get("publishedAt", ""),
            }

    videos = sorted(found.values(), key=lambda x: x["published"], reverse=True)
    return videos[:MAX_PER_RUN]


def extract_metadata(video):
    clean = re.sub(
        r"\\s*[\\(\\[]?.*?(official music video|official video).*?[\\)\\]]?",
        "",
        video["title"],
        flags=re.IGNORECASE,
    ).strip(" -|()[]")
    clean = re.sub(r"\\s+", " ", clean)

    if " - " in clean:
        artist, song = clean.split(" - ", 1)
    elif " – " in clean:
        artist, song = clean.split(" – ", 1)
    else:
        artist, song = video["channel"], clean

    return {
        "artist": artist.strip(),
        "song": song.strip(),
        "year": video["published"][:4] if video["published"] else "",
    }


def generate_cover(artist, song):
    prompt = f"""
Create original cinematic theatrical movie-poster artwork for a music video.
Song title: {song}
Artist credit: {artist}
Create a visually striking, premium Hollywood-style poster in a vertical 2:3
composition. Use dramatic cinematic lighting, rich color grading, atmospheric
depth, detailed visual storytelling, and a strong central visual concept
inspired by the song title and mood. Make it look professionally commissioned.
Do not imitate existing album or movie artwork. Do not depict identifiable real
artists or existing movie characters. Do not add logos, watermarks, or text.
"""
    result = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size="1024x1536",
        quality="high",
        output_format="jpeg",
    )
    encoded = result.data[0].b64_json
    if not encoded:
        raise RuntimeError("Image API returned no base64 image data.")
    return base64.b64decode(encoded)


def upload_cover(image_bytes, artist, song):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", f"{artist}-{song}").strip("-").lower()[:120]
    uploaded = cloudinary.uploader.upload(
        io.BytesIO(image_bytes),
        folder="ogflix/music-video-covers",
        public_id=slug or "ogflix-cover",
        overwrite=False,
        resource_type="image",
    )
    url = uploaded["secure_url"]
    poster = url.replace("/upload/", "/upload/c_fill,w_600,h_900,q_auto,f_auto/")
    backdrop = url.replace("/upload/", "/upload/c_fill,w_1600,h_900,q_auto,f_auto/")
    return poster, backdrop


def build_blogger_html(video, metadata, poster, backdrop):
    artist = html.escape(metadata["artist"], quote=True)
    song = html.escape(metadata["song"], quote=True)
    year = html.escape(metadata["year"], quote=True)
    video_id = html.escape(video["video_id"], quote=True)
    youtube_embed = f"https://www.youtube.com/embed/{video_id}"

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


def send_to_blogger(video, metadata, poster, backdrop, image_bytes):
    subject = f"{metadata['song']} - {metadata['artist']}"
    body = build_blogger_html(video, metadata, poster, backdrop)
    message = MIMEMultipart("mixed")
    message["From"] = SMTP_USER
    message["To"] = BLOGGER_EMAIL
    message["Subject"] = str(Header(subject, "utf-8"))
    message.attach(MIMEText(body, "html", "utf-8"))

    attachment = MIMEImage(image_bytes, _subtype="jpeg")
    attachment.add_header(
        "Content-Disposition", "attachment", filename="ogflix-movie-cover.jpg"
    )
    message.attach(attachment)

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [BLOGGER_EMAIL], message.as_string())


def main():
    initialize_database()
    print("OGFlix TV music-video automation started.")
    videos = discover_videos()
    print(f"New videos from approved channels: {len(videos)}")

    for video in videos:
        try:
            metadata = extract_metadata(video)
            print(f"Processing: {metadata['artist']} - {metadata['song']}")
            image_bytes = generate_cover(metadata["artist"], metadata["song"])
            poster, backdrop = upload_cover(image_bytes, metadata["artist"], metadata["song"])
            send_to_blogger(video, metadata, poster, backdrop, image_bytes)
            mark_posted(video["video_id"], video["title"])
            print(
                f"SUCCESS: {metadata['song']} | "
                f"https://www.youtube.com/watch?v={video['video_id']}"
            )
        except Exception as exc:
            print(f"ERROR for {video.get('title', 'unknown video')}: {exc}")

    print("OGFlix TV automation finished.")


if __name__ == "__main__":
    main()
