"""클릭 통과(WS_EX_TRANSPARENT) 히트 테스트 실측.

WindowFromPoint 가 WS_EX_TRANSPARENT 를 실제로 건너뛰는지, 그리고 Tk 창에서
그 값이 무엇을 가리키는지 계층을 통째로 덤프해서 확인한다.

    python tools/probe_hittest.py
"""

import ctypes
import os
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402

from lane_cover import native  # noqa: E402

u32 = native.u32

u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetClassNameW.restype = ctypes.c_int


def describe(hwnd):
    if not hwnd:
        return "0x0 (없음)"
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf, 256)
    rect = native.get_window_rect(hwnd)
    style = u32.GetWindowLongPtrW(hwnd, -16)   # GWL_STYLE
    ex = u32.GetWindowLongPtrW(hwnd, native.GWL_EXSTYLE)
    return "0x%-8x %-28s rect=%-24s style=0x%08x ex=0x%08x" % (
        hwnd, buf.value or "?", rect, style, ex,
    )


def chain(hwnd, label):
    print("  %s:" % label)
    current = hwnd
    for depth in range(8):
        if not current:
            break
        print("    %s%s" % ("  " * depth + ("└ " if depth else ""), describe(current)))
        current = u32.GetParent(current)
        if not current:
            break
    if not hwnd:
        print("    (없음)")


def main():
    native.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()

    RECT = (200, 150, 500, 300)
    CX, CY = RECT[0] + RECT[2] // 2, RECT[1] + RECT[3] // 2

    # B: 커버 아래에 깔릴 일반 창 (topmost 아님)
    b = tk.Toplevel(root)
    b.overrideredirect(True)
    b.configure(bg="#205020")
    b.update_idletasks()
    native.move_window(native.top_hwnd(b), *RECT)

    # A: 커버 역할. topmost + overrideredirect
    a = tk.Toplevel(root)
    a.overrideredirect(True)
    a.attributes("-topmost", True)
    a.configure(bg="#502020")
    a.update_idletasks()
    native.move_window(native.top_hwnd(a), *RECT)

    root.update()
    a_hwnd = native.top_hwnd(a)
    b_hwnd = native.top_hwnd(b)

    print("=" * 100)
    print("A (커버) = 0x%x    B (아래 창) = 0x%x    probe=(%d,%d)" % (a_hwnd, b_hwnd, CX, CY))
    print("=" * 100)

    print("\n[1] 클릭 통과 OFF")
    native.set_click_through(a_hwnd, False)
    root.update()
    hit = native.window_from_point(CX, CY)
    print("  WindowFromPoint -> %s" % describe(hit))
    chain(hit, "부모 계층")

    print("\n[2] 클릭 통과 ON")
    native.set_click_through(a_hwnd, True)
    root.update()
    hit2 = native.window_from_point(CX, CY)
    print("  WindowFromPoint -> %s" % describe(hit2))
    chain(hit2, "부모 계층")
    print("\n  A 의 ex-style = 0x%08x (TRANSPARENT=%s)" % (
        native.u32.GetWindowLongPtrW(a_hwnd, native.GWL_EXSTYLE),
        bool(native.u32.GetWindowLongPtrW(a_hwnd, native.GWL_EXSTYLE)
             & native.WS_EX_TRANSPARENT),
    ))

    # 알파를 낮춘 레이어드 창에서도 같은지
    print("\n[3] alpha=0.6 + 클릭 통과 ON")
    a.attributes("-alpha", 0.6)
    root.update()
    hit3 = native.window_from_point(CX, CY)
    print("  WindowFromPoint -> %s" % describe(hit3))

    print("\n[4] alpha=0.6 + 클릭 통과 OFF (레이어드만)")
    native.set_click_through(a_hwnd, False)
    root.update()
    hit4 = native.window_from_point(CX, CY)
    print("  WindowFromPoint -> %s" % describe(hit4))

    print("\n[5] 커버 영역 밖 (B 만 있는 지점) - 대조군")
    hit5 = native.window_from_point(RECT[0] + 10, RECT[1] + RECT[3] - 10)
    print("  WindowFromPoint -> %s" % describe(hit5))

    def finish():
        b.destroy()
        a.destroy()
        root.destroy()

    root.after(800, finish)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
