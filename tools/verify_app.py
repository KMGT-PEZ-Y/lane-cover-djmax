"""실제 LaneCoverApp 으로 클릭 통과와 최상단을 최종 검증한다.

합성 창이 아니라 **진짜 앱**을 띄우고, 별도 프로세스를 아래 창으로 둔 뒤 실제 마우스
입력을 주입해 누가 클릭을 받는지 확인한다. 앱의 ``_sync_native`` 경로를 그대로 지나간다.

    python tools/verify_app.py
"""

import ctypes
import os
import queue
import subprocess
import sys
import tempfile
import time
import tkinter as tk

_SANDBOX = tempfile.mkdtemp(prefix="lanecover-verify-")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_clickthrough import (  # noqa: E402
    READY_TIMEOUT, RECT, CX, CY, REPORT, click_at, read_report, reset_report,
)

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def describe(hwnd):
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    buf = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, buf, 256)
    from lane_cover import native
    return "0x%x %s %r ex=0x%08x" % (
        hwnd, buf.value or "?", native.get_window_rect(hwnd),
        native.u32.GetWindowLongPtrW(hwnd, native.GWL_EXSTYLE))



def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-4s %-52s %s" % ("PASS" if ok else "FAIL", name, detail))


def main():
    from lane_cover import app as app_mod, native

    native.enable_dpi_awareness()

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("")

    target = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "probe_clickthrough.py"), "--target",
         str(RECT[0]), str(RECT[1]), str(RECT[2]), str(RECT[3]), REPORT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    events = queue.Queue()
    saved = None
    try:
        start = time.time()
        target_hwnd = 0
        while time.time() - start < READY_TIMEOUT:
            for line in read_report():
                if line.startswith("READY"):
                    target_hwnd = int(line.split()[1], 16)
            if target_hwnd:
                break
            time.sleep(0.1)
        if not target_hwnd:
            print("아래 창이 뜨지 않았습니다")
            return 2
        print("아래 창 = 0x%x (별도 프로세스)\n" % target_hwnd)

        app = app_mod.LaneCoverApp(events=events, on_quit=None)
        warnings = []
        app.show_warning = lambda title, message: warnings.append((title, message))
        app.allow_play_mode = True

        pt = ctypes.wintypes.POINT()
        native.u32.GetCursorPos(ctypes.byref(pt))
        saved = (pt.x, pt.y)

        # 커버를 아래 창과 정확히 겹치게 둔다
        app._geom = list(RECT)
        app._apply_geom()
        app.root.update()

        # 커버가 클릭을 받는지 직접 보려고 **래퍼와 자손 전부**에 서브클래싱을 건다.
        # Tk 계층은 3단이고(래퍼 -> 톱레벨 창 -> Canvas 창) 마우스를 받는 것은 가장 깊은
        # 창이다. 한 곳에만 걸면 클릭을 못 본다 (실측).
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                     ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        hits = {"n": 0}
        hooked = []
        for hwnd in native.widget_hwnds(app.root):
            previous = native.u32.GetWindowLongPtrW(hwnd, native.GWLP_WNDPROC)
            if not previous:
                continue

            def _make(prev):
                @WNDPROC
                def _proc(h, msg, wparam, lparam):
                    if msg == 0x0201:  # WM_LBUTTONDOWN
                        hits["n"] += 1
                    return native.u32.CallWindowProcW(prev, h, msg, wparam, lparam)
                return _proc

            proc = _make(previous)
            native.u32.SetWindowLongPtrW(hwnd, native.GWLP_WNDPROC,
                                         ctypes.cast(proc, ctypes.c_void_p).value)
            hooked.append((hwnd, previous, proc))
        print("클릭 감시 중인 창 %d개: %s\n" % (
            len(hooked), ["0x%x" % h for h, _o, _p in hooked]))
        app.root.update()

        def trial(label, expect_cover):
            hits["n"] = 0
            reset_report()
            end = time.time() + 0.3
            while time.time() < end:
                app.root.update()
                time.sleep(0.01)
            reset_report()
            hits["n"] = 0
            print("      [진단] 커버 rect=%r 모드=%s ex=0x%08x" % (
                native.get_window_rect(app._hwnd), app.cfg.mode,
                native.u32.GetWindowLongPtrW(app._hwnd, native.GWL_EXSTYLE)))
            hit = native.window_from_point(CX, CY)
            node, chain = hit, []
            for _ in range(4):
                if not node:
                    break
                chain.append(describe(node))
                node = native.u32.GetParent(node)
            print("      [진단] 지점의 창=0x%x / %s" % (hit, " <- ".join(chain)))
            click_at(CX, CY)
            end = time.time() + 0.5
            while time.time() < end:
                app.root.update()
                time.sleep(0.01)
            target_got = "TARGET_DOWN" in read_report()
            cover_got = hits["n"] > 0
            who = "커버" if cover_got else ("아래 창" if target_got else "아무도 안 받음")
            check(label, cover_got == expect_cover, "-> %s" % who)

        def exstyle():
            return native.u32.GetWindowLongPtrW(app._hwnd, native.GWL_EXSTYLE)

        print("[대조군] 커버를 딴 데로 치움")
        native.move_window(app._hwnd, 0, 0, 120, 80)
        app.root.update()
        probe = native.window_from_point(CX, CY)
        check("아래 창이 이 지점에 있음", probe == target_hwnd,
              "0x%x vs 0x%x" % (probe, target_hwnd))
        trial("커버 치움 -> 아래 창", False)
        native.move_window(app._hwnd, *RECT)
        app._geom = list(RECT)
        app.root.update()

        print("\n[편집 모드] 클릭 통과 OFF")
        app.set_mode("edit", save=False)
        app.root.update()
        check("TRANSPARENT 없음", not (exstyle() & native.WS_EX_TRANSPARENT),
              "exstyle=0x%08x" % exstyle())
        trial("편집 모드 -> 커버가 받음", True)

        print("\n[플레이 모드] 클릭 통과 ON (핵심 기능)")
        app.cfg.opacity = 1.0          # Tk 가 LAYERED 를 안 켜는 조건
        app._apply_visual_attributes()
        app._sync_native()
        app.set_mode("play", save=False)
        app.root.update()
        check("LAYERED 켜짐", bool(exstyle() & native.WS_EX_LAYERED),
              "exstyle=0x%08x" % exstyle())
        check("TRANSPARENT 켜짐", bool(exstyle() & native.WS_EX_TRANSPARENT))
        check("TOOLWINDOW 유지 (Alt+Tab 숨김)", bool(exstyle() & native.WS_EX_TOOLWINDOW))
        check("TOPMOST 유지", bool(exstyle() & native.WS_EX_TOPMOST))
        check("창이 보임", bool(native.u32.IsWindowVisible(app._hwnd)))
        trial("플레이 모드 -> 아래 창이 받음", False)

        print("\n[플레이 모드 + 투명도 변경] 제약 5 회귀")
        app.cfg.opacity = 0.55
        app._apply_visual_attributes()
        app._sync_native()
        app.root.update()
        check("투명도 변경 후 TRANSPARENT 유지",
              bool(exstyle() & native.WS_EX_TRANSPARENT), "exstyle=0x%08x" % exstyle())
        trial("투명도 0.55 에서도 아래 창이 받음", False)

        print("\n[플레이 모드 + 컬러키]")
        app.cfg.color_key.enabled = True
        app.cfg.color_key.color = "#FF00FF"
        app._apply_visual_attributes()
        app._sync_native()
        app.root.update()
        check("컬러키 켠 뒤 창 유지", bool(native.u32.IsWindowVisible(app._hwnd)))
        trial("컬러키 켜도 아래 창이 받음", False)
        app.cfg.color_key.enabled = False

        print("\n[편집 복귀]")
        app.set_mode("edit", save=False)
        app.root.update()
        check("TRANSPARENT 해제", not (exstyle() & native.WS_EX_TRANSPARENT),
              "exstyle=0x%08x" % exstyle())
        check("TOOLWINDOW 유지", bool(exstyle() & native.WS_EX_TOOLWINDOW))
        trial("편집 복귀 -> 커버가 받음", True)

        for hwnd, previous, _proc in hooked:
            native.u32.SetWindowLongPtrW(hwnd, native.GWLP_WNDPROC, previous)
        app.quit()
        app._shutdown()
        try:
            app.root.destroy()
        except tk.TclError:
            pass
        if warnings:
            print("\n경고 대화상자 %d건: %r" % (len(warnings), warnings[:1]))
    finally:
        if saved:
            native.u32.SetCursorPos(saved[0], saved[1])
        target.terminate()
        try:
            target.wait(timeout=5)
        except subprocess.TimeoutExpired:
            target.kill()

    print("\n" + "=" * 78)
    failed = [r for r in results if not r[1]]
    print("총 %d개 중 %d개 실패" % (len(results), len(failed)))
    for name, _ok, detail in failed:
        print("  FAIL: %s  %s" % (name, detail))
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
