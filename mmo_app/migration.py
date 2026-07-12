from __future__ import annotations

import sqlite3
from pathlib import Path

from .database import Database


def import_youtube_tracker(target: Database, source_path: Path) -> tuple[int, int]:
    """Idempotently import channels and videos without changing the source database."""
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    channels = videos = 0
    try:
        with target.connect() as destination:
            for row in source.execute("SELECT * FROM tracked_channels"):
                cursor = destination.execute(
                    "INSERT OR IGNORE INTO channels(channel_id,title,active,last_checked_at) VALUES (?,?,?,?)",
                    (row["channel_id"], row["channel_name"], row["is_active"], row["last_fallback_checked_at"]),
                ); channels += cursor.rowcount
            for row in source.execute("SELECT * FROM videos"):
                cursor = destination.execute(
                    """INSERT OR IGNORE INTO media_items(youtube_video_id,channel_id,title,source_url,published_at,status,error_message)
                    VALUES (?,?,?,?,?,?,?)""",
                    (row["video_id"], row["channel_id"], row["title"], row["source_url"], row["published_at"], row["status"], row["error_message"]),
                )
                videos += cursor.rowcount
                item = destination.execute("SELECT id FROM media_items WHERE youtube_video_id=?", (row["video_id"],)).fetchone()
                if row["local_path"] and Path(row["local_path"]).is_file():
                    path = Path(row["local_path"])
                    destination.execute("INSERT OR IGNORE INTO artifacts(media_item_id,kind,path,size_bytes) VALUES (?,?,?,?)", (item["id"], "download", str(path), path.stat().st_size))
    finally:
        source.close()
    return channels, videos
