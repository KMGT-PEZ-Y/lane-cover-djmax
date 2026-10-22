"""클릭 통과 레시피 탐색 — Tk 를 배제한 순수 Win32 창으로 실측한다.

Tk 래퍼/자식 어느 쪽에 켜도 통과하지 않았다. `WS_EX_TRANSPARENT` 만으로는 히트 테스트에서
빠지지 않는 것으로 보이며, 가장 흔한 레시피인 `WS_EX_LAYERED` 동반 여부가 의심된다.
커버도 **별도 프로세스**의 순수 Win32 창으로 만들어 변수를 하나씩 바꾼다.

    python tools/probe_recipe.py

조합: (layered, transparent) x (자식 창 있음/없음)
"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
RECT = (240, 180, 560, 320)
CX = RECT[0] + RECT[2] // 2
CY = RECT[1] + RECT[3] // 2
REPORT = os.path.join(os.environ.get("TEMP", "."), "lanecover-recipe.txt")

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
LWA_ALPHA = 0x00000002
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010


def log(report, line):
    with open(report, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


# ------------------------------------------------------------------ 커버 (별도 프로세스)

def run_cover(x, y, w, h, report, layered, transparent, child_mode):
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    u32.CreateWindowExW.restype = wintypes.HWND
    u32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    u32.DefWindowProcW.restype = LRESULT
    u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.RegisterClassW.argtypes = [ctypes.c_void_p]
    u32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                               ctypes.c_ubyte, wintypes.DWORD]
    u32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                 wintypes.UINT, wintypes.UINT, wintypes.UINT]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]

    ex_wanted = 0
    if layered:
        ex_wanted |= WS_EX_LAYERED
    if transparent:
        ex_wanted |= WS_EX_TRANSPARENT
    ex_wanted |= WS_EX_TOPMOST | WS_EX_TOOLWINDOW

    def make_proc(tag):
        @WNDPROC
        def proc(hwnd, msg, wparam, lparam):
            if msg == 0x0201:  # WM_LBUTTONDOWN
                log(report, "%s_DOWN" % tag)
            if msg == 0x0002:
                u32.PostQuitMessage(0)
                return 0
            return u32.DefWindowProcW(hwnd, msg, wparam, lparam)
        return proc

    parent_proc = make_proc("COVER")
    child_proc = make_proc("COVERCHILD")

    wc = WNDCLASSW()
    wc.lpfnWndProc = parent_proc
    wc.hInstance = k32.GetModuleHandleW(None)
    wc.lpszClassName = "LaneCoverRawParent"
    wc.hbrBackground = 6
    if not u32.RegisterClassW(ctypes.byref(wc)):
        log(report, "ERROR RegisterClassW(parent) %d" % ctypes.get_last_error())
        return 2

    hwnd = u32.CreateWindowExW(ex_wanted, wc.lpszClassName, "raw cover",
                               WS_POPUP | WS_VISIBLE,
                               x, y, w, h, None, None, wc.hInstance, None)
    if not hwnd:
        log(report, "ERROR CreateWindowExW(parent) %d" % ctypes.get_last_error())
        return 2
    if layered:
        # LAYERED 만 켜고 이걸 안 부르면 창이 **보이지 않는다** (그리고 히트 테스트에서 빠진다).
        u32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)

    child_hwnd = 0
    if child_mode:
        wc2 = WNDCLASSW()
        wc2.lpfnWndProc = child_proc
        wc2.hInstance = k32.GetModuleHandleW(None)
        wc2.lpszClassName = "LaneCoverRawChild"
        wc2.hbrBackground = 6
        if not u32.RegisterClassW(ctypes.byref(wc2)):
            log(report, "ERROR RegisterClassW(child) %d" % ctypes.get_last_error())
            return 2
        # 1 = TRANSPARENT 만, 2 = LAYERED + TRANSPARENT (자식에도 레이어링이 필요한지)
        child_ex = WS_EX_TRANSPARENT
        if child_mode == 2:
            child_ex |= WS_EX_LAYERED
        child_hwnd = u32.CreateWindowExW(child_ex, wc2.lpszClassName, "",
                                         WS_CHILD | WS_VISIBLE, 0, 0, w, h,
                                         hwnd, None, wc2.hInstance, None)
        if not child_hwnd:
            log(report, "ERROR CreateWindowExW(child) %d" % ctypes.get_last_error())
            return 2
        if child_mode == 2:
            # 자식도 LAYERED 면 이걸 안 부르는 순간 **보이지 않는다**.
            u32.SetLayeredWindowAttributes(child_hwnd, 0, 255, LWA_ALPHA)

    u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    u32.UpdateWindow(hwnd)
    log(report, "READY cover=0x%x child=0x%x ex=0x%08x" % (
        hwnd, child_hwnd, u32.GetWindowLongPtrW(hwnd, -20)))

    timeout = time.time() + 60
    msg = wintypes.MSG()
    while time.time() < timeout:
        if u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == 0x0012:
                break
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        else:
            time.sleep(0.005)
    return 0


# ------------------------------------------------------------------ 오케스트레이터

def read_report():
    if not os.path.isfile(REPORT):
        return []
    with open(REPORT, "r", encoding="utf-8") as fh:
        return [l.strip() for l in fh if l.strip()]


def reset_report():
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("")


def wait_for(prefix, timeout=20.0):
    start = time.time()
    while time.time() - start < timeout:
        for line in read_report():
            if line.startswith(prefix):
                return line
        time.sleep(0.1)
    return None


def spawn(args):
    return subprocess.Popen([sys.executable] + args,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    from probe_clickthrough import click_at

    saved = None
    rows = []
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        pt = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        saved = (pt.x, pt.y)

        target = spawn([os.path.join(HERE, "probe_clickthrough.py"), "--target",
                        str(RECT[0]), str(RECT[1]), str(RECT[2]), str(RECT[3]), REPORT])
        if not wait_for("READY"):
            print("아래 창이 뜨지 않음")
            return 2

        print("순수 Win32 창으로 클릭 통과 레시피 실측 (아래 창은 별도 프로세스)\n")
        print("  %-40s %-14s %s" % ("조합", "누가 받았나", "판정"))
        print("  " + "-" * 66)

        child_names = {0: "없음", 1: "TRANSPARENT만", 2: "LAYERED+TRANSPARENT"}
        combos = [(True, True, 0), (True, True, 1), (True, True, 2),
                  (True, False, 1), (False, True, 2)]
        for layered, transparent, child_mode in combos:
            reset_report()
            cover = spawn([os.path.abspath(__file__), "--cover",
                           str(RECT[0]), str(RECT[1]), str(RECT[2]), str(RECT[3]),
                           REPORT, str(int(layered)), str(int(transparent)),
                           str(int(child_mode))])
            ready = wait_for("READY")
            if not ready:
                print("  커버 프로세스 실패")
                cover.terminate()
                continue
            time.sleep(0.35)
            reset_report()
            click_at(CX, CY)
            time.sleep(0.45)
            lines = read_report()
            target_got = "TARGET_DOWN" in lines
            cover_got = "COVER_DOWN" in lines or "COVERCHILD_DOWN" in lines
            who = "커버" if cover_got else ("아래 창" if target_got else "아무도 안 받음")
            label = "래퍼 LAYERED=%-5s TRANS=%-5s / 자식 %s" % (
                layered, transparent, child_names[child_mode])
            verdict = "통과" if (target_got and not cover_got) else ""
            rows.append((label, who, target_got and not cover_got))
            print("  %-40s %-14s %s" % (label, who, verdict))
            cover.terminate()
            try:
                cover.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cover.kill()
            time.sleep(0.2)

        target.terminate()
        try:
            target.wait(timeout=5)
        except subprocess.TimeoutExpired:
            target.kill()
    finally:
        if saved:
            ctypes.WinDLL("user32").SetCursorPos(saved[0], saved[1])

    print("\n" + "=" * 62)
    good = [r[0] for r in rows if r[2]]
    print("클릭이 통과한 조합: %s" % (", ".join(good) if good else "없음"))
    if not good:
        print("=> WS_EX_TRANSPARENT 로는 클릭 통과가 안 된다. 다른 수단이 필요하다.")
    return 0 if good else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cover":
        sys.exit(run_cover(
            int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]),
            sys.argv[6],
            bool(int(sys.argv[7])), bool(int(sys.argv[8])), int(sys.argv[9]),
        ))
    sys.exit(main())
