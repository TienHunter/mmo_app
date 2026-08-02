from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
from datetime import datetime, timezone
from pathlib import Path

from .application import Application
from .reporting import export_pipeline_report
from .services import GpmLoginClient, UploadHandler
from .settings import DEFAULT_GPM_API_URL


class Desktop(tk.Tk):
    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self.title("MMO App — YouTube → Video → TikTok")
        self.geometry("1120x680")
        self.minsize(900, 560)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self._build()
        self._last_events_text = ""
        self._start_tray()
        self.runtime_status.configure(text="● Chưa chạy — hãy nhập/lưu cấu hình rồi bấm Bắt đầu")
        self.start_button.configure(state="normal")
        self.pause_button.configure(state="disabled")
        self.after(500, self._refresh)

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="MMO APP", font=("Segoe UI", 18, "bold")).pack(side="left")
        self.runtime_status = ttk.Label(toolbar, text="● Đang khởi động…", font=("Segoe UI", 10, "bold"))
        self.runtime_status.pack(side="left", padx=18)
        self.start_button = ttk.Button(toolbar, text="Bắt đầu", command=self._start)
        self.start_button.pack(side="right", padx=4)
        self.pause_button = ttk.Button(toolbar, text="Tạm dừng", command=self._pause)
        self.pause_button.pack(side="right", padx=4)
        ttk.Button(toolbar, text="Thoát", command=self._exit).pack(side="right", padx=4)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.dashboard = ttk.Frame(self.notebook, padding=10)
        self.routes_tab = ttk.Frame(self.notebook, padding=10)
        self.settings_tab = ttk.Frame(self.notebook, padding=10)
        self.logs_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.dashboard, text="Tổng quan & hàng đợi")
        self.notebook.add(self.routes_tab, text="Luồng YouTube → TikTok")
        self.notebook.add(self.settings_tab, text="Cấu hình")
        self.notebook.add(self.logs_tab, text="Log")
        self.summary = ttk.Label(self.dashboard, text="", font=("Segoe UI", 11))
        self.summary.pack(anchor="w", pady=(0, 8))
        self.websub_status = ttk.Label(self.dashboard, text="", font=("Segoe UI", 10))
        self.websub_status.pack(anchor="w", pady=(0, 8))
        actions = ttk.Frame(self.dashboard)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="Xuất Excel báo cáo", command=self._export_report).pack(side="left")
        columns = ("id", "title", "media", "kind", "status", "attempts", "error")
        jobs_frame = ttk.Frame(self.dashboard)
        jobs_frame.pack(fill="both", expand=True)
        self.jobs = ttk.Treeview(jobs_frame, columns=columns, show="headings")
        widths = (55, 220, 80, 90, 110, 70, 340)
        labels = ("ID", "Video", "Media", "Stage", "Trạng thái", "Lần thử", "Lỗi / yêu cầu")
        for column, width, label in zip(columns, widths, labels):
            self.jobs.heading(column, text=label); self.jobs.column(column, width=width, anchor="w")
        jobs_y = ttk.Scrollbar(jobs_frame, orient="vertical", command=self.jobs.yview)
        jobs_x = ttk.Scrollbar(jobs_frame, orient="horizontal", command=self.jobs.xview)
        self.jobs.configure(yscrollcommand=jobs_y.set, xscrollcommand=jobs_x.set)
        self.jobs.grid(row=0, column=0, sticky="nsew")
        jobs_y.grid(row=0, column=1, sticky="ns")
        jobs_x.grid(row=1, column=0, sticky="ew")
        jobs_frame.rowconfigure(0, weight=1); jobs_frame.columnconfigure(0, weight=1)
        self._build_routes()
        self._build_settings()
        logs_frame = ttk.Frame(self.logs_tab)
        logs_frame.pack(fill="both", expand=True)
        self.events = tk.Text(logs_frame, wrap="word", state="disabled", font=("Consolas", 9))
        logs_y = ttk.Scrollbar(logs_frame, orient="vertical", command=self.events.yview)
        self.events.configure(yscrollcommand=logs_y.set)
        self.events.grid(row=0, column=0, sticky="nsew")
        logs_y.grid(row=0, column=1, sticky="ns")
        logs_frame.rowconfigure(0, weight=1); logs_frame.columnconfigure(0, weight=1)
        self._bind_mousewheel(self.events, self.events)

    def _build_settings(self) -> None:
        settings = self.application.settings
        canvas = tk.Canvas(self.settings_tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.settings_tab, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas)
        form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._bind_mousewheel(canvas, form)
        self.variables = {
            "tracking_mode": tk.StringVar(value=settings.tracking_mode),
            "public_domain": tk.StringVar(value=settings.public_domain),
            "webhook_host": tk.StringVar(value=settings.webhook_host),
            "webhook_port": tk.StringVar(value=str(settings.webhook_port)),
            "rss_interval_seconds": tk.StringVar(value=str(settings.rss_interval_seconds)),
            "download_dir": tk.StringVar(value=settings.download_dir),
            "output_dir": tk.StringVar(value=settings.output_dir),
            "output_aspect_ratio": tk.StringVar(value=settings.output_aspect_ratio),
            "video_zoom_factor": tk.DoubleVar(value=max(0.0, min(3.0, (settings.video_zoom_percent if settings.video_zoom_percent is not None else 100) / 100))),
            "video_zoom_label": tk.StringVar(value=""),
            "video_speed": tk.StringVar(value=str(settings.video_speed)),
            "source_orientation_filter": tk.StringVar(value=settings.source_orientation_filter),
            "overlay_text_enabled": tk.BooleanVar(value=settings.overlay_text_enabled),
            "overlay_font_path": tk.StringVar(value=settings.overlay_font_path),
            "overlay_font_color": tk.StringVar(value=settings.overlay_font_color),
            "overlay_font_size": tk.StringVar(value=str(settings.overlay_font_size)),
            "tiktok_publish_enabled": tk.BooleanVar(value=settings.tiktok_publish_enabled),
            "gpm_api_url": tk.StringVar(value=settings.gpm_api_url),
            "gpm_reclaim_profile_in_use": tk.BooleanVar(value=settings.gpm_reclaim_profile_in_use),
        }
        fields = [
            ("Public domain HTTPS (Cloudflare Tunnel)", "public_domain"),
            ("WebSub bind host", "webhook_host"),
            ("WebSub port", "webhook_port"),
            ("RSS fallback interval (giây)", "rss_interval_seconds"),
            ("Thư mục tải video", "download_dir"), ("Thư mục video đã xử lý", "output_dir"),
            ("Speed toàn app", "video_speed"),
            ("GPM API URL", "gpm_api_url"),
            ("Font chữ (.ttf/.otf, bỏ trống dùng Arial)", "overlay_font_path"),
            ("Màu chữ (white hoặc #RRGGBB)", "overlay_font_color"),
            ("Cỡ chữ", "overlay_font_size"),
        ]
        ttk.Label(form, text="Chế độ theo dõi").grid(row=0, column=0, sticky="w", padx=4, pady=7)
        ttk.Combobox(form, textvariable=self.variables["tracking_mode"], values=("both", "websub", "rss"), state="readonly", width=18).grid(row=0, column=1, sticky="w", padx=4, pady=7)
        for offset, (label, key) in enumerate(fields, start=1):
            row = offset
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=7)
            ttk.Entry(form, textvariable=self.variables[key], width=85).grid(row=row, column=1, sticky="ew", padx=4, pady=7)
        zoom_row = len(fields) + 1
        ttk.Label(form, text="Zoom video toàn app").grid(row=zoom_row, column=0, sticky="w", padx=4, pady=7)
        zoom_frame = ttk.Frame(form)
        zoom_frame.grid(row=zoom_row, column=1, sticky="ew", padx=4, pady=7)
        ttk.Scale(
            zoom_frame,
            from_=0,
            to=3,
            orient="horizontal",
            variable=self.variables["video_zoom_factor"],
            command=lambda _value: self._update_zoom_label(),
        ).pack(side="left", fill="x", expand=True)
        ttk.Label(zoom_frame, textvariable=self.variables["video_zoom_label"], width=16).pack(side="left", padx=(10, 0))
        self._update_zoom_label()
        orientation_row = zoom_row + 1
        ttk.Label(form, text="Lọc hướng video tải về").grid(row=orientation_row, column=0, sticky="w", padx=4, pady=7)
        ttk.Combobox(
            form,
            textvariable=self.variables["source_orientation_filter"],
            values=("both", "landscape", "portrait"),
            state="readonly",
            width=18,
        ).grid(row=orientation_row, column=1, sticky="w", padx=4, pady=7)
        ttk.Label(
            form,
            text="both = tải cả ngang/dọc, landscape = chỉ video ngang, portrait = chỉ video dọc",
        ).grid(row=orientation_row + 1, column=1, sticky="w", padx=4, pady=(0, 7))
        ratio_row = orientation_row + 2
        ttk.Label(form, text="Tỷ lệ video đầu ra").grid(row=ratio_row, column=0, sticky="w", padx=4, pady=7)
        ttk.Combobox(
            form,
            textvariable=self.variables["output_aspect_ratio"],
            values=("original", "9:16", "4:5", "1:1", "16:9"),
            state="readonly",
            width=18,
        ).grid(row=ratio_row, column=1, sticky="w", padx=4, pady=7)
        controls_row = ratio_row + 1
        ttk.Checkbutton(form, text="Chèn title phía trên và Part phía dưới video", variable=self.variables["overlay_text_enabled"]).grid(row=controls_row, column=1, sticky="w", padx=4, pady=7)
        ttk.Checkbutton(form, text="Cho phép click Đăng TikTok (tác vụ có tác động công khai)", variable=self.variables["tiktok_publish_enabled"]).grid(row=controls_row + 1, column=1, sticky="w", padx=4, pady=7)
        ttk.Checkbutton(form, text="GPM ProfileInUse: tự đóng profile và mở lại bằng app", variable=self.variables["gpm_reclaim_profile_in_use"]).grid(row=controls_row + 2, column=1, sticky="w", padx=4, pady=7)
        ttk.Button(form, text="Chọn font…", command=self._choose_font).grid(row=controls_row + 3, column=0, sticky="w", padx=4, pady=10)
        ttk.Button(form, text="Lưu cấu hình", command=self._save_settings).grid(row=controls_row + 3, column=1, sticky="w", padx=4, pady=10)
        ttk.Button(form, text="Mở thư mục dữ liệu", command=lambda: os.startfile(settings.data_dir)).grid(row=controls_row + 3, column=1, sticky="e", padx=4, pady=10)
        form.columnconfigure(1, weight=1)

    def _save_settings(self) -> None:
        try:
            value = self.application.settings
            self.runtime_status.configure(text="● Đang lấy Channel ID từ URL…")
            self.update_idletasks()
            resolved = self._save_routes()
            value.youtube_channel_urls = [item[2] for item in resolved]
            value.channel_ids = [item[0] for item in resolved]
            value.tracking_mode = self.variables["tracking_mode"].get()
            value.public_domain = self.variables["public_domain"].get().strip().rstrip("/")
            value.webhook_host = self.variables["webhook_host"].get().strip() or "127.0.0.1"
            value.webhook_port = int(self.variables["webhook_port"].get())
            if not 1 <= value.webhook_port <= 65535: raise ValueError("WebSub port phải từ 1 đến 65535")
            value.rss_interval_seconds = max(60, int(self.variables["rss_interval_seconds"].get()))
            if value.tracking_mode in {"websub", "both"} and value.public_domain and not value.public_domain.startswith("https://"):
                raise ValueError("Public domain WebSub phải bắt đầu bằng https://")
            value.download_dir = self.variables["download_dir"].get().strip()
            value.output_dir = self.variables["output_dir"].get().strip()
            zoom_factor = max(0.0, min(3.0, float(self.variables["video_zoom_factor"].get())))
            value.video_zoom_percent = int(round(zoom_factor * 100))
            value.video_speed = max(0.25, min(4.0, float(self.variables["video_speed"].get())))
            value.source_orientation_filter = self.variables["source_orientation_filter"].get()
            value.output_aspect_ratio = self.variables["output_aspect_ratio"].get()
            value.gpm_api_url = self.variables["gpm_api_url"].get().strip() or DEFAULT_GPM_API_URL
            value.gpm_reclaim_profile_in_use = bool(self.variables["gpm_reclaim_profile_in_use"].get())
            value.overlay_text_enabled = bool(self.variables["overlay_text_enabled"].get())
            value.overlay_font_path = self.variables["overlay_font_path"].get().strip()
            if value.overlay_font_path and not os.path.isfile(value.overlay_font_path):
                raise ValueError("Không tìm thấy file font đã chọn")
            value.overlay_font_color = self.variables["overlay_font_color"].get().strip() or "white"
            value.overlay_font_size = max(12, min(240, int(self.variables["overlay_font_size"].get())))
            value.tiktok_publish_enabled = bool(self.variables["tiktok_publish_enabled"].get())
            value.save()
            self.application.youtube.sync_channels(resolved)
            self.application.restart_websub()
            resolved_text = ", ".join(value.channel_ids)
            messagebox.showinfo("MMO App", f"Đã lưu cấu hình.\nChannel ID: {resolved_text}")
        except Exception as exc:
            messagebox.showerror("Không thể lưu", str(exc))

    def _choose_font(self) -> None:
        path = filedialog.askopenfilename(title="Chọn font chữ", filetypes=[("Font", "*.ttf *.otf"), ("Tất cả file", "*.*")])
        if path: self.variables["overlay_font_path"].set(path)

    def _update_zoom_label(self) -> None:
        factor = max(0.0, min(3.0, float(self.variables["video_zoom_factor"].get())))
        self.variables["video_zoom_label"].set(f"{factor:.2f}x ({int(round(factor * 100))}%)")

    def _build_routes(self) -> None:
        header = ttk.Frame(self.routes_tab)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="Mỗi layout: nhiều nguồn YouTube → một kênh TikTok", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(header, text="＋ Add layout", command=self._add_route).pack(side="right")
        canvas = tk.Canvas(self.routes_tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.routes_tab, orient="vertical", command=canvas.yview)
        self.routes_container = ttk.Frame(canvas)
        self.routes_container.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=self.routes_container, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True); scrollbar.pack(side="right", fill="y")
        self._bind_mousewheel(canvas, self.routes_container)
        self.route_cards = []
        self.loaded_route_ids = set()
        with self.application.database.connect() as connection:
            rows = connection.execute(
                """SELECT r.id,r.name,r.account_id,a.name account_name,a.mode,a.endpoint,a.gpm_profile_id,a.daily_limit,a.hashtags
                FROM channel_routes r JOIN tiktok_accounts a ON a.id=r.account_id
                WHERE r.active=1 ORDER BY r.id"""
            ).fetchall()
            for row in rows:
                urls = [dict(item) for item in connection.execute(
                    """SELECT source_url,segment_seconds,background_path
                    FROM channel_route_sources WHERE route_id=? ORDER BY rowid""", (row["id"],)
                ).fetchall()]
                self._add_route(dict(row), urls)
        if not self.route_cards:
            settings = self.application.settings
            legacy_urls = settings.youtube_channel_urls or [f"https://www.youtube.com/channel/{item}" for item in settings.channel_ids]
            self._add_route({"name": "Layout 1", "account_name": "TikTok 1", "mode": "cdp", "endpoint": settings.tiktok_cdp_url}, legacy_urls)

    def _add_route(self, data=None, urls=None) -> None:
        data, urls = data or {}, urls or [""]
        card = ttk.LabelFrame(self.routes_container, text=data.get("name") or f"Layout {len(self.route_cards) + 1}", padding=10)
        card.pack(fill="x", padx=4, pady=6)
        route = {"id": data.get("id"), "account_id": data.get("account_id"), "frame": card, "sources": []}
        if route["id"]: self.loaded_route_ids.add(route["id"])
        route["name"] = tk.StringVar(value=data.get("name") or f"Layout {len(self.route_cards) + 1}")
        route["account_name"] = tk.StringVar(value=data.get("account_name") or "")
        route["mode"] = tk.StringVar(value=data.get("mode") or "cdp")
        route["endpoint"] = tk.StringVar(value=data.get("endpoint") or "")
        route["gpm_profile_id"] = tk.StringVar(value=data.get("gpm_profile_id") or "")
        route["daily_limit"] = tk.StringVar(value=str(data.get("daily_limit") if data.get("daily_limit") is not None else 3))
        route["hashtags"] = tk.StringVar(value=data.get("hashtags") or "")
        route["manual_upload_path"] = tk.StringVar(value="")
        ttk.Label(card, text="Tên layout").grid(row=0, column=0, sticky="w"); ttk.Entry(card, textvariable=route["name"], width=24).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(card, text="Kênh TikTok").grid(row=0, column=2, sticky="w"); ttk.Entry(card, textvariable=route["account_name"], width=24).grid(row=0, column=3, sticky="ew", padx=6)
        ttk.Button(card, text="Xóa layout", command=lambda: self._remove_route(route)).grid(row=0, column=4, padx=4)
        ttk.Label(card, text="Chế độ upload").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Combobox(card, textvariable=route["mode"], values=("cdp", "gpm"), state="readonly", width=18).grid(row=1, column=1, sticky="w", padx=6, pady=7)
        ttk.Label(card, text="cdp = Chrome CDP/Playwright, gpm = GPM Automation").grid(row=1, column=2, columnspan=3, sticky="w", padx=6)
        ttk.Label(card, text="TikTok Chrome CDP URL").grid(row=2, column=0, sticky="w", pady=7); ttk.Entry(card, textvariable=route["endpoint"]).grid(row=2, column=1, columnspan=4, sticky="ew", padx=6)
        ttk.Label(card, text="GPM Profile ID").grid(row=3, column=0, sticky="w", pady=7); ttk.Entry(card, textvariable=route["gpm_profile_id"]).grid(row=3, column=1, columnspan=3, sticky="ew", padx=6)
        ttk.Button(card, text="Test GPM", command=lambda: self._test_gpm_route(route)).grid(row=3, column=4, padx=4)
        ttk.Label(card, text="Giới hạn đăng/ngày").grid(row=4, column=0, sticky="w", pady=7)
        ttk.Entry(card, textvariable=route["daily_limit"], width=10).grid(row=4, column=1, sticky="w", padx=6, pady=7)
        ttk.Label(card, text="0 = không giới hạn").grid(row=4, column=2, columnspan=3, sticky="w", padx=6)
        ttk.Label(card, text="Hashtag TikTok").grid(row=5, column=0, sticky="w", pady=7)
        ttk.Entry(card, textvariable=route["hashtags"]).grid(row=5, column=1, columnspan=4, sticky="ew", padx=6, pady=7)
        ttk.Label(card, text="Caption upload chỉ dùng hashtag, ví dụ: #review #viral #tiktok").grid(row=6, column=1, columnspan=4, sticky="w", padx=6)
        ttk.Label(card, text="Nguồn YouTube").grid(row=7, column=0, sticky="nw", pady=5)
        route["source_frame"] = ttk.Frame(card); route["source_frame"].grid(row=7, column=1, columnspan=4, sticky="ew")
        for url in urls: self._add_source(route, url)
        ttk.Button(card, text="＋ YouTube", command=lambda: self._add_source(route)).grid(row=8, column=1, sticky="w", pady=6)
        ttk.Label(card, text="Test upload TikTok").grid(row=9, column=0, sticky="w", pady=7)
        ttk.Entry(card, textvariable=route["manual_upload_path"]).grid(row=9, column=1, columnspan=2, sticky="ew", padx=6, pady=7)
        ttk.Button(card, text="Chọn file…", command=lambda: self._choose_manual_upload_file(route)).grid(row=9, column=3, sticky="w", padx=4)
        route["manual_upload_button"] = ttk.Button(card, text="Upload", command=lambda: self._manual_upload_route(route))
        route["manual_upload_button"].grid(row=9, column=4, sticky="w", padx=4)
        ttk.Label(card, text="Chọn video local để chạy thử đúng luồng upload TikTok và ghi log đầy đủ").grid(row=10, column=1, columnspan=4, sticky="w", padx=6)
        for column in (1, 3): card.columnconfigure(column, weight=1)
        self.route_cards.append(route)

    def _add_source(self, route, url="") -> None:
        data = url if isinstance(url, dict) else {"source_url": url}
        row = ttk.LabelFrame(route["source_frame"], text=f"YouTube {len(route['sources']) + 1}", padding=6)
        row.pack(fill="x", pady=4)
        variable = tk.StringVar(value=data.get("source_url") or "")
        segment_seconds = tk.StringVar(value=str(data.get("segment_seconds") or self.application.settings.segment_seconds))
        background_path = tk.StringVar(value=data.get("background_path") or "")
        ttk.Label(row, text="URL kênh").grid(row=0, column=0, sticky="w")
        ttk.Entry(row, textvariable=variable).grid(row=0, column=1, columnspan=5, sticky="ew", padx=5, pady=2)
        ttk.Label(row, text="Đoạn (giây)").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(row, textvariable=segment_seconds, width=10).grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(row, text="Zoom, Speed và lọc hướng video dùng chung trong tab Cấu hình").grid(row=1, column=2, columnspan=4, sticky="w", padx=5, pady=2)
        ttk.Label(row, text="Background").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(row, textvariable=background_path).grid(row=2, column=1, columnspan=4, sticky="ew", padx=5)
        source = {
            "frame": row,
            "url": variable,
            "segment_seconds": segment_seconds,
            "background_path": background_path,
        }
        ttk.Button(row, text="Chọn…", command=lambda: self._choose_source_background(source)).grid(row=2, column=5, sticky="w", padx=5)
        ttk.Button(row, text="− Xóa YouTube", command=lambda: self._remove_source(route, source)).grid(row=3, column=1, sticky="w", padx=5, pady=4)
        row.columnconfigure(1, weight=1)
        row.columnconfigure(4, weight=1)
        route["sources"].append(source)

    def _remove_source(self, route, source) -> None:
        if len(route["sources"]) <= 1:
            source["url"].set(""); return
        source["frame"].destroy(); route["sources"].remove(source)

    def _choose_source_background(self, source) -> None:
        path = filedialog.askopenfilename(
            title="Chọn background theo kênh YouTube",
            filetypes=[
                ("Video / Image", "*.mp4 *.mov *.mkv *.webm *.jpg *.jpeg *.png *.webp *.bmp"),
                ("Tất cả file", "*.*"),
            ],
        )
        if path:
            source["background_path"].set(path)

    def _choose_manual_upload_file(self, route) -> None:
        path = filedialog.askopenfilename(
            title="Chọn video local để upload TikTok",
            filetypes=[
                ("Video", "*.mp4 *.mov *.mkv *.webm *.avi *.m4v"),
                ("Tất cả file", "*.*"),
            ],
        )
        if path:
            route["manual_upload_path"].set(path)

    def _manual_upload_route(self, route) -> None:
        path = route["manual_upload_path"].get().strip()
        if not path:
            messagebox.showwarning("Chưa chọn file", "Hãy chọn một file video local trước khi bấm Upload.")
            return
        file_path = Path(path)
        if not file_path.is_file():
            messagebox.showerror("Không tìm thấy file", f"Không tìm thấy file video:\n{file_path}")
            route["manual_upload_path"].set("")
            return
        mode = route["mode"].get().strip() or "cdp"
        endpoint = route["endpoint"].get().strip()
        gpm_profile_id = route["gpm_profile_id"].get().strip()
        account_name = route["account_name"].get().strip() or route["name"].get().strip() or "TikTok"
        if mode == "cdp" and not (endpoint or self.application.settings.tiktok_cdp_url):
            messagebox.showwarning("Thiếu CDP URL", "Layout này đang dùng CDP, hãy nhập TikTok Chrome CDP URL trước.")
            return
        if mode == "gpm" and not gpm_profile_id:
            messagebox.showwarning("Thiếu GPM Profile ID", "Layout này đang dùng GPM, hãy nhập GPM Profile ID trước.")
            return
        button = route.get("manual_upload_button")
        if button:
            button.configure(state="disabled")
        self.runtime_status.configure(text=f"● Đang upload thủ công lên {account_name}…")
        self.application.database.event(None, "INFO", f"TikTok manual upload: người dùng chọn file {file_path} cho {account_name}")

        def worker():
            success = False
            error: Exception | None = None
            try:
                UploadHandler(self.application.database, self.application.settings).manual_upload_file(
                    file_path,
                    route["hashtags"].get().strip(),
                    mode,
                    endpoint=endpoint,
                    gpm_profile_id=gpm_profile_id,
                    account_id=route.get("account_id"),
                    job_id=None,
                )
                success = True
            except Exception as exc:
                error = exc
                self.application.database.event(None, "ERROR", f"TikTok manual upload thất bại: {exc}")

            def finish():
                route["manual_upload_path"].set("")
                if button:
                    button.configure(state="normal")
                if success:
                    self.runtime_status.configure(text="● Upload thủ công hoàn tất")
                    messagebox.showinfo("MMO App", f"Đã upload file lên {account_name}.\nÔ chọn file đã được xóa.")
                else:
                    self.runtime_status.configure(text="● Upload thủ công thất bại")
                    messagebox.showerror("Upload TikTok thất bại", str(error))

            self.after(0, finish)

        threading.Thread(target=worker, name="manual-tiktok-upload", daemon=True).start()

    def _remove_route(self, route) -> None:
        route["frame"].destroy(); self.route_cards.remove(route)

    def _test_gpm_route(self, route) -> None:
        profile_id = route["gpm_profile_id"].get().strip()
        api_url = self.variables["gpm_api_url"].get().strip() or self.application.settings.gpm_api_url
        if not profile_id:
            messagebox.showwarning("Thiếu GPM Profile ID", "Hãy nhập GPM Profile ID cho layout này trước.")
            return
        self.runtime_status.configure(text=f"● Đang test GPM profile {profile_id}…")

        def worker():
            try:
                cdp_url = GpmLoginClient(
                    api_url,
                    reclaim_profile_in_use=bool(self.variables["gpm_reclaim_profile_in_use"].get()),
                ).start_profile(profile_id)
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Test GPM thất bại", str(exc)))
                self.after(0, lambda: self.runtime_status.configure(text="● Test GPM thất bại"))
                return
            self.after(0, lambda: route["endpoint"].set(cdp_url))
            self.after(0, lambda: messagebox.showinfo("Test GPM thành công", f"Profile đã mở.\nCDP URL:\n{cdp_url}"))
            self.after(0, lambda: self.runtime_status.configure(text="● Test GPM thành công"))

        threading.Thread(target=worker, name="test-gpm-profile", daemon=True).start()

    def _save_routes(self):
        if not self.route_cards:
            raise ValueError("Cần ít nhất một layout YouTube → TikTok")
        prepared, all_resolved, used = [], [], set()
        for index, route in enumerate(self.route_cards, 1):
            name = route["name"].get().strip() or f"Layout {index}"
            account_name = route["account_name"].get().strip()
            mode = route["mode"].get().strip() or "cdp"
            endpoint = route["endpoint"].get().strip()
            gpm_profile_id = route["gpm_profile_id"].get().strip()
            hashtags = route["hashtags"].get().strip()
            try:
                daily_limit = int(route["daily_limit"].get().strip() or "0")
            except ValueError as exc:
                raise ValueError(f"{name}: giới hạn đăng/ngày phải là số nguyên") from exc
            sources = []
            for source in route["sources"]:
                url = source["url"].get().strip()
                if not url:
                    continue
                try:
                    segment_seconds = int(source["segment_seconds"].get().strip() or str(self.application.settings.segment_seconds))
                except ValueError as exc:
                    raise ValueError(f"{name}: thời lượng mỗi đoạn của YouTube không hợp lệ") from exc
                background_path = source["background_path"].get().strip()
                if segment_seconds < 5: raise ValueError(f"{name}: thời lượng mỗi đoạn của YouTube phải >= 5 giây")
                if background_path and not os.path.isfile(background_path):
                    raise ValueError(f"{name}: không tìm thấy background {background_path}")
                sources.append((url, segment_seconds, background_path))
            if not account_name: raise ValueError(f"{name}: chưa nhập tên kênh TikTok")
            if not sources: raise ValueError(f"{name}: cần ít nhất một URL YouTube")
            if mode not in {"cdp", "gpm"}: raise ValueError(f"{name}: chế độ upload không hợp lệ")
            if daily_limit < 0: raise ValueError(f"{name}: giới hạn đăng/ngày không được âm")
            if mode == "cdp" and not endpoint: raise ValueError(f"{name}: chế độ cdp cần TikTok Chrome CDP URL")
            if mode == "gpm" and not gpm_profile_id: raise ValueError(f"{name}: chế độ gpm cần GPM Profile ID")
            resolved_sources = []
            for url, segment_seconds, background_path in sources:
                channel_id, title, normalized_url = self.application.youtube.resolve_channel_url(url)
                resolved_sources.append((channel_id, title, normalized_url, segment_seconds, background_path))
            duplicate = next((item[0] for item in resolved_sources if item[0] in used), None)
            if duplicate: raise ValueError(f"Kênh YouTube {duplicate} đang nằm trong nhiều layout")
            used.update(item[0] for item in resolved_sources)
            all_resolved.extend((item[0], item[1], item[2]) for item in resolved_sources)
            prepared.append((route, name, account_name, mode, endpoint, gpm_profile_id, daily_limit, hashtags, resolved_sources))
        self.application.youtube.sync_channels(all_resolved)
        active_ids = []
        with self.application.database.connect() as connection:
            connection.execute("DELETE FROM channel_route_sources")
            for route, name, account_name, mode, endpoint, gpm_profile_id, daily_limit, hashtags, resolved in prepared:
                if route["account_id"]:
                    connection.execute(
                        "UPDATE tiktok_accounts SET name=?,mode=?,endpoint=?,gpm_profile_id=?,daily_limit=?,hashtags=?,active=1 WHERE id=?",
                        (account_name, mode, endpoint, gpm_profile_id, daily_limit, hashtags, route["account_id"]),
                    )
                    account_id = route["account_id"]
                else:
                    account_id = connection.execute(
                        "INSERT INTO tiktok_accounts(name,mode,endpoint,gpm_profile_id,daily_limit,hashtags) VALUES (?,?,?,?,?,?)",
                        (account_name, mode, endpoint, gpm_profile_id, daily_limit, hashtags),
                    ).lastrowid
                    route["account_id"] = account_id
                if route["id"]:
                    connection.execute("UPDATE channel_routes SET name=?,account_id=?,active=1 WHERE id=?", (name, account_id, route["id"])); route_id = route["id"]
                else:
                    route_id = connection.execute("INSERT INTO channel_routes(name,account_id) VALUES (?,?)", (name, account_id)).lastrowid; route["id"] = route_id
                active_ids.append(route_id)
                for channel_id, _title, url, segment_seconds, background_path in resolved:
                    connection.execute(
                        """INSERT INTO channel_route_sources(route_id,channel_id,source_url,segment_seconds,background_path)
                        VALUES (?,?,?,?,?)""",
                        (route_id, channel_id, url, segment_seconds, background_path),
                    )
            removed = self.loaded_route_ids - set(active_ids)
            for route_id in removed: connection.execute("UPDATE channel_routes SET active=0 WHERE id=?", (route_id,))
        self.loaded_route_ids = set(active_ids)
        return all_resolved

    def _refresh(self) -> None:
        if not self.winfo_exists(): return
        with self.application.database.connect() as connection:
            rows = connection.execute("""SELECT j.*,m.title FROM jobs j JOIN media_items m ON m.id=j.media_item_id ORDER BY j.id DESC LIMIT 200""").fetchall()
            totals = connection.execute("SELECT status,COUNT(*) count FROM jobs GROUP BY status").fetchall()
            events = connection.execute("SELECT * FROM job_events ORDER BY id DESC LIMIT 250").fetchall()
            subscriptions = connection.execute(
                "SELECT subscription_status,COUNT(*) count FROM channels WHERE active=1 GROUP BY subscription_status"
            ).fetchall()
        running = self.application.is_running
        self.runtime_status.configure(text="● Đang chạy" if running else "● Chưa chạy / Đã tạm dừng")
        self.start_button.configure(state="disabled" if running else "normal")
        self.pause_button.configure(state="normal" if running else "disabled")
        self.summary.configure(text="  |  ".join(f"{row['status']}: {row['count']}" for row in totals) or "Chưa có tác vụ")
        subscription_text = "  |  ".join(f"{row['subscription_status']}: {row['count']}" for row in subscriptions)
        if self.application.settings.tracking_mode in {"websub", "both"}:
            self.websub_status.configure(
                text=f"WebSub: {subscription_text or 'chưa có kênh'} — chỉ video mới đăng sau khi subscribe sẽ vào hàng đợi"
            )
        else:
            self.websub_status.configure(text="WebSub: không sử dụng (đang chạy chế độ RSS)")
        self.jobs.delete(*self.jobs.get_children())
        for row in rows:
            self.jobs.insert("", "end", values=(row["id"], row["title"], row["media_item_id"], row["kind"], row["status"], row["attempts"], row["error_message"] or ""))
        text = "\n".join(
            f"{self.format_log_time(row['created_at'])} | {row['level']:<7} | job={row['job_id'] or '-'} | {row['message']}"
            for row in reversed(events)
        )
        if text != self._last_events_text:
            first, last = self.events.yview()
            stick_to_bottom = last >= 0.98
            self.events.configure(state="normal")
            self.events.delete("1.0", "end")
            self.events.insert("1.0", text)
            self.events.configure(state="disabled")
            if stick_to_bottom:
                self.events.see("end")
            else:
                self.events.yview_moveto(first)
            self._last_events_text = text
        self.after(2000, self._refresh)

    @staticmethod
    def format_log_time(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            if text.endswith("Z"):
                parsed = datetime.fromisoformat(text[:-1] + "+00:00")
            else:
                parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            local = parsed.astimezone()
            return local.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return text

    def _start(self) -> None:
        try:
            started = self.application.start()
            self.runtime_status.configure(text="● Đang chạy" if started else "● Đang chạy (đã bật trước đó)")
            self.start_button.configure(state="disabled")
            self.pause_button.configure(state="normal")
        except Exception as exc:
            self.runtime_status.configure(text="● Không thể khởi động")
            messagebox.showerror("Không thể bắt đầu", str(exc))

    def _pause(self) -> None:
        self.application.pause()
        self.runtime_status.configure(text="● Đã tạm dừng")
        self.start_button.configure(state="normal")
        self.pause_button.configure(state="disabled")

    def _export_report(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Xuất Excel báo cáo flow",
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
            initialfile="mmo_flow_report.xlsx",
        )
        if not path:
            return
        try:
            exported = export_pipeline_report(self.application.database, path)
            messagebox.showinfo("MMO App", f"Đã xuất báo cáo:\n{exported}")
        except Exception as exc:
            messagebox.showerror("Không thể xuất Excel", str(exc))

    def _exit(self) -> None:
        if getattr(self, "tray", None): self.tray.stop()
        self.application.shutdown(); self.destroy()

    def _start_tray(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            self.tray = None; return
        image = Image.new("RGB", (64, 64), "#172554")
        draw = ImageDraw.Draw(image); draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill="#38bdf8"); draw.text((19, 20), "M", fill="#082f49")
        self.tray = pystray.Icon("MMOApp", image, "MMO App", menu=pystray.Menu(
            pystray.MenuItem("Mở MMO App", lambda *_: self.after(0, self._show)),
            pystray.MenuItem("Bắt đầu", lambda *_: self.application.start()),
            pystray.MenuItem("Tạm dừng", lambda *_: self.application.pause()),
            pystray.MenuItem("Thoát hoàn toàn", lambda *_: self.after(0, self._exit)),
        ))
        threading.Thread(target=self.tray.run, name="system-tray", daemon=True).start()

    def _show(self) -> None:
        self.deiconify(); self.lift(); self.focus_force()

    def _bind_mousewheel(self, target, widget) -> None:
        def on_mousewheel(event):
            target.yview_scroll(int(-1 * (event.delta / 120)), "units")

        widget.bind("<Enter>", lambda _e: self.bind_all("<MouseWheel>", on_mousewheel))
        widget.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))


def run() -> None:
    app = Application()
    Desktop(app).mainloop()
