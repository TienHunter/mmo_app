PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS channels(
 id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL UNIQUE, title TEXT, source_url TEXT, active INTEGER NOT NULL DEFAULT 1,
 last_checked_at TEXT, subscription_status TEXT NOT NULL DEFAULT 'not_subscribed',
 subscribed_at TEXT, subscription_expires_at TEXT, subscription_error TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS media_items(
 id INTEGER PRIMARY KEY, youtube_video_id TEXT UNIQUE, channel_id TEXT, title TEXT, source_url TEXT,
 published_at TEXT, status TEXT NOT NULL DEFAULT 'discovered', error_message TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS artifacts(
 id INTEGER PRIMARY KEY, media_item_id INTEGER NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
 segment_index INTEGER, size_bytes INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(media_item_id,kind,path), FOREIGN KEY(media_item_id) REFERENCES media_items(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS jobs(
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, media_item_id INTEGER NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 payload TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 max_attempts INTEGER NOT NULL DEFAULT 3, available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 lease_owner TEXT, lease_expires_at TEXT, error_message TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(media_item_id) REFERENCES media_items(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status,kind,available_at,created_at);
CREATE TABLE IF NOT EXISTS job_events(
 id INTEGER PRIMARY KEY, job_id INTEGER, level TEXT NOT NULL, message TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS tiktok_accounts(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'cdp', endpoint TEXT,
 gpm_profile_id TEXT, active INTEGER NOT NULL DEFAULT 1, daily_limit INTEGER NOT NULL DEFAULT 3,
 posts_today INTEGER NOT NULL DEFAULT 0, counter_date TEXT, status TEXT NOT NULL DEFAULT 'idle',
 hashtags TEXT NOT NULL DEFAULT '', speed REAL NOT NULL DEFAULT 1.0,
 zoom_width_percent INTEGER NOT NULL DEFAULT 100, zoom_height_percent INTEGER NOT NULL DEFAULT 100,
 caption_mode TEXT NOT NULL DEFAULT 'fixed', caption_text TEXT NOT NULL DEFAULT '',
 output_dir TEXT NOT NULL DEFAULT '', notification_sound TEXT NOT NULL DEFAULT '',
 split_enabled INTEGER, overlay_enabled INTEGER, background_path TEXT NOT NULL DEFAULT '',
 publish_enabled INTEGER, ai_api_url TEXT NOT NULL DEFAULT '', ai_api_key TEXT NOT NULL DEFAULT '',
 ai_model TEXT NOT NULL DEFAULT '', ai_system_prompt TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS channel_routes(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, account_id INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(account_id) REFERENCES tiktok_accounts(id));
CREATE TABLE IF NOT EXISTS channel_route_sources(
 route_id INTEGER NOT NULL, channel_id TEXT NOT NULL, source_url TEXT NOT NULL,
 segment_seconds INTEGER, zoom_percent INTEGER NOT NULL DEFAULT 100, speed REAL NOT NULL DEFAULT 1.0,
 background_path TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(route_id,channel_id), UNIQUE(channel_id),
 FOREIGN KEY(route_id) REFERENCES channel_routes(id) ON DELETE CASCADE,
 FOREIGN KEY(channel_id) REFERENCES channels(channel_id));
CREATE TABLE IF NOT EXISTS upload_tasks(
 id INTEGER PRIMARY KEY, media_item_id INTEGER NOT NULL, artifact_id INTEGER NOT NULL, account_id INTEGER,
 caption TEXT NOT NULL DEFAULT '', scheduled_at TEXT, status TEXT NOT NULL DEFAULT 'pending', posted_at TEXT,
 error_message TEXT, is_test INTEGER NOT NULL DEFAULT 0, UNIQUE(artifact_id,account_id),
 FOREIGN KEY(media_item_id) REFERENCES media_items(id), FOREIGN KEY(artifact_id) REFERENCES artifacts(id),
 FOREIGN KEY(account_id) REFERENCES tiktok_accounts(id));
