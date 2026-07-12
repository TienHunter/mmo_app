from __future__ import annotations

import tempfile
import unittest
import logging
import time
from pathlib import Path

from mmo_app.application import Application
from mmo_app.database import Database
from mmo_app import services
from mmo_app.services import ManualAction, ProcessHandler, UploadHandler, YouTubeService
from mmo_app.settings import Settings
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

    def test_retry_and_recovery(self):
        service = YouTubeService(self.db, self.settings); service.discover("12345678901", "UCtest", "Demo")
        job = self.db.claim("test", ("download",), lease_seconds=-1)
        self.assertEqual(1, self.db.recover_expired_leases())
        again = self.db.claim("test", ("download",)); self.db.fail(again["id"], "boom")
        with self.db.connect() as connection:
            row = connection.execute("SELECT status FROM jobs WHERE id=?", (again["id"],)).fetchone()
        self.assertEqual("retry_wait", row["status"])

    def test_websub_notification_is_primary_ingest(self):
        youtube = YouTubeService(self.db, self.settings)
        websub = WebSubService(self.db, self.settings, youtube)
        body = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
        <entry><yt:videoId>websub00001</yt:videoId><yt:channelId>UCdemo</yt:channelId>
        <title>Realtime</title><published>2026-07-07T00:00:00Z</published></entry></feed>'''
        self.assertEqual(1, websub.receive_notification(body))
        self.assertEqual(0, websub.receive_notification(body))
        with self.db.connect() as connection:
            job = connection.execute("SELECT kind,status FROM jobs").fetchone()
        self.assertEqual(("download", "queued"), (job["kind"], job["status"]))

    def test_websub_verification_marks_channel_subscribed(self):
        channel_id = "UCverify000000000000000001"
        with self.db.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id) VALUES (?)", (channel_id,))
        websub = WebSubService(self.db, self.settings, YouTubeService(self.db, self.settings))
        websub.record_verification(
            "subscribe",
            f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "3600",
        )
        with self.db.connect() as connection:
            channel = connection.execute("SELECT subscription_status,subscribed_at,subscription_expires_at FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
        self.assertEqual("subscribed", channel["subscription_status"])
        self.assertTrue(channel["subscribed_at"])
        self.assertTrue(channel["subscription_expires_at"])

    def test_mobile_aspect_ratio_filters_fit_without_cropping(self):
        self.assertEqual("", ProcessHandler.video_filter("original"))
        self.assertEqual(
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
            ProcessHandler.video_filter("9:16"),
        )
        self.assertIn("pad=1080:1350", ProcessHandler.video_filter("4:5"))
        self.assertNotIn("crop=", ProcessHandler.video_filter("9:16"))

    def test_short_final_segment_is_merged_into_previous_part(self):
        self.assertEqual(
            [(0.0, 60.0), (60.0, 70.0)],
            ProcessHandler.segment_ranges(130, 60),
        )
        self.assertEqual(
            [(0.0, 60.0), (60.0, 31.0)],
            ProcessHandler.segment_ranges(91, 60),
        )
        self.assertEqual(
            [(0.0, 89.0)],
            ProcessHandler.segment_ranges(89, 60),
        )

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

    def test_split_video_captions_get_part_suffixes(self):
        youtube = YouTubeService(self.db, self.settings)
        youtube.discover("parts000001", "UCparts", "Demo title")
        with self.db.connect() as connection:
            media_id = connection.execute("SELECT id FROM media_items WHERE youtube_video_id='parts000001'").fetchone()["id"]
            connection.execute("INSERT INTO artifacts(media_item_id,kind,path,segment_index) VALUES (?,'rendered','part1.mp4',1)", (media_id,))
            connection.execute("INSERT INTO artifacts(media_item_id,kind,path,segment_index) VALUES (?,'rendered','part2.mp4',2)", (media_id,))
            connection.execute("INSERT INTO tiktok_accounts(name,endpoint) VALUES ('TikTok','http://127.0.0.1:9222')")
        ProcessHandler(self.db, self.settings)._route_uploads(media_id, "Demo title")
        with self.db.connect() as connection:
            captions = [row["caption"] for row in connection.execute("SELECT caption FROM upload_tasks ORDER BY artifact_id")]
        self.assertEqual(["Demo title - Part 1", "Demo title - Part 2"], captions)

    def test_processor_output_group_uses_mapped_tiktok_channel_name(self):
        with self.db.connect() as connection:
            connection.execute("INSERT INTO channels(channel_id) VALUES ('UCfolder')")
            account_id = connection.execute("INSERT INTO tiktok_accounts(name) VALUES ('TikTok: Main/01')").lastrowid
            route_id = connection.execute("INSERT INTO channel_routes(name,account_id) VALUES ('Layout',?)", (account_id,)).lastrowid
            connection.execute("INSERT INTO channel_route_sources(route_id,channel_id,source_url) VALUES (?,?,?)", (route_id, 'UCfolder', 'https://youtube.com/@folder'))
        handler = ProcessHandler(self.db, self.settings)
        self.assertEqual("TikTok_ Main_01", handler._output_group("UCfolder"))
        self.assertEqual("UCunmapped", handler._output_group("UCunmapped"))

    def test_title_and_part_overlay_filters_use_utf8_text_files(self):
        self.settings.overlay_text_enabled = True
        self.settings.overlay_font_color = "#ffcc00"
        self.settings.overlay_font_size = 64
        destination = Path(self.temp.name) / "001.mp4"
        filters, files = ProcessHandler(self.db, self.settings).overlay_filters(destination, "Tiêu đề tiếng Việt", 2, 3)
        self.assertEqual(2, len(filters))
        self.assertIn("fontcolor=#ffcc00", filters[0])
        self.assertIn("fontsize=64", filters[0])
        self.assertEqual("Tiêu đề tiếng Việt", files[0].read_text(encoding="utf-8"))
        self.assertEqual("Part 2", files[1].read_text(encoding="utf-8"))

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
        self.assertEqual(1, len(calls))
        command_text = ",".join(calls[0])
        self.assertIn("drawtext=", command_text)
        self.assertIn(".part.txt", command_text)

    def test_existing_output_without_filters_is_skipped(self):
        source = Path(self.temp.name) / "source.mp4"
        destination = Path(self.temp.name) / "001.mp4"
        source.write_bytes(b"source")
        destination.write_bytes(b"old")
        calls = []
        original = services.subprocess.run
        try:
            services.subprocess.run = lambda command, **kwargs: calls.append(command)
            ProcessHandler(self.db, self.settings)._render(source, destination, 0, None, "Title", 1, 1)
        finally:
            services.subprocess.run = original
        self.assertEqual([], calls)

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

    def test_gpm_upload_mode_is_saved_but_requires_adapter(self):
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
        with self.assertRaisesRegex(ManualAction, "GPM Automation"):
            UploadHandler(self.db, self.settings).run(job)

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
        try:
            UploadHandler._playwright_upload = staticmethod(lambda path, caption, cdp_url: None)
            UploadHandler(self.db, self.settings).run({"payload": f'{{"task_id": {task_id}}}'})
        finally:
            UploadHandler._playwright_upload = original
        with self.db.connect() as connection:
            account = connection.execute("SELECT posts_today,counter_date FROM tiktok_accounts WHERE id=?", (account_id,)).fetchone()
        self.assertEqual(1, account["posts_today"])
        self.assertTrue(account["counter_date"])

    def test_daily_limit_counter_resets_on_new_day(self):
        self.settings.tiktok_publish_enabled = True
        task_id, account_id = self._insert_upload_task(daily_limit=1, posts_today=1, counter_date="2000-01-01")
        original = UploadHandler._playwright_upload
        try:
            UploadHandler._playwright_upload = staticmethod(lambda path, caption, cdp_url: None)
            UploadHandler(self.db, self.settings).run({"payload": f'{{"task_id": {task_id}}}'})
        finally:
            UploadHandler._playwright_upload = original
        with self.db.connect() as connection:
            account = connection.execute("SELECT posts_today,counter_date FROM tiktok_accounts WHERE id=?", (account_id,)).fetchone()
        self.assertEqual(1, account["posts_today"])
        self.assertNotEqual("2000-01-01", account["counter_date"])

    def test_tiktok_secondary_post_confirmation_is_accepted(self):
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

        page = FakePage()
        self.assertTrue(UploadHandler._confirm_tiktok_post_if_needed(page))
        self.assertEqual("button", page.role)
        self.assertTrue(page.name.search("Post now"))
        self.assertTrue(page.button.clicked)

    def test_tiktok_secondary_post_confirmation_uses_tux_modal_fallback(self):
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

        page = FakePage()
        self.assertTrue(UploadHandler._confirm_tiktok_post_if_needed(page))
        self.assertIn('TUXModal', page.selectors[0])
        self.assertIn('Continue to post?', page.selectors[0])
        self.assertIn('Post now', page.selectors[0])
        self.assertTrue(page.locator_button.clicked)

    def test_tiktok_upload_can_use_select_video_button_filechooser(self):
        original = UploadHandler._find_tiktok_file_input

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
            page = FakePage()
            UploadHandler._select_tiktok_upload_file(page, Path("demo.mp4"), timeout=45_000)
        finally:
            UploadHandler._find_tiktok_file_input = original
        self.assertEqual('button[aria-label="Select video"]', page.selectors[0])
        self.assertTrue(page.button.clicked)
        self.assertEqual("demo.mp4", page.chooser.path)

    def test_tiktok_upload_prefers_hidden_video_file_input(self):
        class FakeInput:
            def __init__(self):
                self.path = None

            def set_input_files(self, path):
                self.path = path

        class FakePage:
            def __init__(self):
                self.input = FakeInput()
                self.button_requested = False

            def locator(self, selector):
                self.button_requested = True
                raise RuntimeError("button fallback should not be used")

        original = UploadHandler._find_tiktok_file_input
        try:
            UploadHandler._find_tiktok_file_input = staticmethod(lambda page, timeout=45_000: page.input)
            page = FakePage()
            UploadHandler._select_tiktok_upload_file(page, Path("hidden.mp4"), timeout=45_000)
        finally:
            UploadHandler._find_tiktok_file_input = original
        self.assertEqual("hidden.mp4", page.input.path)
        self.assertFalse(page.button_requested)


if __name__ == "__main__": unittest.main()
