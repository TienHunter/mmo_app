from __future__ import annotations

import logging
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .database import Database, utc_now
from .services import YouTubeService
from .settings import Settings

LOGGER = logging.getLogger(__name__)
HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
TOPIC_URL = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"
ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"


class WebSubService:
    def __init__(self, database: Database, settings: Settings, youtube: YouTubeService):
        self.db, self.settings, self.youtube = database, settings, youtube
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def callback_url(self) -> str:
        return self.settings.public_domain.rstrip("/") + "/webhook"

    def start(self) -> None:
        if self.server:
            return
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/health":
                    self._reply(200, b'{"status":"ok"}', "application/json"); return
                if parsed.path != "/webhook":
                    self._reply(404, b"Not found"); return
                query = urllib.parse.parse_qs(parsed.query)
                challenge = query.get("hub.challenge", [""])[0]
                mode = query.get("hub.mode", [""])[0]
                topic = query.get("hub.topic", [""])[0]
                lease = query.get("hub.lease_seconds", [""])[0]
                if not challenge or mode not in {"subscribe", "unsubscribe"}:
                    self._reply(400, b"Invalid verification"); return
                service.record_verification(mode, topic, lease)
                self._reply(200, challenge.encode("utf-8"), "text/plain; charset=utf-8")

            def do_POST(self):
                if urllib.parse.urlparse(self.path).path != "/webhook":
                    self._reply(404, b"Not found"); return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(min(length, 2_000_000))
                    count = service.receive_notification(body)
                    self._reply(200, f"OK {count}".encode())
                except Exception as exc:
                    LOGGER.exception("Invalid WebSub notification")
                    self._reply(400, str(exc).encode("utf-8", errors="replace"))

            def _reply(self, status, body, content_type="text/plain"):
                self.send_response(status); self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def log_message(self, pattern, *args):
                LOGGER.info("WebSub HTTP: " + pattern, *args)

        self.server = ThreadingHTTPServer((self.settings.webhook_host, self.settings.webhook_port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="websub-server", daemon=True)
        self.thread.start()
        LOGGER.info("WebSub callback listening on http://%s:%s/webhook", self.settings.webhook_host, self.settings.webhook_port)

    def stop(self) -> None:
        if self.server:
            self.server.shutdown(); self.server.server_close(); self.server = None
        if self.thread:
            self.thread.join(3); self.thread = None

    def renew_due(self, force: bool = False) -> int:
        if not self.settings.public_domain:
            LOGGER.warning("WebSub chưa subscribe: PUBLIC_DOMAIN đang trống")
            return 0
        self.youtube.sync_channels()
        threshold = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with self.db.connect() as connection:
            if force:
                rows = connection.execute("SELECT * FROM channels WHERE active=1").fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM channels WHERE active=1 AND
                    (subscription_status!='subscribed' OR subscription_expires_at IS NULL OR subscription_expires_at<=?)""",
                    (threshold,),
                ).fetchall()
        succeeded = 0
        for channel in rows:
            try:
                LOGGER.info("WebSub subscribing channel=%s callback=%s", channel["channel_id"], self.callback_url)
                self.subscribe(channel["channel_id"]); succeeded += 1
            except Exception as exc:
                LOGGER.exception("WebSub subscribe %s failed", channel["channel_id"])
                with self.db.connect() as connection:
                    connection.execute("UPDATE channels SET subscription_status='error',subscription_error=? WHERE id=?", (str(exc)[:1000], channel["id"]))
        if rows:
            LOGGER.info("WebSub subscribe requests sent: %s/%s", succeeded, len(rows))
        return succeeded

    def subscribe(self, channel_id: str) -> None:
        data = urllib.parse.urlencode({
            "hub.callback": self.callback_url,
            "hub.topic": TOPIC_URL.format(channel_id=channel_id),
            "hub.verify": "async", "hub.mode": "subscribe",
            "hub.lease_seconds": str(self.settings.subscription_lease_seconds),
        }).encode()
        request = urllib.request.Request(HUB_URL, data=data, method="POST")
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in {202, 204}:
                raise RuntimeError(f"YouTube Hub HTTP {response.status}")
            LOGGER.info("WebSub hub accepted subscribe request channel=%s http=%s", channel_id, response.status)
        with self.db.connect() as connection:
            connection.execute("UPDATE channels SET subscription_status='pending',subscription_error=NULL WHERE channel_id=?", (channel_id,))

    def record_verification(self, mode: str, topic: str, lease: str) -> None:
        channel_id = urllib.parse.parse_qs(urllib.parse.urlparse(topic).query).get("channel_id", [""])[0]
        if not channel_id:
            return
        if mode == "unsubscribe":
            status, subscribed, expires = "not_subscribed", None, None
        else:
            seconds = int(lease or self.settings.subscription_lease_seconds)
            status, subscribed = "subscribed", utc_now()
            expires = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
        with self.db.connect() as connection:
            connection.execute("""UPDATE channels SET subscription_status=?,subscribed_at=?,subscription_expires_at=?,
                subscription_error=NULL WHERE channel_id=?""", (status, subscribed, expires, channel_id))
        if mode == "subscribe":
            LOGGER.info("WebSub subscribed channel=%s expires_at=%s", channel_id, expires)
        else:
            LOGGER.info("WebSub unsubscribed channel=%s", channel_id)

    def receive_notification(self, body: bytes) -> int:
        root = ET.fromstring(body)
        count = 0
        for entry in root.findall(f"{{{ATOM}}}entry"):
            video_id = entry.findtext(f"{{{YT}}}videoId", "")
            channel_id = entry.findtext(f"{{{YT}}}channelId", "")
            title = entry.findtext(f"{{{ATOM}}}title", "")
            published = entry.findtext(f"{{{ATOM}}}published", "")
            if video_id and channel_id and self.youtube.discover(video_id, channel_id, title, published):
                LOGGER.info("WebSub notification queued video=%s channel=%s title=%s", video_id, channel_id, title)
                count += 1
            elif video_id and channel_id:
                LOGGER.info("WebSub notification ignored duplicate video=%s channel=%s", video_id, channel_id)
        if count:
            LOGGER.info("WebSub notification processed new_videos=%s", count)
        return count
