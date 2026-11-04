
import os
import re
import io
import json
import html
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
import cloudinary
import cloudinary.uploader
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SETTINGS_FILE = DATA / "settings.json"
CHANNELS_FILE = DATA / "approved_channels.json"
POSTED_FILE = DATA / "posted_videos.json"

YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")

BLOGGER_CLIENT_ID = os.getenv("BLOGGER_CLIENT_ID")
BLOGGER_CLIENT_SECRET = os.getenv("BLOGGER_CLIENT_SECRET")
BLOGGER_REFRESH_TOKEN = os.getenv("BLOGGER_REFRESH_TOKEN")
BLOGGER_BLOG_ID = os.getenv("BLOGGER_BLOG_ID")


for name, value in {
    "YOUTUBE_API_KEY": YOUTUBE_KEY,
    "CLOUDINARY_URL": CLOUDINARY_URL,
    "BLOGGER_CLIENT_ID": BLOGGER_CLIENT_ID,
    "BLOGGER_CLIENT_SECRET": BLOGGER_CLIENT_SECRET,
    "BLOGGER_REFRESH_TOKEN": BLOGGER_REFRESH_TOKEN,
    "BLOGGER_BLOG_ID": BLOGGER_BLOG_ID,
}.items():
    if not value:
        raise RuntimeError(f"Missing GitHub secret: {name}")

cloudinary.config(cloudinary_url=CLOUDINARY_URL)


def read_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_settings():
    defaults = {
        "blog_name": "OGFlix TV",
        "blog_url": "https://ogflixtv.blogspot.com/",
        "music_categories": ["Rap", "Hip-Hop", "R&B"],
        "post_mode": "draft",
        "videos_per_run": 10,
        "lookback_hours": 48,
        "auto_discovery": True,
    }
    saved = read_json(SETTINGS_FILE, {})
    if not isinstance(saved, dict):
        raise RuntimeError("data/settings.json must contain a JSON object.")
    defaults.update(saved)
    return defaults



def load_approved_channels():
    entries = read_json(CHANNELS_FILE, [])
    resolved = set()

    if not isinstance(entries, list):
        raise RuntimeError(
            "data/approved_channels.json must contain a JSON array of handles or channel IDs."
        )

    print(f"Reading {len(entries)} approved channel entries.")
    print(f"Channel file path: {CHANNELS_FILE}")
    print(f"YouTube API key loaded: {bool(YOUTUBE_KEY)}")

    for entry in entries:
        value = str(entry).strip()

        if not value:
            continue

        # Existing YouTube channel IDs can be used directly.
        if value.startswith("UC") and "/" not in value:
            resolved.add(value)
            print(f"APPROVED CHANNEL ID: {value}")
            continue

        # Accept @handles or full YouTube channel URLs.
        match = re.search(r"youtube\.com/@([^/?]+)", value)
        handle = match.group(1) if match else value.lstrip("@")

        try:
            response = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={
                    "part": "id",
                    "forHandle": handle,
                    "key": YOUTUBE_KEY,
                },
                timeout=30,
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


def load_posted():
    return set(read_json(POSTED_FILE, []))


def save_posted(posted):
    DATA.mkdir(parents=True, exist_ok=True)
    with POSTED_FILE.open("w", encoding="utf-8") as file:
        json.dump(sorted(posted), file, indent=2)


def discover_videos(settings, approved, posted):
    """
    Find videos from two sources:
    1. Every manually approved channel, searched directly.
    2. Broader Rap / Hip-Hop / R&B searches for likely official releases.

    YouTube does not expose a reliable Official Artist Channel verification
    flag through search.list, so broad discovery uses conservative title and
    channel-name checks. Review the first live posts before relying on it.
    """
    hours = int(settings.get("lookback_hours", 48))
    maximum = int(settings.get("videos_per_run", 10))
    auto_discovery = settings.get("auto_discovery", True)
    print(f"Automatic discovery enabled: {bool(auto_discovery)}")

    published_after = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")

    found = {}

    excluded_terms = (
        "lyrics", "lyric video", "reaction", "live performance",
        "audio only", "visualizer", "fan made", "cover", "dance practice",
        "behind the scenes", "interview", "shorts"
    )
    official_title_terms = (
        "official music video", "official video", "official mv"
    )
    trusted_channel_terms = (
        "vevo", "official", "records", "recordings", "music",
        "entertainment", "label", "universal", "atlantic", "warner",
        "sony", "def jam", "motown", "roc nation", "cash money",
        "young money", "interscope", "empire", "quality control"
    )

    def search_videos(params, source_label):
        request_params = {
            "part": "snippet",
            "type": "video",
            "order": "date",
            "publishedAfter": published_after,
            "videoCategoryId": "10",
            "maxResults": 50,
            "key": YOUTUBE_KEY,
        }
        request_params.update(params)

        response = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params=request_params,
            timeout=30,
        )
        response.raise_for_status()

        items = response.json().get("items", [])
        print(f"{source_label}: YouTube returned {len(items)} results.")

        for item in items:
            video_id = item.get("id", {}).get("videoId")
            snippet = item.get("snippet", {})
            channel_id = snippet.get("channelId", "")
            title = snippet.get("title", "")
            channel_name = snippet.get("channelTitle", "")
            lowered_title = title.lower()
            lowered_channel = channel_name.lower()

            if not video_id or video_id in posted:
                continue

            if any(term in lowered_title for term in excluded_terms):
                continue

            has_official_title = any(
                term in lowered_title for term in official_title_terms
            )
            if not has_official_title:
                continue

            # Direct searches are trusted because the channel was manually
            # approved. Broad searches require a recognizable official,
            # VEVO, label, or music-company channel name.
            if source_label == "AUTOMATIC DISCOVERY":
                if not any(term in lowered_channel for term in trusted_channel_terms):
                    continue

            found[video_id] = {
                "video_id": video_id,
                "title": title,
                "channel": channel_name,
                "channel_id": channel_id,
                "published": snippet.get("publishedAt", ""),
                "thumbnail_url": (
                    snippet.get("thumbnails", {}).get("maxres", {}).get("url")
                    or snippet.get("thumbnails", {}).get("high", {}).get("url")
                    or snippet.get("thumbnails", {}).get("medium", {}).get("url")
                    or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                ),
                "discovery_source": source_label,
            }

    # Search each approved channel directly. This avoids losing its uploads
    # just because a global YouTube search did not rank them in its first 50.
    print(f"Searching {len(approved)} approved channels directly.")
    for channel_id in sorted(approved):
        search_videos(
            {"channelId": channel_id},
            f"APPROVED CHANNEL {channel_id}",
        )

    # Add wider discovery without requiring the channel to be in the manual
    # approval list. Broad results are filtered more strictly above.
    if auto_discovery:
        broad_queries = (
            "official rap music video",
            "official hip hop music video",
            "official R&B music video",
        )
        for query in broad_queries:
            search_videos({"q": query}, "AUTOMATIC DISCOVERY")

    videos = sorted(
        found.values(),
        key=lambda video: video["published"],
        reverse=True,
    )

    print(f"Unique eligible videos after filtering: {len(videos)}")
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


def load_poster_font(size, bold=True):
    """Load a common Linux font, with a Pillow fallback."""
    candidates = []
    if bold:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ])
    else:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ])

    for font_path in candidates:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)
    return ImageFont.load_default()


def wrap_poster_text(draw, text, font, max_width):
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def generate_cover(video, artist, song):
    """Create a 2:3 movie-poster graphic from the video's YouTube thumbnail.

    This is a local Pillow design; it does not call a paid image-generation API.
    """
    width, height = 1200, 1800
    thumbnail_url = video.get("thumbnail_url") or (
        f"https://i.ytimg.com/vi/{video['video_id']}/hqdefault.jpg"
    )

    response = requests.get(thumbnail_url, timeout=45)
    if not response.ok:
        # Some uploads do not provide a max-resolution thumbnail.
        fallback_url = f"https://i.ytimg.com/vi/{video['video_id']}/hqdefault.jpg"
        response = requests.get(fallback_url, timeout=45)
    response.raise_for_status()

    from io import BytesIO
    original = Image.open(BytesIO(response.content)).convert("RGB")

    # Full-bleed, softened image gives the poster a cinematic background.
    background = ImageOps.fit(original, (width, height), method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(radius=24))
    background = ImageEnhance.Brightness(background).enhance(0.42)
    poster = background.convert("RGBA")

    # Dark gradients create readable text while keeping the source image visible.
    top_gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    top_draw = ImageDraw.Draw(top_gradient)
    for y in range(height):
        alpha = int(175 * max(0, 1 - y / 680))
        if alpha:
            top_draw.line((0, y, width, y), fill=(5, 8, 15, alpha))
    poster = Image.alpha_composite(poster, top_gradient)

    bottom_gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    bottom_draw = ImageDraw.Draw(bottom_gradient)
    start_y = 900
    for y in range(start_y, height):
        progress = (y - start_y) / max(1, height - start_y)
        alpha = int(70 + 175 * progress)
        bottom_draw.line((0, y, width, y), fill=(3, 5, 10, alpha))
    poster = Image.alpha_composite(poster, bottom_gradient)

    # Main thumbnail panel, preserving the familiar video frame.
    frame = ImageOps.fit(original, (1080, 608), method=Image.Resampling.LANCZOS)
    frame = ImageEnhance.Contrast(frame).enhance(1.08)
    frame = ImageEnhance.Color(frame).enhance(1.12)
    frame_x, frame_y = 60, 350
    poster.alpha_composite(frame.convert("RGBA"), (frame_x, frame_y))

    draw = ImageDraw.Draw(poster)
    white = (255, 255, 255, 255)
    accent = (244, 190, 65, 255)

    small_font = load_poster_font(34, bold=True)
    eyebrow_font = load_poster_font(30, bold=True)
    title_font_size = 112
    title_font = load_poster_font(title_font_size, bold=True)
    artist_font = load_poster_font(58, bold=True)
    footer_font = load_poster_font(28, bold=True)

    # Top brand line.
    draw.text((64, 78), "OGFLIX TV", font=load_poster_font(54, bold=True), fill=white)
    draw.text((66, 150), "MUSIC VIDEO PREMIERE", font=eyebrow_font, fill=accent)
    draw.line((64, 215, 1136, 215), fill=(255, 255, 255, 150), width=3)

    # Poster title and artist.
    title_lines = wrap_poster_text(draw, song.upper(), title_font, 1060)
    if len(title_lines) > 2:
        title_font_size = 88
        title_font = load_poster_font(title_font_size, bold=True)
        title_lines = wrap_poster_text(draw, song.upper(), title_font, 1060)
    y = 1110
    for line in title_lines[:4]:
        draw.text((64, y), line, font=title_font, fill=white, stroke_width=2, stroke_fill=(0, 0, 0, 170))
        y += int(title_font_size * 1.05)

    artist_y = min(y + 24, 1580)
    artist_text = artist.upper()
    artist_lines = wrap_poster_text(draw, artist_text, artist_font, 1060)
    for line in artist_lines[:2]:
        draw.text((68, artist_y), line, font=artist_font, fill=accent)
        artist_y += 72

    year = video.get("published", "")[:4]
    footer = f"OFFICIAL MUSIC VIDEO  •  {year}" if year else "OFFICIAL MUSIC VIDEO"
    draw.line((64, 1690, 1136, 1690), fill=(255, 255, 255, 130), width=2)
    draw.text((66, 1720), footer, font=footer_font, fill=white)

    output = BytesIO()
    poster.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()


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

    if not response.ok:
        print("BLOGGER TOKEN ERROR:", response.text)
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
                video,
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
