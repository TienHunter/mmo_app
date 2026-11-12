from __future__ import annotations

import tempfile
import unittest
import logging
import time
from datetime import datetime, timedelta, timezone
import json
import sys
import types
import threading
from pathlib import Path
from unittest import mock
from zipfile import ZipFile

from mmo_app.application import Application
from mmo_app.database import Database
from mmo_app.desktop import Desktop
from mmo_app.reporting import build_pipeline_report_rows, export_pipeline_report
from mmo_app import services
from mmo_app.services import DownloadHandler, GpmLoginClient, ManualAction, PipelineOrchestrator, ProcessHandler, TikTokBrowserOutOfMemory, UploadHandler, YouTubeService
from mmo_app.settings import DEFAULT_GPM_API_URL, Settings
from mmo_app.ui.services.repository import UIRepository, format_local_time
from mmo_app.websub import WebSubService


class PipelineDatabaseTests(unittest.TestCase):
    def setUp(self):
        logging.shutdown()
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(data_dir=str(root), download_dir=str(root / "downloads"), output_dir=str(root / "output"))
        self.db = Database(root / "test.db"); self.db.migrate()

    def tearDown(self):
        logging.shutdown()
        self.temp.cleanup()

    def test_discovery_and_claim_are_idempotent(self):
        service = YouTubeService(self.db, self.settings)
        self.assertTrue(service.discover("abcdefghijk", "UCtest", "Demo"))
        self.assertFalse(service.discover("abcdefghijk", "UCtest", "Demo"))
        job = self.db.claim("test", ("download",))
        self.assertEqual("download", job["kind"])
        self.assertIsNone(self.db.claim("test2", ("download",)))

    def test_log_time_is_displayed_in_local_timezone(self):
        self.assertEqual("2026-07-26 20:53:02", Desktop.format_log_time("2026-07-26T13:53:02+00:00"))
        self.assertEqual("2026-07-26 20:53:02", Desktop.format_log_time("2026-07-26 13:53:02"))

    def test_ui_time_is_displayed_in_utc_plus_7(self):
        self.assertEqual(
            "2026-07-26 20:53:02",
            format_local_time("2026-07-26T13:53:02+00:00"),
        )
        self.assertEqual(
            "20:53:02",
            format_local_time("2026-07-26T13:53:02+00:00", include_date=False),
        )
        self.assertEqual(
            "2026-07-26 20:53:02",
            format_local_time("2026-07-26 13:53:02"),
        )
        self.assertEqual("", format_local_time(None))

    def test_default_pipeline_worker_counts(self):
        self.assertEqual(2, self.settings.max_download_workers)
        self.assertEqual(1, self.settings.max_process_workers)
        self.assertEqual(1, self.settings.max_upload_workers)

    def test_retry_and_recovery(self):
        service = YouTubeService(self.db, self.settings); service.discover("12345678901", "UCtest", "Demo")
        job = self.db.claim("test", ("download",), lease_seconds=-1)
        self.assertEqual(1, self.db.recover_expired_leases())
        again = self.db.claim("test", ("download",)); self.db.fail(again["id"], "boom")
        with self.db.connect() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE id=?", (again["id"],)).fetchone()
        self.assertEqual("retry_wait", row["status"])

    def test_download_passes_ffmpeg_location_to_ytdlp(self):
        captured_options = {}
        output_path = Path(self.settings.download_dir) / "UCtest" / "Downloaded [ffmpeg00001].mp4"
        self.settings.ffmpeg_path = str(Path(self.temp.name) / "ffmpeg.exe")

        class FakeYoutubeDL:
            def __init__(self, options):
                captured_options.update(options)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=True):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"mp4")
                return {"id": "ffmpeg00001", "title": "Downloaded", "ext": "mp4"}

            def prepare_filename(self, info):
                return str(output_path)

        original_module = sys.modules.get("yt_dlp")
        sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        try:
            with self.db.connect() as connection:
                media_id = connection.execute(
                    """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url)
                       VALUES ('ffmpeg00001','UCtest','Downloaded','https://www.youtube.com/watch?v=ffmpeg00001')"""
                ).lastrowid
            DownloadHandler(self.db, self.settings).run({"media_item_id": media_id})
        finally:
            if original_module is None:
                sys.modules.pop("yt_dlp", None)
            else:
                sys.modules["yt_dlp"] = original_module

        self.assertEqual(self.settings.ffmpeg_path, captured_options["ffmpeg_location"])

    def test_download_propagates_test_payload_to_process_job(self):
        output_path = Path(self.settings.download_dir) / "test-account" / "Downloaded [testjob0001].mp4"

        class FakeYoutubeDL:
            def __init__(self, options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=True):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"mp4")
                return {"id": "testjob0001", "title": "Downloaded", "ext": "mp4"}

            def prepare_filename(self, info):
                return str(output_path)

        original_module = sys.modules.get("yt_dlp")
        original_bell = services.play_notification_bell
        sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        services.play_notification_bell = lambda *_args, **_kwargs: True
        try:
            with self.db.connect() as connection:
                account_id = connection.execute(
                    "INSERT INTO tiktok_accounts(name) VALUES ('Test')").lastrowid
                media_id = connection.execute(
                    """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url)
                       VALUES ('testjob0001','test_account_1','Downloaded','https://www.youtube.com/watch?v=testjob0001')"""
                ).lastrowid
            job_id = self.db.enqueue(
                "download", media_id, "download:testjob0001",
                json.dumps({"test_account_id": account_id, "is_test": 1}))
            with self.db.connect() as connection:
                job = connection.execute(
                    "SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            DownloadHandler(self.db, self.settings).run(job)
        finally:
            services.play_notification_bell = original_bell
            if original_module is None:
                sys.modules.pop("yt_dlp", None)
            else:
                sys.modules["yt_dlp"] = original_module
        with self.db.connect() as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE kind='process' ORDER BY id DESC LIMIT 1").fetchone()
        payload = json.loads(job["payload"])
        self.assertEqual(1, payload["is_test"])
        self.assertEqual(account_id, payload["test_account_id"])
        self.assertIn("artifact_id", payload)

    def test_download_uses_cookiefile_when_vpn_mode_is_enabled(self):
        captured_options = {}
        output_path = Path(self.settings.download_dir) / "UCtest" / "Downloaded [cookie00001].mp4"
        cookies_path = Path(self.temp.name) / "cookies.txt"
        cookies_path.write_text("dummy", encoding="utf-8")
        self.settings.use_vpn_for_youtube = True
        self.settings.youtube_cookies_path = str(cookies_path)

        class FakeYoutubeDL:
            def __init__(self, options):
                captured_options.update(options)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=True):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"mp4")
                return {"id": "cookie00001", "title": "Downloaded", "ext": "mp4"}

            def prepare_filename(self, info):
                return str(output_path)

        original_module = sys.modules.get("yt_dlp")
        sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        try:
            with self.db.connect() as connection:
                media_id = connection.execute(
                    """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url)
                       VALUES ('cookie00001','UCtest','Downloaded','https://www.youtube.com/watch?v=cookie00001')"""
                ).lastrowid
            DownloadHandler(self.db, self.settings).run({"media_item_id": media_id})
        finally:
            if original_module is None:
                sys.modules.pop("yt_dlp", None)
            else:
                sys.modules["yt_dlp"] = original_module

        self.assertEqual(str(cookies_path), captured_options["cookiefile"])

    def test_download_skips_video_when_orientation_filter_does_not_match(self):
        self.settings.source_orientation_filter = "portrait"
        extract_calls = []

        class FakeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, _url, download=True):
                extract_calls.append(download)
                if download:
                    raise AssertionError("Không được tải video khi metadata đã biết không đúng hướng")
                return {"id": "orient00001", "title": "Landscape", "ext": "mp4", "width": 1920, "height": 1080}

            def prepare_filename(self, _info):
                raise AssertionError("Không được chuẩn bị filename khi đã skip trước tải")

        original_module = sys.modules.get("yt_dlp")
        sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        try:
            with self.db.connect() as connection:
                connection.execute("INSERT INTO channels(channel_id) VALUES ('UCvertical')")
                account_id = connection.execute("INSERT INTO tiktok_accounts(name) VALUES ('TikTok')").lastrowid
                route_id = connection.execute("INSERT INTO channel_routes(name,account_id) VALUES ('Layout',?)", (account_id,)).lastrowid
                connection.execute(
                    "INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)",
                    (route_id, "UCvertical", "https://youtube.com/@vertical"),
                )
                media_id = connection.execute(
                    """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url)
                       VALUES ('orient00001','UCvertical','Landscape','https://www.youtube.com/watch?v=orient00001')"""
                ).lastrowid
                job_id = connection.execute(
                    """INSERT INTO jobs(kind,media_item_id,idempotency_key,status)
                       VALUES ('download',?,'download:orient00001','leased')""",
                    (media_id,),
                ).lastrowid
            DownloadHandler(self.db, self.settings).run({"id": job_id, "media_item_id": media_id})
        finally:
            if original_module is None:
                sys.modules.pop("yt_dlp", None)
            else:
                sys.modules["yt_dlp"] = original_module

        with self.db.connect() as connection:
            media = connection.execute("SELECT status,error_message FROM media_items WHERE id=?", (media_id,)).fetchone()
            process_jobs = connection.execute("SELECT COUNT(*) count FROM jobs WHERE kind='process'").fetchone()["count"]
            event = connection.execute("SELECT message FROM job_events ORDER BY id DESC LIMIT 1").fetchone()["message"]
        self.assertEqual("skipped", media["status"])
        self.assertIn("chỉ video dọc", media["error_message"])
        self.assertEqual(0, process_jobs)
        self.assertIn("bỏ qua video", event)
        self.assertEqual([False], extract_calls)

    def test_download_falls_back_to_local_probe_when_metadata_has_no_dimensions(self):
        self.settings.source_orientation_filter = "portrait"
        output_path = Path(self.settings.download_dir) / "UCfallback" / "Landscape [fallback01].mp4"
        extract_calls = []

        class FakeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, _url, download=True):
                extract_calls.append(download)
                if not download:
                    return {"id": "fallback01", "title": "Landscape", "ext": "mp4"}
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"mp4")
                return {"id": "fallback01", "title": "Landscape", "ext": "mp4"}

            def prepare_filename(self, _info):
                return str(output_path)

        original_module = sys.modules.get("yt_dlp")
        original_dimensions = DownloadHandler._dimensions
        sys.modules["yt_dlp"] = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        try:
            DownloadHandler._dimensions = lambda _handler, _path: (1920, 1080)
            with self.db.connect() as connection:
                media_id = connection.execute(
                    """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url)
                       VALUES ('fallback01','UCfallback','Landscape','https://www.youtube.com/watch?v=fallback01')"""
                ).lastrowid
                job_id = connection.execute(
                    """INSERT INTO jobs(kind,media_item_id,idempotency_key,status)
                       VALUES ('download',?,'download:fallback01','leased')""",
                    (media_id,),
                ).lastrowid
            DownloadHandler(self.db, self.settings).run({"id": job_id, "media_item_id": media_id})
        finally:
            DownloadHandler._dimensions = original_dimensions
            if original_module is None:
                sys.modules.pop("yt_dlp", None)
            else:
                sys.modules["yt_dlp"] = original_module

        with self.db.connect() as connection:
            media = connection.execute("SELECT status,error_message FROM media_items WHERE id=?", (media_id,)).fetchone()
            events = [row["message"] for row in connection.execute("SELECT message FROM job_events ORDER BY id").fetchall()]
            process_jobs = connection.execute("SELECT COUNT(*) count FROM jobs WHERE kind='process'").fetchone()["count"]
        self.assertEqual([False, True], extract_calls)
        self.assertEqual("skipped", media["status"])
        self.assertIn("file đã tải", media["error_message"])
        self.assertEqual(0, process_jobs)
        self.assertTrue(any("sẽ tải rồi kiểm tra bằng ffprobe" in event for event in events))

    def test_websub_notification_is_primary_ingest(self):
        youtube = YouTubeService(self.db, self.settings)
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO channels(channel_id,title,source_url) VALUES ('UCdemo','Demo Channel','https://www.youtube.com/@demo')"
            )
        websub = WebSubService(self.db, self.settings, youtube)
        body = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
        <entry><yt:videoId>websub00001</yt:videoId><yt:channelId>UCdemo</yt:channelId>
        <title>Realtime</title><published>2026-07-07T00:00:00Z</published></entry></feed>'''
        self.assertEqual(1, websub.receive_notification(body))
        self.assertEqual(0, websub.receive_notification(body))
        with self.db.connect() as connection:
            job = connection.execute("SELECT kind,status FROM jobs").fetchone()
            events = [row["message"] for row in connection.execute("SELECT message FROM job_events ORDER BY id").fetchall()]
        self.assertEqual(("download", "queued"), (job["kind"], job["status"]))
        self.assertTrue(any("WebSub đã nhận thông báo video mới: Realtime" in event for event in events))
        self.assertTrue(any("Demo Channel" in event and "https://www.youtube.com/watch?v=websub00001" in event for event in events))
        self.assertTrue(any("WebSub nhận thông báo video đã có trong hệ thống nên bỏ qua" in event for event in events))

    def test_websub_verification_marks_channel_subscribed(self):
        channel_id = "UCverify000000000000000001"
        with self.db.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id,title) VALUES (?,?)", (channel_id, "Verify Channel"))
        websub = WebSubService(self.db, self.settings, YouTubeService(self.db, self.settings))
        websub.record_verification(
            "subscribe",
            f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "3600",
        )
        with self.db.connect() as connection:
            channel = connection.execute("SELECT subscription_status,subscribed_at,subscription_expires_at FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
            event = connection.execute("SELECT message FROM job_events ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual("subscribed", channel["subscription_status"])
        self.assertTrue(channel["subscribed_at"])
        self.assertTrue(channel["subscription_expires_at"])
        self.assertIn("WebSub đã subscribe thành công kênh YouTube: Verify Channel", event["message"])

    def test_mobile_aspect_ratio_filters_fit_without_cropping(self):
        self.assertEqual("", ProcessHandler.video_filter("original"))
        self.assertEqual(
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
            ProcessHandler.video_filter("9:16"),
        )
        self.assertIn("pad=1080:1350", ProcessHandler.video_filter("4:5"))
        self.assertNotIn("crop=", ProcessHandler.video_filter("9:16"))

    def test_zoom_in_filter_crops_excess_and_pads_missing_space(self):
        filter_text = ProcessHandler.video_filter("9:16", 150)
        self.assertIn("scale=1620:2880:force_original_aspect_ratio=decrease", filter_text)
        self.assertIn(r"crop=min(iw\,1080):min(ih\,1920):(iw-ow)/2:(ih-oh)/2", filter_text)
        self.assertIn("pad=1080:1920", filter_text)
        box = ProcessHandler.scaled_video_box((1920, 1080), "9:16", 150)
        self.assertEqual(1080, box.video_width)
        self.assertEqual(910, box.video_height)

    def test_independent_height_zoom_stretches_vertical_only(self):
        filter_text = ProcessHandler.video_filter("9:16", 100, 140)
        self.assertIn(
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "scale=trunc(iw*1/2)*2:trunc(ih*1.4/2)*2",
            filter_text,
        )
        box = ProcessHandler.scaled_video_box((1920, 1080), "9:16", 100, 140)
        self.assertEqual(1080, box.video_width)
        self.assertGreater(box.video_height, 606)
        self.assertLess(box.video_height, 1920)
        width_box = ProcessHandler.scaled_video_box((1920, 1080), "9:16", 150, 100)
        self.assertEqual(606, width_box.video_height)
        self.assertGreaterEqual(width_box.video_width, 1080)

    def test_caption_above_video_uses_rounded_font(self):
        handler = ProcessHandler(self.db, self.settings)
        font = handler.caption_font("Tiêu đề tiếng Việt", 56)
        self.assertIsNotNone(font)

    def test_zoom_factor_can_go_down_to_zero(self):
        filter_text = ProcessHandler.video_filter("9:16", 0)
        self.assertIn("scale=2:2:force_original_aspect_ratio=decrease", filter_text)
        self.settings.video_zoom_percent = 0
        options = ProcessHandler(self.db, self.settings)._processing_options(None)
        self.assertEqual(0, options.zoom_percent)

    def test_download_start_plays_account_notification_sound(self):
        with self.db.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCbell')")
            account_id = connection.execute(
                "INSERT INTO tiktok_accounts(name,notification_sound) VALUES ('TikTok','bell.wav')"
            ).lastrowid
            route_id = connection.execute(
                "INSERT INTO channel_routes(name,account_id) VALUES ('Layout',?)", (account_id,)
            ).lastrowid
            connection.execute(
                "INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)",
                (route_id, "UCbell", "https://youtube.com/@bell"),
            )
        played = []
        original = services.play_notification_bell
        try:
            services.play_notification_bell = lambda path="", ffmpeg_binary="ffmpeg": played.append(path) or True
            DownloadHandler(self.db, self.settings)._notify_download_started(None, "UCbell", "Bell demo")
        finally:
            services.play_notification_bell = original
        self.assertEqual(["bell.wav"], played)

    def test_notification_bell_stops_on_demand(self):
        bell = services.NotificationBell(duration_seconds=30)
        with mock.patch("winsound.MessageBeep"), mock.patch("winsound.PlaySound"):
            self.assertTrue(bell.play("", "ffmpeg"))
            self.assertTrue(bell.is_playing)
            bell.stop()
            self.assertFalse(bell.is_playing)
            time.sleep(0.05)

    def test_part_count_uses_speed_adjusted_duration_thresholds(self):
        expected = {
            299.9: 1,
            300.0: 2,
            479.9: 2,
            480.0: 3,
            899.9: 3,
            900.0: 4,
            1079.9: 4,
            1080.0: 5,
            3600.0: 5,
        }
        for duration, parts in expected.items():
            self.assertEqual(parts, ProcessHandler.part_count_for_duration(duration))

    def test_parts_have_slight_length_difference_and_absorb_remainder(self):
        ranges = ProcessHandler.segment_ranges_for_parts(1000.0, 4)
        self.assertEqual(4, len(ranges))
        lengths = [length for _, length in ranges]
        self.assertAlmostEqual(1000.0, sum(lengths), places=6)
        self.assertGreater(len(set(round(length, 3) for length in lengths)), 1)
        self.assertEqual(4, len(set(round(length, 3) for length in lengths)))
        for length in lengths:
            self.assertGreaterEqual(length, 250.0 * 0.9)
            self.assertLessEqual(length, 250.0 * 1.1)
        self.assertAlmostEqual(ranges[-1][0], sum(lengths[:-1]), places=6)
        self.assertEqual([(0.0, 42.0)], ProcessHandler.segment_ranges_for_parts(42.0, 1))

    def test_process_splits_after_applying_speed_timeline(self):
        self.settings.video_speed = 2.0
        self.settings.split_enabled = True
        source = Path(self.temp.name) / "source.mp4"
        source.write_bytes(b"video")
        youtube = YouTubeService(self.db, self.settings)
        youtube.discover("speed000001", "UCspeed", "Speed demo")
        with self.db.connect() as connection:
            media_id = connection.execute("SELECT id FROM media_items WHERE youtube_video_id='speed000001'").fetchone()["id"]
            artifact_id = connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path) VALUES (?,'downloaded',?)", (media_id, str(source))
            ).lastrowid
            job = {"id": 1, "media_item_id": media_id, "payload": json.dumps({"artifact_id": artifact_id})}

        calls = []
        original_duration = ProcessHandler._duration
        original_render = ProcessHandler._render
        original_route_uploads = ProcessHandler._route_uploads
        try:
            ProcessHandler._duration = lambda _handler, _source: 600.0

            def fake_render(handler, _source, destination, start, length, title="", part=1, total_parts=1, options=None):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"rendered")
                calls.append((start, length, part, total_parts))

            ProcessHandler._render = fake_render
            ProcessHandler._route_uploads = lambda *_args, **_kwargs: None
            ProcessHandler(self.db, self.settings).run(job)
        finally:
            ProcessHandler._duration = original_duration
            ProcessHandler._render = original_render
            ProcessHandler._route_uploads = original_route_uploads

        self.assertEqual(2, len(calls))
        self.assertEqual((0.0, 315.0, 1, 2), calls[0])
        self.assertAlmostEqual(315.0, calls[1][0], places=2)
        self.assertAlmostEqual(285.0, calls[1][1], places=2)

    def test_account_split_disabled_forces_single_part(self):
        self.settings.split_enabled = True
        source = Path(self.temp.name) / "source.mp4"
        source.write_bytes(b"video")
        youtube = YouTubeService(self.db, self.settings)
        youtube.discover("nosplit0001", "UCnosplit", "No split")
        with self.db.connect() as connection:
            media_id = connection.execute(
                "SELECT id FROM media_items WHERE youtube_video_id='nosplit0001'").fetchone()["id"]
            artifact_id = connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path) VALUES (?,'downloaded',?)",
                (media_id, str(source))).lastrowid
            account_id = connection.execute(
                "INSERT INTO tiktok_accounts(name,split_enabled) VALUES ('No split',0)").lastrowid
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCnosplit')")
            route_id = connection.execute(
                "INSERT INTO channel_routes(name,account_id) VALUES ('R',?)", (account_id,)).lastrowid
            connection.execute(
                "INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)",
                (route_id, "UCnosplit", "https://youtube.com/@nosplit"))
            job = {"id": 1, "media_item_id": media_id, "payload": json.dumps({"artifact_id": artifact_id})}
        calls = []
        original_duration = ProcessHandler._duration
        original_render = ProcessHandler._render
        original_route_uploads = ProcessHandler._route_uploads
        try:
            ProcessHandler._duration = lambda _handler, _source: 1200.0

            def fake_render(handler, _source, destination, start, length, title="", part=1, total_parts=1, options=None):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"rendered")
                calls.append((start, length, part, total_parts))

            ProcessHandler._render = fake_render
            ProcessHandler._route_uploads = lambda *_args, **_kwargs: None
            ProcessHandler(self.db, self.settings).run(job)
        finally:
            ProcessHandler._duration = original_duration
            ProcessHandler._render = original_render
            ProcessHandler._route_uploads = original_route_uploads
        self.assertEqual([(0.0, None, 1, 1)], calls)

    def test_youtube_video_url_extracts_video_id(self):
        expected = "dQw4w9WgXcQ"
        for url in (
            f"https://www.youtube.com/watch?v={expected}",
            f"https://youtube.com/watch?v={expected}&t=42",
            f"https://youtu.be/{expected}?si=abc",
            f"https://www.youtube.com/shorts/{expected}",
            f"https://www.youtube.com/live/{expected}",
        ):
            self.assertEqual(expected, YouTubeService.video_id_from_url(url))
        with self.assertRaises(ValueError):
            YouTubeService.video_id_from_url("https://example.com/watch?v=dQw4w9WgXcQ")
        with self.assertRaises(ValueError):
            YouTubeService.video_id_from_url("https://www.youtube.com/watch?v=short")

    def test_queue_test_video_creates_download_job_for_account(self):
        with self.db.connect() as connection:
            account_id = connection.execute(
                "INSERT INTO tiktok_accounts(name) VALUES ('Test account')").lastrowid
        original = YouTubeService.resolve_video_url
        try:
            YouTubeService.resolve_video_url = lambda _self, _url: (
                "dQw4w9WgXcQ", "Test video title", "UCtestchannel")
            media_id = YouTubeService(self.db, self.settings).queue_test_video(
                "https://youtu.be/dQw4w9WgXcQ", account_id)
        finally:
            YouTubeService.resolve_video_url = original
        with self.db.connect() as connection:
            media = connection.execute(
                "SELECT * FROM media_items WHERE id=?", (media_id,)).fetchone()
            job = connection.execute(
                "SELECT * FROM jobs WHERE kind='download' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual("dQw4w9WgXcQ", media["youtube_video_id"])
        self.assertEqual("Test video title", media["title"])
        self.assertEqual(f"test_account_{account_id}", media["channel_id"])
        payload = json.loads(job["payload"])
        self.assertEqual(1, payload["is_test"])
        self.assertEqual(account_id, payload["test_account_id"])

    def test_test_video_routes_to_selected_account_without_upload_job(self):
        youtube = YouTubeService(self.db, self.settings)
        youtube.discover("test0000001", "UCtest", "Test route")
        with self.db.connect() as connection:
            media_id = connection.execute(
                "SELECT id FROM media_items WHERE youtube_video_id='test0000001'").fetchone()["id"]
            connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path,segment_index) VALUES (?,'rendered','test.mp4',1)",
                (media_id,))
            account_id = connection.execute(
                "INSERT INTO tiktok_accounts(name,caption_text) VALUES ('Test account','Caption')").lastrowid
        ProcessHandler(self.db, self.settings)._route_uploads(
            media_id, "Test route", account_id=account_id, is_test=True)
        with self.db.connect() as connection:
            task = connection.execute("SELECT * FROM upload_tasks").fetchone()
            upload_jobs = connection.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE kind='upload'").fetchone()["c"]
        self.assertEqual(account_id, task["account_id"])
        self.assertEqual(1, task["is_test"])
        self.assertEqual("test_ready", task["status"])
        self.assertEqual(0, upload_jobs)

    def test_upload_handler_never_posts_a_test_task(self):
        task_id, account_id = self._insert_upload_task()
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE upload_tasks SET is_test=1 WHERE id=?", (task_id,))
        calls = []
        original_start = UploadHandler._start_gpm_profile
        try:
            UploadHandler._start_gpm_profile = lambda *_args, **_kwargs: calls.append("start")
            UploadHandler(self.db, self.settings).run(
                {"payload": f'{{"task_id": {task_id}}}'})
        finally:
            UploadHandler._start_gpm_profile = original_start
        with self.db.connect() as connection:
            status = connection.execute(
                "SELECT status FROM upload_tasks WHERE id=?", (task_id,)).fetchone()["status"]
        self.assertEqual([], calls)
        self.assertEqual("test_ready", status)

    def test_youtube_channel_url_can_resolve_direct_channel_id_without_network(self):
        service = YouTubeService(self.db, self.settings)
        channel_id = "UC8YTaII3UJzSDRpt3JS7LpQ"
        resolved, title, url = service.resolve_channel_url(
            f"https://www.youtube.com/channel/{channel_id}/videos?view=0"
        )
        self.assertEqual(channel_id, resolved)
        self.assertEqual("", title)
        self.assertEqual(f"https://www.youtube.com/channel/{channel_id}/videos", url)
        with self.assertRaises(ValueError):
            service.resolve_channel_url("https://example.com/channel/test")

    def test_processed_video_routes_to_tiktok_account_from_its_layout(self):
        youtube = YouTubeService(self.db, self.settings)
        youtube.discover("route000001", "UCroute", "Route demo")
        with self.db.connect() as connection:
            media_id = connection.execute("SELECT id FROM media_items WHERE youtube_video_id='route000001'").fetchone()["id"]
            artifact_id = connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path) VALUES (?,'rendered','demo.mp4')", (media_id,)
            ).lastrowid
            account_id = connection.execute("INSERT INTO tiktok_accounts(name,endpoint) VALUES ('TikTok B','http://127.0.0.1:9222')").lastrowid
            route_id = connection.execute("INSERT INTO channel_routes(name,account_id) VALUES ('Layout B',?)", (account_id,)).lastrowid
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCroute')")
            connection.execute("INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)", (route_id, "UCroute", "https://youtube.com/@route"))
        ProcessHandler(self.db, self.settings)._route_uploads(media_id, "caption")
        with self.db.connect() as connection:
            task = connection.execute("SELECT account_id,artifact_id FROM upload_tasks").fetchone()
        self.assertEqual((account_id, artifact_id), (task["account_id"], task["artifact_id"]))

    def test_upload_caption_uses_youtube_title_index_and_hashtags(self):
        youtube = YouTubeService(self.db, self.settings)
        youtube.discover("parts000001", "UCparts", "Demo title")
        with self.db.connect() as connection:
            media_id = connection.execute("SELECT id FROM media_items WHERE youtube_video_id='parts000001'").fetchone()["id"]
            connection.execute("INSERT INTO artifacts(media_item_id,kind,path,segment_index) VALUES (?,'rendered','part1.mp4',1)", (media_id,))
            connection.execute("INSERT INTO artifacts(media_item_id,kind,path,segment_index) VALUES (?,'rendered','part2.mp4',2)", (media_id,))
            account_id = connection.execute("INSERT INTO tiktok_accounts(name,endpoint,hashtags) VALUES ('TikTok','http://127.0.0.1:9222','viral, #review')").lastrowid
            route_id = connection.execute("INSERT INTO channel_routes(name,account_id) VALUES ('Layout',?)", (account_id,)).lastrowid
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCparts')")
            connection.execute("INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)", (route_id, "UCparts", "https://youtube.com/@parts"))
        ProcessHandler(self.db, self.settings)._route_uploads(media_id, "Demo title")
        with self.db.connect() as connection:
            captions = [row["caption"] for row in connection.execute("SELECT caption FROM upload_tasks ORDER BY artifact_id")]
        self.assertEqual(
            ["Demo title (1)\n#viral #review", "Demo title (2)\n#viral #review"],
            captions,
        )

    def test_single_part_caption_is_title_without_index(self):
        with self.db.connect() as connection:
            connection.execute("INSERT INTO tiktok_accounts(name) VALUES ('TikTok')")
            account = connection.execute("SELECT * FROM tiktok_accounts").fetchone()
        handler = ProcessHandler(self.db, self.settings)
        self.assertEqual(
            "Demo title",
            handler._upload_caption("Demo title", 1, account, total_parts=1),
        )

    def test_fixed_account_caption_uses_caption_text_and_part_suffix(self):
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO tiktok_accounts(name,caption_mode,caption_text,hashtags)
                   VALUES ('TikTok','fixed','Caption cố định','#ignored')"""
            )
            account = connection.execute("SELECT * FROM tiktok_accounts").fetchone()
        handler = ProcessHandler(self.db, self.settings)
        self.assertEqual(
            "Caption cố định (2)",
            handler._upload_caption("Demo title", 2, account, total_parts=2),
        )

    def test_ai_account_caption_uses_generated_text_and_falls_back(self):
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO tiktok_accounts(name,caption_mode,caption_text)
                   VALUES ('TikTok','ai','Caption dự phòng')"""
            )
            account = connection.execute("SELECT * FROM tiktok_accounts").fetchone()
        handler = ProcessHandler(self.db, self.settings)
        original = ProcessHandler._generate_caption
        try:
            ProcessHandler._generate_caption = lambda _self, title, part=1, account=None, total_parts=1: f"AI caption cho {title}"
            self.assertEqual(
                "AI caption cho Demo title (3)",
                handler._upload_caption("Demo title", 3, account, total_parts=3),
            )

            def boom(*_args, **_kwargs):
                raise RuntimeError("AI offline")

            ProcessHandler._generate_caption = boom
            self.assertEqual(
                "Caption dự phòng",
                handler._upload_caption("Demo title", 1, account),
            )
        finally:
            ProcessHandler._generate_caption = original

    def test_processor_output_group_uses_mapped_tiktok_channel_name(self):
        with self.db.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCfolder')")
            account_id = connection.execute("INSERT INTO tiktok_accounts(name) VALUES ('TikTok: Main/01')").lastrowid
            route_id = connection.execute("INSERT INTO channel_routes(name,account_id) VALUES ('Layout',?)", (account_id,)).lastrowid
            connection.execute("INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)", (route_id, 'UCfolder', 'https://youtube.com/@folder'))
        handler = ProcessHandler(self.db, self.settings)
        self.assertEqual("TikTok_ Main_01", handler._output_group("UCfolder"))
        self.assertEqual("UCunmapped", handler._output_group("UCunmapped"))

    def test_channel_can_be_reassigned_and_deleted(self):
        with self.db.connect() as connection:
            first = connection.execute(
                "INSERT INTO tiktok_accounts(name) VALUES ('First')").lastrowid
            second = connection.execute(
                "INSERT INTO tiktok_accounts(name) VALUES ('Second')").lastrowid
        repository = UIRepository(self.db)
        repository.save_channel_route(
            "UCedit", "Edit channel", "https://youtube.com/@edit", first)
        repository.update_channel_account("UCedit", second)
        with self.db.connect() as connection:
            row = connection.execute(
                """SELECT r.account_id FROM channel_route_sources s
                   JOIN channel_routes r ON r.id=s.route_id
                   WHERE s.channel_id='UCedit'"""
            ).fetchone()
        self.assertEqual(second, row["account_id"])
        repository.delete_channel("UCedit")
        with self.db.connect() as connection:
            channel = connection.execute(
                "SELECT 1 FROM channels WHERE channel_id='UCedit'").fetchone()
            source = connection.execute(
                "SELECT 1 FROM channel_route_sources WHERE channel_id='UCedit'").fetchone()
        self.assertIsNone(channel)
        self.assertIsNone(source)

    def test_pipeline_runs_downloads_in_parallel(self):
        self.settings.max_download_workers = 2
        self.settings.max_process_workers = 1
        self.settings.max_upload_workers = 1
        media_ids = []
        with self.db.connect() as connection:
            for index in range(4):
                media_ids.append(connection.execute(
                    """INSERT INTO media_items(youtube_video_id,channel_id,title)
                       VALUES (?, 'UCparallel', ?)""",
                    (f"parallel{index:03d}", f"Parallel {index}"),
                ).lastrowid)
        for index, media_id in enumerate(media_ids):
            self.db.enqueue(
                "download", media_id, f"parallel-download-{index}")
        state = {"active": 0, "max": 0}
        state_lock = threading.Lock()

        class FakeDownload:
            def run(self, _job):
                with state_lock:
                    state["active"] += 1
                    state["max"] = max(state["max"], state["active"])
                time.sleep(0.3)
                with state_lock:
                    state["active"] -= 1

        orchestrator = PipelineOrchestrator(self.db, self.settings)
        orchestrator.handlers["download"] = FakeDownload()
        try:
            orchestrator.start()
            deadline = time.time() + 6
            while time.time() < deadline:
                with self.db.connect() as connection:
                    completed = connection.execute(
                        "SELECT COUNT(*) AS c FROM jobs WHERE kind='download' AND status='completed'"
                    ).fetchone()["c"]
                if completed >= 4:
                    break
                time.sleep(0.05)
        finally:
            orchestrator.stop()
        self.assertEqual(4, completed)
        self.assertEqual(2, state["max"])

    def test_pipeline_logs_include_video_and_channel(self):
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO channels(channel_id,title) VALUES ('UClog','Kênh Log')")
            media_id = connection.execute(
                """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url)
                   VALUES ('logvideo001','UClog','Video Log','https://www.youtube.com/watch?v=logvideo001')"""
            ).lastrowid
        job_id = self.db.enqueue("download", media_id, "download:logvideo001")
        state = {"done": False}

        class FakeDownload:
            def run(self, _job):
                state["done"] = True

        orchestrator = PipelineOrchestrator(self.db, self.settings)
        orchestrator.handlers["download"] = FakeDownload()
        try:
            orchestrator.start()
            deadline = time.time() + 4
            while time.time() < deadline and not state["done"]:
                time.sleep(0.05)
            time.sleep(0.05)
        finally:
            orchestrator.stop()
        with self.db.connect() as connection:
            messages = [
                row["message"] for row in connection.execute(
                    "SELECT message FROM job_events WHERE job_id=? ORDER BY id",
                    (job_id,))
            ]
        self.assertTrue(any("Bắt đầu tải video: Video Log" in m for m in messages))
        self.assertTrue(any("Kênh: Kênh Log" in m for m in messages))
        self.assertTrue(any(
            "Link: https://www.youtube.com/watch?v=logvideo001" in m
            for m in messages))
        self.assertTrue(any("Hoàn tất tải video: Video Log" in m for m in messages))

    def test_pipeline_activity_estimates_remaining_time(self):
        def stamp(seconds_ago: int) -> str:
            return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()

        with self.db.connect() as connection:
            media_id = connection.execute(
                "INSERT INTO media_items(youtube_video_id,channel_id,title) VALUES ('eta00000001','UCx','ETA')"
            ).lastrowid
            completed_id = connection.execute(
                """INSERT INTO jobs(kind,media_item_id,idempotency_key,status)
                   VALUES ('download',?,'eta-completed','completed')""",
                (media_id,),
            ).lastrowid
            connection.execute(
                "INSERT INTO job_events(job_id,level,message,created_at) VALUES (?, 'INFO', 'Bắt đầu download', ?)",
                (completed_id, stamp(200)))
            connection.execute(
                "INSERT INTO job_events(job_id,level,message,created_at) VALUES (?, 'INFO', 'Hoàn tất', ?)",
                (completed_id, stamp(100)))
            running_id = connection.execute(
                """INSERT INTO jobs(kind,media_item_id,idempotency_key,status)
                   VALUES ('download',?,'eta-running','leased')""",
                (media_id,),
            ).lastrowid
            connection.execute(
                "INSERT INTO job_events(job_id,level,message,created_at) VALUES (?, 'INFO', 'Bắt đầu download', ?)",
                (running_id, stamp(20)))
            second_running = connection.execute(
                """INSERT INTO jobs(kind,media_item_id,idempotency_key,status)
                   VALUES ('download',?,'eta-running-2','leased')""",
                (media_id,),
            ).lastrowid
            connection.execute(
                "INSERT INTO job_events(job_id,level,message,created_at) VALUES (?, 'INFO', 'Bắt đầu download', ?)",
                (second_running, stamp(10)))
        activity = UIRepository(self.db).pipeline_activity()
        download = next(
            stage for stage in activity["stages"] if stage["kind"] == "download")
        self.assertEqual("running", download["state"])
        self.assertIsNotNone(download["eta_seconds"])
        self.assertGreaterEqual(download["eta_seconds"], 70)
        self.assertLessEqual(download["eta_seconds"], 90)
        self.assertEqual(2, len(download["tasks"]))
        self.assertEqual("ETA", download["tasks"][0]["title"])

    def test_pipeline_activity_reports_live_stage_states(self):
        with self.db.connect() as connection:
            media_id = connection.execute(
                "INSERT INTO media_items(youtube_video_id,channel_id,title) VALUES ('activity001','UCx','Video')"
            ).lastrowid
            artifact_id = connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path) VALUES (?,'rendered','x.mp4')",
                (media_id,),
            ).lastrowid
            connection.execute(
                "INSERT INTO jobs(kind,media_item_id,idempotency_key,status) VALUES ('download',?,'activity-d','completed')",
                (media_id,),
            )
            connection.execute(
                "INSERT INTO jobs(kind,media_item_id,idempotency_key,status) VALUES ('process',?,'activity-p','leased')",
                (media_id,),
            )
            connection.execute(
                """INSERT INTO jobs(kind,media_item_id,idempotency_key,status,error_message)
                   VALUES ('upload',?,'activity-u','failed','upload boom')""",
                (media_id,),
            )
            connection.execute(
                "INSERT INTO job_events(job_id,level,message) VALUES (2,'INFO','Đang xử lý')")
            connection.execute(
                "INSERT INTO upload_tasks(media_item_id,artifact_id,account_id,caption) VALUES (?,?,NULL,'x')",
                (media_id, artifact_id),
            )
        activity = UIRepository(self.db).pipeline_activity()
        stages = {stage["kind"]: stage for stage in activity["stages"]}
        self.assertEqual("success", stages["download"]["state"])
        self.assertEqual("running", stages["process"]["state"])
        self.assertEqual("failed", stages["upload"]["state"])
        self.assertIn("upload boom", stages["upload"]["detail"])
        self.assertEqual(1, activity["pending_uploads"])
        self.assertTrue(activity["events"])

    def test_pipeline_report_exports_excel_rows(self):
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO channels(channel_id,title,source_url) VALUES ('UCreport','YouTube A','https://youtube.com/@a')"
            )
            media_id = connection.execute(
                """INSERT INTO media_items(youtube_video_id,channel_id,title,source_url)
                   VALUES ('report00001','UCreport','Report title','https://www.youtube.com/watch?v=report00001')"""
            ).lastrowid
            artifact_id = connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path,segment_index) VALUES (?,'rendered','001.mp4',1)",
                (media_id,),
            ).lastrowid
            account_id = connection.execute("INSERT INTO tiktok_accounts(name) VALUES ('TikTok A')").lastrowid
            route_id = connection.execute("INSERT INTO channel_routes(name,account_id) VALUES ('Layout A',?)", (account_id,)).lastrowid
            connection.execute(
                "INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)",
                (route_id, "UCreport", "https://youtube.com/@a"),
            )
            task_id = connection.execute(
                """INSERT INTO upload_tasks(media_item_id,artifact_id,account_id,caption,status)
                   VALUES (?,?,?,'#report','completed')""",
                (media_id, artifact_id, account_id),
            ).lastrowid
            job_id = connection.execute(
                """INSERT INTO jobs(kind,media_item_id,idempotency_key,payload,status)
                   VALUES ('upload',?,?,?, 'completed')""",
                (media_id, f"upload:{task_id}", f'{{"task_id": {task_id}}}'),
            ).lastrowid
            connection.execute("INSERT INTO job_events(job_id,level,message) VALUES (?,'INFO','Hoàn tất')", (job_id,))
        rows = build_pipeline_report_rows(self.db)
        self.assertEqual(1, len(rows))
        self.assertEqual("YouTube A", rows[0].youtube_channel)
        self.assertEqual("https://youtube.com/@a", rows[0].youtube_channel_url)
        self.assertEqual("TikTok A", rows[0].tiktok_channel)
        self.assertEqual("Report title", rows[0].title)
        self.assertEqual("001.mp4", rows[0].rendered_video_path)
        self.assertEqual("Có", rows[0].has_tiktok_upload)
        self.assertEqual("Có", rows[0].upload_success)
        destination = Path(self.temp.name) / "report.xlsx"
        export_pipeline_report(self.db, destination)
        with ZipFile(destination) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            rels = workbook.read("xl/worksheets/_rels/sheet1.xml.rels").decode("utf-8")
        self.assertIn("Kênh YouTube", sheet)
        self.assertIn("Path video đã tách", sheet)
        self.assertIn('hyperlink ref="A2"', sheet)
        self.assertIn("Report title", sheet)
        self.assertIn("001.mp4", sheet)
        self.assertIn("TikTok A", sheet)
        self.assertIn("https://youtube.com/@a", rels)

    def test_caption_overlay_is_white_plate_with_black_text_on_top_of_video(self):
        self.settings.overlay_text_enabled = True
        self.settings.overlay_font_size = 64
        destination = Path(self.temp.name) / "001.mp4"
        video_box = services.VideoBox(1080, 1920, 1080, 608, 0, 656)
        handler = ProcessHandler(self.db, self.settings)
        text = handler.caption_overlay_text("Tiêu đề tiếng Việt", 2, 3)
        self.assertEqual("Tiêu đề tiếng Việt (2)", text)
        image_path, x, y = handler.caption_overlay_image(destination, text, "9:16", video_box)
        self.assertTrue(image_path.is_file())
        self.assertGreater(image_path.stat().st_size, 0)
        self.assertGreaterEqual(x, 0)
        from PIL import Image
        with Image.open(image_path) as plate:
            plate_height = plate.height
        self.assertEqual(max(307, 656 - plate_height - 8), y)
        self.assertLess(y, 656)
        filters, files = handler.overlay_filters(destination, "Tiêu đề tiếng Việt", 2, 3, video_box)
        self.assertEqual(1, len(filters))
        self.assertIn("fontcolor=black", filters[0])
        self.assertIn("fontsize=64", filters[0])
        self.assertIn("box=1", filters[0])
        self.assertIn("boxcolor=white", filters[0])
        self.assertIn("boxborderw=8", filters[0])
        self.assertIn(r"y=max(307\,656-text_h-8)", filters[0])
        self.assertEqual("Tiêu đề tiếng Việt (2)", files[0].read_text(encoding="utf-8"))

    def test_scaled_video_box_tracks_original_video_inside_vertical_canvas(self):
        box = ProcessHandler.scaled_video_box((1920, 1080), "9:16", 100)
        self.assertEqual((1080, 1920), (box.canvas_width, box.canvas_height))
        self.assertEqual(1080, box.video_width)
        self.assertEqual(606, box.video_height)
        self.assertEqual(657, box.video_y)
        self.assertEqual(1263, box.video_bottom)

    def test_existing_output_is_rerendered_when_overlay_enabled(self):
        self.settings.overlay_text_enabled = True
        source = Path(self.temp.name) / "source.mp4"
        destination = Path(self.temp.name) / "001.mp4"
        source.write_bytes(b"source")
        destination.write_bytes(b"old")
        calls = []
        original = services.subprocess.run
        try:
            services.subprocess.run = lambda command, **kwargs: calls.append(command)
            ProcessHandler(self.db, self.settings)._render(source, destination, 0, 60, "Title", 2, 2)
        finally:
            services.subprocess.run = original
        ffmpeg_calls = [command for command in calls if any("caption.png" in str(item) for item in command)]
        self.assertEqual(1, len(ffmpeg_calls))
        command_text = ",".join(ffmpeg_calls[0])
        self.assertIn("-filter_complex", command_text)
        self.assertIn(".caption.png", command_text)
        self.assertIn("overlay=", command_text)
        self.assertFalse(any(".caption.png" in str(item) for item in Path(self.temp.name).glob("*")))

    def test_existing_output_without_filters_is_skipped(self):
        source = Path(self.temp.name) / "source.mp4"
        destination = Path(self.temp.name) / "001.mp4"
        source.write_bytes(b"source")
        destination.write_bytes(b"old")
        calls = []
        original = services.subprocess.run
        try:
            services.subprocess.run = lambda command, **kwargs: calls.append(command)
            ProcessHandler(self.db, self.settings)._render(
                source, destination, 0, None, "Title", 1, 1,
                services.ProcessingOptions(60, "original", 100, 1.0, ""),
            )
        finally:
            services.subprocess.run = original
        self.assertEqual([], calls)

    def test_account_processing_options_drive_speed_zoom_and_output(self):
        self.settings.video_zoom_percent = 91
        self.settings.video_speed = 1.75
        with self.db.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCoptions')")
            account_id = connection.execute(
                """INSERT INTO tiktok_accounts(
                     name,speed,zoom_width_percent,zoom_height_percent,caption_mode,caption_text,
                     output_dir,notification_sound,split_enabled,overlay_enabled,
                     background_path,publish_enabled,ai_api_url,ai_model)
                   VALUES ('TikTok',1.5,82,70,'ai','fixed caption','D:/out','bell.wav',
                           1,1,'account-bg.mp4',1,'https://ai.example/v1/chat','gpt-test')"""
            ).lastrowid
            route_id = connection.execute("INSERT INTO channel_routes(name,account_id) VALUES ('Layout',?)", (account_id,)).lastrowid
            connection.execute(
                """INSERT INTO channel_route_sources(route_id,channel_id,source_url,segment_seconds,zoom_percent,speed,background_path)
                VALUES (?,?,?,?,?,?,?)""",
                (route_id, "UCoptions", "https://youtube.com/@options", 45, 82, 1.5, "bg.mp4"),
            )
        options = ProcessHandler(self.db, self.settings)._processing_options("UCoptions")
        self.assertEqual("9:16", options.aspect_ratio)
        self.assertEqual(1.5, options.speed)
        self.assertEqual((82, 70), options.zoom_pair())
        self.assertEqual("account-bg.mp4", options.background_path)
        self.assertEqual(account_id, options.account_id)
        self.assertEqual("D:/out", options.output_dir)
        self.assertEqual("ai", options.caption_mode)
        self.assertEqual("fixed caption", options.caption_text)
        self.assertEqual("bell.wav", options.notification_sound)
        self.assertTrue(options.split_enabled)
        self.assertTrue(options.overlay_enabled)
        self.assertEqual(
            Path("D:/out") / "Demo title_2.mp4",
            ProcessHandler.output_path(Path(options.output_dir), "Demo title", 2),
        )

    def test_inactive_account_still_drives_processing_but_not_uploads(self):
        with self.db.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCpaused')")
            account_id = connection.execute(
                """INSERT INTO tiktok_accounts(name,active,speed,output_dir)
                   VALUES ('Paused',0,1.4,'D:/paused-out')"""
            ).lastrowid
            route_id = connection.execute(
                "INSERT INTO channel_routes(name,account_id) VALUES ('R',?)", (account_id,)
            ).lastrowid
            connection.execute(
                "INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)",
                (route_id, "UCpaused", "https://youtube.com/@paused"))
        options = ProcessHandler(self.db, self.settings)._processing_options("UCpaused")
        self.assertEqual(account_id, options.account_id)
        self.assertEqual(1.4, options.speed)
        self.assertEqual("D:/paused-out", options.output_dir)
        with self.db.connect() as connection:
            media_id = connection.execute(
                "INSERT INTO media_items(youtube_video_id,channel_id,title) VALUES ('paused00001','UCpaused','Paused')"
            ).lastrowid
            connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path,segment_index) VALUES (?,'rendered','paused.mp4',1)",
                (media_id,))
        ProcessHandler(self.db, self.settings)._route_uploads(media_id, "Paused")
        with self.db.connect() as connection:
            tasks = connection.execute(
                "SELECT COUNT(*) AS c FROM upload_tasks").fetchone()["c"]
        self.assertEqual(0, tasks)

    def test_null_account_flags_inherit_global_settings(self):
        self.settings.split_enabled = False
        self.settings.overlay_text_enabled = True
        with self.db.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCinherit')")
            account_id = connection.execute(
                "INSERT INTO tiktok_accounts(name) VALUES ('Inherit')").lastrowid
            route_id = connection.execute(
                "INSERT INTO channel_routes(name,account_id) VALUES ('R',?)", (account_id,)).lastrowid
            connection.execute(
                "INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)",
                (route_id, "UCinherit", "https://youtube.com/@inherit"))
        options = ProcessHandler(self.db, self.settings)._processing_options("UCinherit")
        self.assertFalse(options.split_enabled)
        self.assertTrue(options.overlay_enabled)

    def test_account_ai_config_overrides_global_and_blank_inherits(self):
        self.settings.ai_api_url = "https://global.example/v1/chat"
        self.settings.ai_api_key = "global-key"
        self.settings.ai_model = "global-model"
        with self.db.connect() as connection:
            account_id = connection.execute(
                """INSERT INTO tiktok_accounts(name,ai_api_url,ai_api_key,ai_model)
                   VALUES ('AI','https://account.example/v1/chat','','account-model')"""
            ).lastrowid
            account = connection.execute(
                "SELECT * FROM tiktok_accounts WHERE id=?", (account_id,)).fetchone()
        handler = ProcessHandler(self.db, self.settings)
        self.assertEqual("https://account.example/v1/chat", handler._ai_value(account, "ai_api_url"))
        self.assertEqual("global-key", handler._ai_value(account, "ai_api_key"))
        self.assertEqual("account-model", handler._ai_value(account, "ai_model"))

    def test_zoom_out_and_speed_are_applied_to_ffmpeg_command(self):
        source = Path(self.temp.name) / "source.mp4"
        destination = Path(self.temp.name) / "001.mp4"
        source.write_bytes(b"source")
        calls = []
        original = services.subprocess.run
        try:
            services.subprocess.run = lambda command, **kwargs: calls.append(command)
            ProcessHandler(self.db, self.settings)._render(
                source,
                destination,
                0,
                90,
                "Title",
                1,
                1,
                services.ProcessingOptions(60, "9:16", 80, 1.25, ""),
            )
        finally:
            services.subprocess.run = original
        command = calls[0]
        command_text = ",".join(command)
        self.assertIn("scale=864:1536:force_original_aspect_ratio=decrease", command_text)
        self.assertIn("setpts=PTS/1.25", command_text)
        self.assertIn("atempo=1.25", command_text)
        self.assertEqual(1, command.count("-vf"))

    def test_trim_duration_is_applied_to_source_before_speed_filter(self):
        source = Path(self.temp.name) / "source.mp4"
        destination = Path(self.temp.name) / "001.mp4"
        source.write_bytes(b"source")
        calls = []
        original = services.subprocess.run
        try:
            services.subprocess.run = lambda command, **kwargs: calls.append(command)
            ProcessHandler(self.db, self.settings)._render(
                source,
                destination,
                0,
                33,
                "Title",
                1,
                1,
                services.ProcessingOptions(30, "9:16", 100, 1.1, ""),
            )
        finally:
            services.subprocess.run = original
        command = calls[0]
        self.assertLess(command.index("-t"), command.index("-i"))
        self.assertIn("setpts=PTS/1.1", ",".join(command))

    def test_application_does_not_start_without_saved_routes(self):
        app = Application(self.settings)
        self.assertFalse(app.is_running)
        with self.assertRaisesRegex(RuntimeError, "Chưa có layout"):
            app.start()
        self.assertFalse(app.is_running)
        app.shutdown()

    def test_application_start_forces_websub_resubscribe(self):
        self.settings.public_domain = "https://example.com"
        self.settings.tracking_mode = "websub"
        app = Application(self.settings)
        with app.database.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id,active,subscription_status) VALUES ('UCforce',1,'subscribed')")
            account_id = connection.execute("INSERT INTO tiktok_accounts(name) VALUES ('TikTok')").lastrowid
            connection.execute("INSERT INTO channel_routes(name,account_id,active) VALUES ('Layout',?,1)", (account_id,))

        class FakeWebSub:
            def __init__(self):
                self.calls = []

            def start(self):
                self.calls.append(("start", None))

            def renew_due(self, force=False):
                self.calls.append(("renew_due", force))
                return 1

            def stop(self):
                self.calls.append(("stop", None))

        class FakePipeline:
            def __init__(self):
                self.pause_event = type("Pause", (), {"is_set": lambda _self: False})()
                self.thread = type("Thread", (), {"is_alive": lambda _self: True})()

            def start(self):
                pass

            def stop(self):
                pass

        fake_websub = FakeWebSub()
        app.websub = fake_websub
        app.pipeline = FakePipeline()
        app.start()
        deadline = time.time() + 2
        while ("renew_due", True) not in fake_websub.calls and time.time() < deadline:
            time.sleep(0.01)
        self.assertIn(("start", None), fake_websub.calls)
        self.assertIn(("renew_due", True), fake_websub.calls)
        app.shutdown()

    def test_application_does_not_spawn_duplicate_websub_subscribe_threads(self):
        self.settings.public_domain = "https://example.com"
        self.settings.tracking_mode = "websub"
        app = Application(self.settings)
        with app.database.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id,active,subscription_status) VALUES ('UCforce',1,'subscribed')")
            account_id = connection.execute("INSERT INTO tiktok_accounts(name) VALUES ('TikTok')").lastrowid
            connection.execute("INSERT INTO channel_routes(name,account_id,active) VALUES ('Layout',?,1)", (account_id,))

        release = threading.Event()

        class FakeWebSub:
            def __init__(self):
                self.renew_calls = 0
                self.force_calls = 0

            def start(self):
                pass

            def renew_due(self, force=False):
                self.renew_calls += 1
                if force:
                    self.force_calls += 1
                release.wait(1)
                return 1

            def stop(self):
                pass

        class FakePipeline:
            def __init__(self):
                self.pause_event = type("Pause", (), {"is_set": lambda _self: False})()
                self.thread = type("Thread", (), {"is_alive": lambda _self: True})()

            def start(self):
                pass

            def stop(self):
                pass

        app.websub = FakeWebSub()
        app.pipeline = FakePipeline()
        try:
            app.start()
            app.start()
            time.sleep(0.05)
            self.assertEqual(1, app.websub.force_calls)
        finally:
            release.set()
            app.shutdown()

    def test_gpm_cdp_url_is_extracted_from_common_api_shapes(self):
        self.assertEqual(
            "http://127.0.0.1:34567",
            GpmLoginClient._extract_cdp_url({"success": True, "data": {"remote_debugging_address": "127.0.0.1:34567"}}),
        )
        self.assertEqual(
            "http://127.0.0.1:45678",
            GpmLoginClient._extract_cdp_url({"data": {"remote_debugging_port": 45678}}),
        )
        self.assertEqual(
            "http://127.0.0.1:56789",
            GpmLoginClient._extract_cdp_url({"data": {"browserWSEndpoint": "ws://127.0.0.1:56789/devtools/browser/demo"}}),
        )
        self.assertEqual(
            "http://127.0.0.1:40444",
            GpmLoginClient._extract_cdp_url({"data": {"websocket_debugging_url": "ws://127.0.0.1:40444/devtools/browser/abc-123"}}),
        )
        self.assertEqual(
            "ws://10.0.0.5:40444/devtools/browser/abc-123",
            GpmLoginClient._extract_cdp_url({"data": {"websocket_debugging_url": "ws://10.0.0.5:40444/devtools/browser/abc-123"}}),
        )

    def test_gpm_profile_in_use_can_reuse_returned_or_running_cdp(self):
        client = GpmLoginClient(DEFAULT_GPM_API_URL)
        original_get_json = client._get_json
        original_find = GpmLoginClient.find_running_profile_cdp
        original_wait = client._wait_for_cdp_endpoint
        try:
            client._wait_for_cdp_endpoint = lambda cdp_url, timeout=60: cdp_url
            client._get_json = lambda _path: {"success": False, "message": "ProfileInUse", "data": {"remote_debugging_port": 40001}}
            self.assertEqual("http://127.0.0.1:40001", client.start_profile("profile-1"))

            client._get_json = lambda _path: {"success": False, "message": "ProfileInUse"}
            GpmLoginClient.find_running_profile_cdp = classmethod(lambda cls, profile_id: f"http://127.0.0.1:40002" if profile_id == "profile-2" else "")
            self.assertEqual("http://127.0.0.1:40002", client.start_profile("profile-2"))
        finally:
            client._get_json = original_get_json
            client._wait_for_cdp_endpoint = original_wait
            GpmLoginClient.find_running_profile_cdp = original_find

    def test_gpm_profile_in_use_can_be_reclaimed_by_stop_then_start(self):
        client = GpmLoginClient(DEFAULT_GPM_API_URL, reclaim_profile_in_use=True, reclaim_wait_seconds=0)
        original_get_json = client._get_json
        original_find = GpmLoginClient.find_running_profile_cdp
        original_wait = client._wait_for_cdp_endpoint
        calls = []
        try:
            GpmLoginClient.find_running_profile_cdp = classmethod(lambda cls, profile_id: "")
            client._wait_for_cdp_endpoint = lambda cdp_url, timeout=60: cdp_url

            def fake_get_json(path):
                calls.append(path)
                if path == "/profiles/start/profile-3" and calls.count(path) == 1:
                    return {"success": False, "message": "ProfileInUse"}
                if path == "/profiles/stop/profile-3":
                    return {"success": False, "message": "OK"}
                if path == "/profiles/start/profile-3":
                    return {"success": True, "data": {"remote_debugging_port": 40003}}
                raise AssertionError(path)

            client._get_json = fake_get_json
            self.assertEqual("http://127.0.0.1:40003", client.start_profile("profile-3"))
        finally:
            client._get_json = original_get_json
            client._wait_for_cdp_endpoint = original_wait
            GpmLoginClient.find_running_profile_cdp = original_find
        self.assertEqual(
            ["/profiles/start/profile-3", "/profiles/stop/profile-3", "/profiles/start/profile-3"],
            calls,
        )

    def test_gpm_stale_returned_cdp_can_be_reclaimed_by_stop_then_start(self):
        events = []
        client = GpmLoginClient(
            DEFAULT_GPM_API_URL,
            reclaim_profile_in_use=True,
            reclaim_wait_seconds=0,
            event_callback=events.append,
        )
        original_get_json = client._get_json
        original_wait = client._wait_for_cdp_endpoint
        calls = []
        wait_calls = []
        try:
            def fake_get_json(path):
                calls.append(path)
                if path == "/profiles/start/profile-4" and calls.count(path) == 1:
                    return {"success": True, "data": {"remote_debugging_port": 57067}}
                if path == "/profiles/stop/profile-4":
                    return {"success": False, "message": "OK"}
                if path == "/profiles/start/profile-4":
                    return {"success": True, "data": {"remote_debugging_port": 57068}}
                raise AssertionError(path)

            def fake_wait(cdp_url, timeout=60):
                wait_calls.append(cdp_url)
                if cdp_url.endswith(":57067"):
                    raise ManualAction("stale cdp")
                return cdp_url

            client._get_json = fake_get_json
            client._wait_for_cdp_endpoint = fake_wait
            self.assertEqual("http://127.0.0.1:57068", client.start_profile("profile-4"))
        finally:
            client._get_json = original_get_json
            client._wait_for_cdp_endpoint = original_wait
        self.assertEqual(
            ["/profiles/start/profile-4", "/profiles/stop/profile-4", "/profiles/start/profile-4"],
            calls,
        )
        self.assertEqual(["http://127.0.0.1:57067", "http://127.0.0.1:57068"], wait_calls)
        self.assertTrue(any("CDP http://127.0.0.1:57067 chưa sẵn sàng" in item for item in events))

    def test_gpm_default_api_url_matches_current_local_api_docs(self):
        self.assertEqual("http://127.0.0.1:9495/api/v1", DEFAULT_GPM_API_URL)

    def test_gpm_upload_mode_starts_profile_then_uses_playwright_cdp(self):
        self.settings.tiktok_publish_enabled = True
        with self.db.connect() as connection:
            media_id = connection.execute(
                "INSERT INTO media_items(youtube_video_id,channel_id,title) VALUES ('gpm00000001','UCgpm','GPM demo')"
            ).lastrowid
            artifact_id = connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path) VALUES (?,'rendered','demo.mp4')", (media_id,)
            ).lastrowid
            account_id = connection.execute(
                "INSERT INTO tiktok_accounts(name,mode,gpm_profile_id) VALUES ('TikTok GPM','gpm','profile-1')"
            ).lastrowid
            task_id = connection.execute(
                "INSERT INTO upload_tasks(media_item_id,artifact_id,account_id,caption) VALUES (?,?,?,'Caption')",
                (media_id, artifact_id, account_id),
            ).lastrowid
        job = {"payload": f'{{"task_id": {task_id}}}'}
        calls = []
        original_start = UploadHandler._start_gpm_profile
        original_upload = UploadHandler._playwright_upload
        try:
            UploadHandler._start_gpm_profile = lambda handler, profile_id, account_id, job_id=None: calls.append(("start", profile_id, account_id)) or "http://127.0.0.1:34567"
            UploadHandler._playwright_upload = staticmethod(lambda path, caption, cdp_url, log=None: calls.append(("upload", str(path), caption, cdp_url)))
            UploadHandler(self.db, self.settings).run(job)
        finally:
            UploadHandler._start_gpm_profile = original_start
            UploadHandler._playwright_upload = original_upload
        self.assertEqual(("start", "profile-1", account_id), calls[0])
        self.assertEqual(("upload", "demo.mp4", "Caption", "http://127.0.0.1:34567"), calls[1])
        with self.db.connect() as connection:
            task = connection.execute("SELECT status FROM upload_tasks WHERE id=?", (task_id,)).fetchone()
        self.assertEqual("completed", task["status"])

    def test_manual_tiktok_upload_uses_layout_config_and_logs(self):
        video = Path(self.temp.name) / "manual.mp4"
        video.write_bytes(b"demo")
        with self.db.connect() as connection:
            account_id = connection.execute(
                "INSERT INTO tiktok_accounts(name,mode,gpm_profile_id,hashtags) VALUES ('TikTok Manual','gpm','profile-manual','viral, #review')"
            ).lastrowid
        calls = []
        original_start = UploadHandler._start_gpm_profile
        original_upload = UploadHandler._playwright_upload
        try:
            UploadHandler._start_gpm_profile = lambda handler, profile_id, account_id, job_id=None: calls.append(("start", profile_id, account_id)) or "http://127.0.0.1:45678"
            UploadHandler._playwright_upload = staticmethod(lambda path, caption, cdp_url, log=None: calls.append(("upload", str(path), caption, cdp_url)) or (log and log("TikTok: fake upload ok")))
            UploadHandler(self.db, self.settings).manual_upload_file(
                video,
                "viral, #review",
                "gpm",
                gpm_profile_id="profile-manual",
                account_id=account_id,
            )
        finally:
            UploadHandler._start_gpm_profile = original_start
            UploadHandler._playwright_upload = original_upload
        self.assertEqual(("start", "profile-manual", account_id), calls[0])
        self.assertEqual(("upload", str(video), "#viral #review", "http://127.0.0.1:45678"), calls[1])
        with self.db.connect() as connection:
            account = connection.execute("SELECT posts_today,status FROM tiktok_accounts WHERE id=?", (account_id,)).fetchone()
            events = [row["message"] for row in connection.execute("SELECT message FROM job_events ORDER BY id")]
        self.assertEqual(1, account["posts_today"])
        self.assertEqual("idle", account["status"])
        self.assertTrue(any("TikTok manual upload: bắt đầu file" in item for item in events))
        self.assertTrue(any("TikTok: fake upload ok" in item for item in events))
        self.assertTrue(any("TikTok manual upload: hoàn tất" in item for item in events))

    def _insert_upload_task(self, daily_limit=3, posts_today=0, counter_date=None):
        with self.db.connect() as connection:
            media_id = connection.execute(
                "INSERT INTO media_items(youtube_video_id,channel_id,title) VALUES ('upl00000001','UCup','Upload demo')"
            ).lastrowid
            artifact_id = connection.execute(
                "INSERT INTO artifacts(media_item_id,kind,path) VALUES (?,'rendered','demo.mp4')", (media_id,)
            ).lastrowid
            account_id = connection.execute(
                """INSERT INTO tiktok_accounts(name,mode,endpoint,daily_limit,posts_today,counter_date)
                   VALUES ('TikTok','cdp','http://127.0.0.1:9222',?,?,?)""",
                (daily_limit, posts_today, counter_date),
            ).lastrowid
            task_id = connection.execute(
                "INSERT INTO upload_tasks(media_item_id,artifact_id,account_id,caption) VALUES (?,?,?,'Caption')",
                (media_id, artifact_id, account_id),
            ).lastrowid
        return task_id, account_id

    def test_zero_daily_limit_means_unlimited_uploads(self):
        self.settings.tiktok_publish_enabled = True
        task_id, account_id = self._insert_upload_task(daily_limit=0, posts_today=99, counter_date=None)
        original = UploadHandler._playwright_upload
        original_start = UploadHandler._start_gpm_profile
        try:
            UploadHandler._start_gpm_profile = lambda handler, profile_id, account_id, job_id=None: "http://127.0.0.1:45678"
            UploadHandler._playwright_upload = staticmethod(lambda path, caption, cdp_url, log=None: None)
            UploadHandler(self.db, self.settings).run({"payload": f'{{"task_id": {task_id}}}'})
        finally:
            UploadHandler._playwright_upload = original
            UploadHandler._start_gpm_profile = original_start
        with self.db.connect() as connection:
            account = connection.execute("SELECT posts_today,counter_date FROM tiktok_accounts WHERE id=?", (account_id,)).fetchone()
        self.assertEqual(1, account["posts_today"])
        self.assertTrue(account["counter_date"])

    def test_daily_limit_counter_resets_on_new_day(self):
        self.settings.tiktok_publish_enabled = True
        task_id, account_id = self._insert_upload_task(daily_limit=1, posts_today=1, counter_date="2000-01-01")
        original = UploadHandler._playwright_upload
        original_start = UploadHandler._start_gpm_profile
        try:
            UploadHandler._start_gpm_profile = lambda handler, profile_id, account_id, job_id=None: "http://127.0.0.1:45678"
            UploadHandler._playwright_upload = staticmethod(lambda path, caption, cdp_url, log=None: None)
            UploadHandler(self.db, self.settings).run({"payload": f'{{"task_id": {task_id}}}'})
        finally:
            UploadHandler._playwright_upload = original
            UploadHandler._start_gpm_profile = original_start
        with self.db.connect() as connection:
            account = connection.execute("SELECT posts_today,counter_date FROM tiktok_accounts WHERE id=?", (account_id,)).fetchone()
        self.assertEqual(1, account["posts_today"])
        self.assertNotEqual("2000-01-01", account["counter_date"])

    def test_account_publish_flag_overrides_global(self):
        self.settings.tiktok_publish_enabled = False
        task_id, account_id = self._insert_upload_task()
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE tiktok_accounts SET publish_enabled=1,gpm_profile_id='p' WHERE id=?",
                (account_id,),
            )
        original = UploadHandler._playwright_upload
        original_start = UploadHandler._start_gpm_profile
        try:
            UploadHandler._start_gpm_profile = lambda handler, profile_id, account_id, job_id=None: "http://127.0.0.1:45678"
            UploadHandler._playwright_upload = staticmethod(lambda path, caption, cdp_url, log=None: None)
            UploadHandler(self.db, self.settings).run({"payload": f'{{"task_id": {task_id}}}'})
        finally:
            UploadHandler._playwright_upload = original
            UploadHandler._start_gpm_profile = original_start
        with self.db.connect() as connection:
            status = connection.execute(
                "SELECT status FROM upload_tasks WHERE id=?", (task_id,)).fetchone()["status"]
        self.assertEqual("completed", status)

    def test_account_publish_disabled_blocks_global_true(self):
        self.settings.tiktok_publish_enabled = True
        task_id, account_id = self._insert_upload_task()
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE tiktok_accounts SET publish_enabled=0 WHERE id=?", (account_id,))
        with self.assertRaisesRegex(ManualAction, "Cho phép đăng TikTok"):
            UploadHandler(self.db, self.settings).run(
                {"payload": f'{{"task_id": {task_id}}}'})

    def test_tiktok_secondary_post_confirmation_is_accepted(self):
        original_click = UploadHandler._human_click

        class FakeButton:
            def __init__(self):
                self.clicked = False

            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

            def click(self):
                self.clicked = True

        class FakePage:
            def __init__(self):
                self.button = FakeButton()

            def get_by_role(self, role, name):
                self.role = role
                self.name = name
                return self.button

        try:
            UploadHandler._human_click = staticmethod(lambda page, locator, timeout=10_000: locator.click())
            page = FakePage()
            self.assertTrue(UploadHandler._confirm_tiktok_post_if_needed(page))
        finally:
            UploadHandler._human_click = original_click
        self.assertEqual("button", page.role)
        self.assertTrue(page.name.search("Post now"))
        self.assertTrue(page.button.clicked)

    def test_tiktok_secondary_post_confirmation_uses_tux_modal_fallback(self):
        original_click = UploadHandler._human_click

        class MissingRoleButton:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                raise RuntimeError("role selector unavailable")

        class FakeLocator:
            def __init__(self):
                self.clicked = False

            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

            def click(self):
                self.clicked = True

        class FakePage:
            def __init__(self):
                self.role_button = MissingRoleButton()
                self.locator_button = FakeLocator()
                self.selectors = []

            def get_by_role(self, role, name):
                return self.role_button

            def locator(self, selector):
                self.selectors.append(selector)
                return self.locator_button

        try:
            UploadHandler._human_click = staticmethod(lambda page, locator, timeout=10_000: locator.click())
            page = FakePage()
            self.assertTrue(UploadHandler._confirm_tiktok_post_if_needed(page))
        finally:
            UploadHandler._human_click = original_click
        self.assertIn('TUXModal', page.selectors[0])
        self.assertIn('Continue to post?', page.selectors[0])
        self.assertIn('Post now', page.selectors[0])
        self.assertTrue(page.locator_button.clicked)

    def test_tiktok_upload_can_use_select_video_button_filechooser(self):
        original = UploadHandler._find_tiktok_file_input
        original_click = UploadHandler._human_click

        class FakeButton:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

            def click(self):
                self.clicked = True

        class FakeChooser:
            def __init__(self):
                self.path = None

            def set_files(self, path):
                self.path = path

        class FakeChooserContext:
            def __init__(self, chooser):
                self.value = chooser

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakePage:
            def __init__(self):
                self.button = FakeButton()
                self.chooser = FakeChooser()
                self.selectors = []

            def locator(self, selector):
                self.selectors.append(selector)
                return self.button

            def expect_file_chooser(self, **kwargs):
                self.chooser_kwargs = kwargs
                return FakeChooserContext(self.chooser)

        try:
            UploadHandler._find_tiktok_file_input = staticmethod(lambda page, timeout=45_000: (_ for _ in ()).throw(RuntimeError("no input yet")))
            UploadHandler._human_click = staticmethod(lambda page, locator, timeout=10_000: locator.click())
            page = FakePage()
            UploadHandler._select_tiktok_upload_file(page, Path("demo.mp4"), timeout=45_000)
        finally:
            UploadHandler._find_tiktok_file_input = original
            UploadHandler._human_click = original_click
        self.assertEqual('button[aria-label="Select video"]', page.selectors[0])
        self.assertTrue(page.button.clicked)
        self.assertEqual("demo.mp4", page.chooser.path)

    def test_human_click_moves_mouse_to_dom_element_center(self):
        class FakeLocator:
            def __init__(self):
                self.clicked = False

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

            def scroll_into_view_if_needed(self, **kwargs):
                self.scrolled = True

            def bounding_box(self, **kwargs):
                return {"x": 10, "y": 20, "width": 100, "height": 50}

            def click(self):
                self.clicked = True

        class FakeMouse:
            def __init__(self):
                self.moves = []
                self.clicks = []

            def move(self, x, y, steps=1):
                self.moves.append((x, y, steps))

            def click(self, x, y):
                self.clicks.append((x, y))

        class FakePage:
            def __init__(self):
                self.mouse = FakeMouse()

            def wait_for_timeout(self, timeout):
                self.timeout = timeout

        page = FakePage()
        locator = FakeLocator()
        UploadHandler._human_click(page, locator)
        self.assertEqual([(60.0, 45.0, 8)], page.mouse.moves)
        self.assertEqual([(60.0, 45.0)], page.mouse.clicks)
        self.assertFalse(locator.clicked)

    def test_tiktok_wait_video_ready_accepts_check_circle_then_post_button(self):
        class FakePost:
            def __init__(self):
                self.calls = 0

            def wait_for(self, **kwargs):
                self.calls += 1
                if self.calls < 2:
                    raise RuntimeError("post not ready yet")

            def is_enabled(self, **kwargs):
                return True

            def get_attribute(self, name, **kwargs):
                if name in {"aria-disabled", "data-disabled", "data-loading"}:
                    return "false"
                return None

        class FakeIcon:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

        class FakePage:
            def __init__(self):
                self.icon = FakeIcon()
                self.selectors = []

            def locator(self, selector):
                self.selectors.append(selector)
                if selector == '[data-testid="CheckCircleFill"]':
                    return self.icon
                raise RuntimeError("not visible")

            def wait_for_timeout(self, timeout):
                self.timeout = timeout

        logs = []
        page = FakePage()
        post = FakePost()
        self.assertTrue(UploadHandler._wait_for_tiktok_video_ready(page, post, timeout=2_000, log=logs.append))
        self.assertEqual('[data-testid="CheckCircleFill"]', page.selectors[0])
        self.assertIn("TikTok: video đã xử lý xong (CheckCircleFill), đang chờ nút Post sẵn sàng", logs)
        self.assertIn("TikTok: video đã xử lý xong và nút Post đã sẵn sàng", logs)

    def test_tiktok_wait_video_ready_detects_browser_out_of_memory(self):
        class FakePost:
            def wait_for(self, **kwargs):
                raise RuntimeError("post not ready")

        class FakeBody:
            def inner_text(self, **kwargs):
                return "Aw, Snap! Something went wrong while displaying this webpage. Out of memory."

        class FakePage:
            url = "https://www.tiktok.com/tiktokstudio/upload"

            def locator(self, selector):
                return FakeBody()

        with self.assertRaises(TikTokBrowserOutOfMemory):
            UploadHandler._wait_for_tiktok_video_ready(FakePage(), FakePost(), timeout=1_000)

    def test_tiktok_crash_monitor_marks_page_crash(self):
        callbacks = {}

        class FakePage:
            def on(self, event, callback):
                callbacks[event] = callback

        logs = []
        state = UploadHandler._attach_tiktok_crash_monitor(FakePage(), log=logs.append)
        callbacks["crash"]()
        self.assertTrue(state["crashed"])
        self.assertTrue(UploadHandler._page_has_tiktok_browser_oom(FakePage(), state))
        self.assertIn("TikTok: Chrome báo page crash, app sẽ reload tab upload", logs)

    def test_tiktok_post_button_prefers_data_e2e_selector(self):
        class FakeButton:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

        class FakePage:
            def __init__(self):
                self.button = FakeButton()
                self.selectors = []

            def locator(self, selector):
                self.selectors.append(selector)
                return self.button

        page = FakePage()
        self.assertIs(page.button, UploadHandler._find_tiktok_post_button(page))
        self.assertEqual('button[data-e2e="post_video_button"][data-disabled="false"]', page.selectors[0])

    def test_tiktok_upload_closes_only_stale_upload_pages(self):
        class FakePage:
            def __init__(self, url):
                self.url = url
                self.closed = False
                self.blank = False

            def goto(self, url, **kwargs):
                self.blank = url == "about:blank"

            def close(self):
                self.closed = True

            def is_closed(self):
                return self.closed

        class FakeContext:
            def __init__(self):
                self.pages = [
                    FakePage("https://www.tiktok.com/tiktokstudio/upload"),
                    FakePage("https://www.tiktok.com/@demo"),
                    FakePage("https://www.tiktok.com/upload"),
                ]

        logs = []
        context = FakeContext()
        self.assertEqual(2, UploadHandler._close_stale_tiktok_upload_pages(context, log=logs.append))
        self.assertTrue(context.pages[0].closed)
        self.assertTrue(context.pages[0].blank)
        self.assertFalse(context.pages[1].closed)
        self.assertTrue(context.pages[2].closed)
        self.assertIn("TikTok: đã dọn 2 tab upload cũ để tránh lỗi out of memory", logs)

    def test_tiktok_caption_selects_first_hashtag_suggestion(self):
        original_click = UploadHandler._human_click

        class FakeEditor:
            def __init__(self):
                self.typed = []
                self.pressed = []

            def press(self, key):
                self.pressed.append(key)

            def type(self, text, delay=0):
                self.typed.append(text)

        class FakeOption:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

            def click(self):
                self.clicked = True

        class FakePage:
            def __init__(self):
                self.option = FakeOption()
                self.selectors = []

            def locator(self, selector):
                self.selectors.append(selector)
                return self.option

        clicks = []
        try:
            UploadHandler._human_click = staticmethod(lambda page, locator, timeout=10_000: clicks.append(locator))
            page = FakePage()
            editor = FakeEditor()
            logs = []
            UploadHandler._fill_tiktok_caption(page, editor, "#tik #viral", log=logs.append)
        finally:
            UploadHandler._human_click = original_click
        self.assertEqual(["Control+A", "Backspace"], editor.pressed)
        self.assertEqual(["#tik", " ", "#viral"], editor.typed)
        self.assertIn(".mention-list-popover", page.selectors[0])
        self.assertEqual(2, len(clicks))
        self.assertIn("TikTok: đã chọn hashtag #tik", logs)
        self.assertIn("TikTok: đã chọn hashtag #viral", logs)

    def test_tiktok_caption_types_title_before_hashtags(self):
        original_click = UploadHandler._human_click

        class FakeEditor:
            def __init__(self):
                self.typed = []
                self.pressed = []

            def press(self, key):
                self.pressed.append(key)

            def type(self, text, delay=0):
                self.typed.append(text)

        class FakeOption:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                pass

        class FakePage:
            def locator(self, selector):
                return FakeOption()

        try:
            UploadHandler._human_click = staticmethod(lambda page, locator, timeout=10_000: None)
            editor = FakeEditor()
            logs = []
            UploadHandler._fill_tiktok_caption(
                FakePage(), editor, "Tiêu đề video\n#tik #viral", log=logs.append)
        finally:
            UploadHandler._human_click = original_click
        self.assertEqual(["Control+A", "Backspace"], editor.pressed[:2])
        self.assertIn("Enter", editor.pressed)
        self.assertEqual(["Tiêu đề video", "#tik", " ", "#viral"], editor.typed)
        self.assertIn("TikTok: đã nhập nội dung caption", logs)

    def test_tiktok_upload_prefers_hidden_video_file_input(self):
        class FakeInput:
            def __init__(self):
                self.path = None

            def set_input_files(self, path):
                self.path = path

        class FakeEditor:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

        class FakePage:
            def __init__(self):
                self.input = FakeInput()
                self.editor = FakeEditor()
                self.button_requested = False

            def locator(self, selector):
                if "contenteditable" in selector:
                    return self.editor
                self.button_requested = True
                raise RuntimeError("button fallback should not be used")

        original = UploadHandler._find_tiktok_file_input
        try:
            UploadHandler._find_tiktok_file_input = staticmethod(lambda page, timeout=45_000: page.input)
            page = FakePage()
            logs = []
            UploadHandler._select_tiktok_upload_file(page, Path("hidden.mp4"), timeout=45_000, log=logs.append)
        finally:
            UploadHandler._find_tiktok_file_input = original
        self.assertEqual("hidden.mp4", page.input.path)
        self.assertFalse(page.button_requested)
        self.assertIn("TikTok: đã tìm thấy input[type=file]", logs)
        self.assertIn("TikTok: đã set file hidden.mp4", logs)
        self.assertIn("TikTok: file đã được gửi vào TikTok, tiếp tục chờ ô caption để nhập song song lúc video xử lý", logs)

    def test_tiktok_upload_reports_file_input_set_failure(self):
        class FakeInput:
            def set_input_files(self, path):
                raise RuntimeError("browser target crashed")

        original = UploadHandler._find_tiktok_file_input
        try:
            UploadHandler._find_tiktok_file_input = staticmethod(lambda page, timeout=45_000: FakeInput())
            logs = []
            with self.assertRaisesRegex(RuntimeError, r"set file thất bại|set file tháº¥t báº¡i"):
                UploadHandler._select_tiktok_upload_file(object(), Path("hidden.mp4"), timeout=45_000, log=logs.append)
        finally:
            UploadHandler._find_tiktok_file_input = original
        self.assertIn("TikTok: đã tìm thấy input[type=file]", logs)
        self.assertTrue(any("TikTok: đang set file" in item for item in logs))

    def test_tiktok_upload_waits_for_caption_form_after_hidden_file_input(self):
        class FakeInput:
            def __init__(self):
                self.path = None

            def set_input_files(self, path):
                self.path = path

        class FakeEditor:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

        class FakePage:
            def __init__(self):
                self.input = FakeInput()
                self.editor = FakeEditor()
                self.selectors = []

            def locator(self, selector):
                self.selectors.append(selector)
                if "contenteditable" in selector:
                    return self.editor
                raise RuntimeError("unexpected selector")

        original = UploadHandler._find_tiktok_file_input
        try:
            UploadHandler._find_tiktok_file_input = staticmethod(lambda page, timeout=45_000: page.input)
            page = FakePage()
            UploadHandler._select_tiktok_upload_file(page, Path("hidden.mp4"), timeout=45_000)
        finally:
            UploadHandler._find_tiktok_file_input = original
        self.assertEqual("hidden.mp4", page.input.path)
        self.assertEqual("hidden.mp4", page.input.path)

    def test_tiktok_file_select_fallback_accepts_already_visible_caption_form(self):
        original_input = UploadHandler._find_tiktok_file_input
        original_click = UploadHandler._human_click

        class FakeButton:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

        class FakeEditor:
            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                self.wait_kwargs = kwargs

        class FakePage:
            def __init__(self):
                self.button = FakeButton()
                self.editor = FakeEditor()

            def locator(self, selector):
                if "contenteditable" in selector:
                    return self.editor
                return self.button

            def expect_file_chooser(self, **kwargs):
                raise RuntimeError("file chooser closed after file was already accepted")

        try:
            UploadHandler._find_tiktok_file_input = staticmethod(lambda page, timeout=45_000: (_ for _ in ()).throw(RuntimeError("no input")))
            UploadHandler._human_click = staticmethod(lambda page, locator, timeout=10_000: None)
            UploadHandler._select_tiktok_upload_file(FakePage(), Path("accepted.mp4"), timeout=45_000)
        finally:
            UploadHandler._find_tiktok_file_input = original_input
            UploadHandler._human_click = original_click


if __name__ == "__main__": unittest.main()
