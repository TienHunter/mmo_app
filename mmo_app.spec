# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas = [(str(root / "mmo_app" / "schema.sql"), "mmo_app")]
binaries = []
hiddenimports = ["pystray._win32"]
tools = root / "tools"
if tools.exists():
    datas.append((str(tools), "tools"))
else:
    # Reuse the already audited binaries from the existing internal releases.
    binaries += [
        (str(root.parent / "video_processor" / "dist_release" / "VideoProcessor" / "_internal" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"), "tools/ffmpeg/bin"),
        (str(root.parent / "video_processor" / "dist_release" / "VideoProcessor" / "_internal" / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"), "tools/ffmpeg/bin"),
        (str(root.parent / "youtube_tracker" / "dist_responsive" / "YouTubeTracker" / "_internal" / "runtime" / "node.exe"), "tools/runtime"),
    ]
for package in ("yt_dlp", "playwright"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas; binaries += package_binaries; hiddenimports += package_hidden

a = Analysis([str(root / "main.py")], pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[str(root / "mmo_app" / "qt_runtime_hook.py")], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
# Qt binaries must remain uncompressed. Compressing a Qt DLL can surface as an
# opaque "DLL load failed" error on Windows before the application UI appears.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MMOApp", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MMOApp")
