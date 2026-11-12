"""Ensure a frozen Windows build resolves Qt DLLs from its own bundle first."""

from __future__ import annotations

import os
import sys


if getattr(sys, "frozen", False):
    bundle_dir = str(getattr(sys, "_MEIPASS", ""))
    if bundle_dir:
        os.add_dll_directory(bundle_dir)
        os.environ["PATH"] = bundle_dir + os.pathsep + os.environ.get("PATH", "")
