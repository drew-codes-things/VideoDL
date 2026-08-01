QUALITY_TIERS = {
    "best": {
        "label": "Best available",
        "format": (
            "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
            "best[vcodec^=avc1][ext=mp4]/best[ext=mp4]/best"
        ),
        "postprocessors": [],
    },
    "1080p": {
        "label": "1080p",
        "format": (
            "bestvideo[vcodec^=avc1][ext=mp4][height<=1080]+bestaudio[ext=m4a]/"
            "best[vcodec^=avc1][ext=mp4][height<=1080]/best[ext=mp4][height<=1080]/best[height<=1080]"
        ),
        "postprocessors": [],
    },
    "720p": {
        "label": "720p",
        "format": (
            "bestvideo[vcodec^=avc1][ext=mp4][height<=720]+bestaudio[ext=m4a]/"
            "best[vcodec^=avc1][ext=mp4][height<=720]/best[ext=mp4][height<=720]/best[height<=720]"
        ),
        "postprocessors": [],
    },
    "480p": {
        "label": "480p",
        "format": (
            "bestvideo[vcodec^=avc1][ext=mp4][height<=480]+bestaudio[ext=m4a]/"
            "best[vcodec^=avc1][ext=mp4][height<=480]/best[ext=mp4][height<=480]/best[height<=480]"
        ),
        "postprocessors": [],
    },
    "audio": {
        "label": "Audio only (MP3)",
        "format": "bestaudio/best",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
    },
}


def resolve_quality(key):
    if key not in QUALITY_TIERS:
        raise ValueError(f"Unknown quality tier: {key}")
    return QUALITY_TIERS[key]


def quality_choices():
    return [(key, tier["label"]) for key, tier in QUALITY_TIERS.items()]
