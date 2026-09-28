from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

user32 = ctypes.windll.user32
hwnd = user32.FindWindowW(None, "Design")
if not hwnd:
    raise SystemExit("Claude Design window not found")
rect = wintypes.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(rect))
user32.ShowWindow(hwnd, 9)
user32.SetForegroundWindow(hwnd)
output = Path(__file__).resolve().parents[1] / "work" / "gloria_ads_case_20260812" / "claude_design.png"
output.parent.mkdir(parents=True, exist_ok=True)
ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True).save(output)
print(f"{hwnd}|{rect.left}|{rect.top}|{rect.right}|{rect.bottom}|{output}")
