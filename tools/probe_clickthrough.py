"""클릭 통과를 **실제 마우스 입력 주입**으로 검증한다.

`WindowFromPoint` 는 래퍼가 아니라 그 자식(TkChild)을 돌려주고, 자식에는
`WS_EX_TRANSPARENT` 가 없으므로 이 API로는 절대 판정할 수 없다 (probe_hittest.py 참고).
게다가 `HTTRANSPARENT` 는 문서상 **같은 스레드**의 아래 창으로만 전달되므로 같은
프로세스 안에서 실험하면 거짓 양성이 나온다. 그래서 아래 창은 **별도 프로세스**가
소유하고, 커서를 실제로 옮겨 클릭한 뒤 누가 받았는지 확인한다.

    python tools/probe_clickthrough.py

시나리오
  [대조군]  커버를 숨긴다        -> 아래 창이 클릭을 받아야 한다 (하네스 자체 검증)
  [OFF]     편집 모드            -> 커버가 클릭을 받아야 한다
  [ON]      플레이 모드          -> 아래 창이 클릭을 받아야 한다 (핵심 기능)
"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RECT = (240, 180, 560, 320)
CX = RECT[0] + RECT[2] // 2
CY = RECT[1] + RECT[3] // 2
REPORT = os.path.join(os.environ.get("TEMP", "."), "lanecover-clickthrough.txt")
READY_TIMEOUT = 20.0


# ============================================================ 아래 창 (별도 프로세스)

def run_target(rect, report):
    """클릭을 받으면 파일에 기록하는 평범한 Win32 창. 별도 프로세스로 실행된다."""
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    LRESULT = ctypes.c_ssize_t

    u32.CreateWindowExW.restype = wintypes.HWND
    u32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    u32.DefWindowProcW.restype = LRESULT
    u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    u32.RegisterClassW.argtypes = [ctypes.c_void_p]
    u32.SetWindowPos.restype = wintypes.BOOL
    u32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]

    def log(line):
        # 매번 열고 닫는다. 검증 쪽에서 파일을 비우므로 핸들을 붙잡고 있으면 어긋난다.
        with open(report, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)
    state = {"hwnd": 0}

    @WNDPROC
    def proc(hwnd, msg, wparam, lparam):
        if msg == 0x0201:  # WM_LBUTTONDOWN
            log("TARGET_DOWN")
        elif msg == 0x0084:  # WM_NCHITTEST
            result = u32.DefWindowProcW(hwnd, msg, wparam, lparam)
            log("TARGET_NCHITTEST %d" % result)
            return result
        elif msg == 0x0002:  # WM_DESTROY
            u32.PostQuitMessage(0)
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]

    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = k32.GetModuleHandleW(None)
    wc.lpszClassName = "LaneCoverClickTarget"
    wc.hbrBackground = 6  # COLOR_WINDOW+1, 안 칠하면 아래 창이 안 보인다
    if not u32.RegisterClassW(ctypes.byref(wc)):
        log("ERROR RegisterClassW %d" % ctypes.get_last_error())
        return 2

    # WS_POPUP 만 주면 창이 **보이지 않는다**. 보이지 않는 창은 히트 테스트에서 빠져
    # 클릭을 절대 받을 수 없으므로 WS_VISIBLE 이 반드시 있어야 한다.
    hwnd = u32.CreateWindowExW(
        0, wc.lpszClassName, "click target", 0x80000000 | 0x10000000,  # WS_POPUP|WS_VISIBLE
        rect[0], rect[1], rect[2], rect[3],
        None, None, wc.hInstance, None,
    )
    if not hwnd:
        log("ERROR CreateWindowExW %d" % ctypes.get_last_error())
        return 2
    u32.ShowWindow(hwnd, 5)  # SW_SHOW
    u32.UpdateWindow(hwnd)
    state["hwnd"] = hwnd
    log("READY 0x%x visible=%d" % (hwnd, u32.IsWindowVisible(hwnd)))

    timeout = time.time() + 120
    msg = wintypes.MSG()
    while time.time() < timeout:
        if u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == 0x0012:  # WM_QUIT
                break
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
        else:
            time.sleep(0.005)
    return 0


# ============================================================ 오케스트레이터

def click_at(x, y):
    """실제 커서를 옮기고 좌클릭을 주입한다."""
    u32 = ctypes.WinDLL("user32", use_last_error=True)

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    u32.SendInput.restype = wintypes.UINT
    u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    u32.SetCursorPos.restype = wintypes.BOOL

    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.06)
    for flag in (0x0002, 0x0004):  # LEFTDOWN, LEFTUP
        item = INPUT()
        item.type = 0  # INPUT_MOUSE
        item.u.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
        if u32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) != 1:
            raise OSError("SendInput 실패 %d" % ctypes.get_last_error())
        time.sleep(0.06)


def read_report():
    if not os.path.isfile(REPORT):
        return []
    with open(REPORT, "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


def reset_report():
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("")


def wait_ready(deadline=READY_TIMEOUT):
    start = time.time()
    while time.time() - start < deadline:
        for line in read_report():
            if line.startswith("READY"):
                return True
        time.sleep(0.1)
    return False


def pump(root, seconds):
    """Tk 이벤트 루프를 돌리며 기다린다.

    ``time.sleep()`` 으로 메인 스레드를 멈추면 Tk 가 메시지를 펌프하지 못해 클릭 메시지가
    큐에 쌓였다가 **다음 시행에서** 서브클래스로 전달된다 (한 시행씩 밀림). 반드시 펌프한다.
    """
    end = time.time() + seconds
    while time.time() < end:
        try:
            root.update()
        except Exception:
            return
        time.sleep(0.01)


def main():
    import tkinter as tk

    from lane_cover import native

    native.enable_dpi_awareness()

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("")

    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--target",
         str(RECT[0]), str(RECT[1]), str(RECT[2]), str(RECT[3]), REPORT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    root = tk.Tk()
    root.withdraw()

    saved_cursor = None
    verdicts = []
    try:
        if not wait_ready():
            print("아래 창이 뜨지 않았습니다. 보고서: %r" % read_report())
            return 2
        target_lines = [l for l in read_report() if l.startswith("READY")]
        target_hwnd = int(target_lines[0].split()[1], 16)
        print("아래 창(별도 프로세스) = 0x%x   커서 클릭 지점 = (%d,%d)" % (target_hwnd, CX, CY))

        pt = wintypes.POINT()
        native.u32.GetCursorPos(ctypes.byref(pt))
        saved_cursor = (pt.x, pt.y)

        # 커버: 실제 앱과 같은 구성 (overrideredirect + topmost)
        cover = tk.Toplevel(root)
        cover.overrideredirect(True)
        cover.attributes("-topmost", True)
        cover.configure(bg="#502020")
        cover.update_idletasks()
        native.move_window(native.top_hwnd(cover), *RECT)
        root.update()
        cover_hwnd = native.top_hwnd(cover)
        print("커버 = 0x%x\n" % cover_hwnd)

        # 커버가 클릭을 받는지도 직접 봐야 한다. 자식 HWND(TkChild)에 걸어 둔다.
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)
        cover_hits = {"count": 0, "nchittest": []}
        cover_child = native.child_hwnd(cover)
        old_proc = native.u32.GetWindowLongPtrW(cover_child, native.GWLP_WNDPROC)

        @WNDPROC
        def cover_proc(hwnd, msg, wparam, lparam):
            if msg == 0x0201:  # WM_LBUTTONDOWN
                cover_hits["count"] += 1
            elif msg == 0x0084:  # WM_NCHITTEST
                result = native.u32.CallWindowProcW(old_proc, hwnd, msg, wparam, lparam)
                cover_hits["nchittest"].append(result)
                return result
            return native.u32.CallWindowProcW(old_proc, hwnd, msg, wparam, lparam)

        native.u32.SetWindowLongPtrW(
            cover_child, native.GWLP_WNDPROC, ctypes.cast(cover_proc, ctypes.c_void_p).value
        )

        def trial(label, expect, expect_cover_clicks):
            reset_report()
            cover_hits["count"] = 0
            cover_hits["nchittest"] = []
            pump(root, 0.3)
            reset_report()          # 커서 이동 중 생긴 잡음(NCHITTEST)은 버린다
            cover_hits["count"] = 0
            click_at(CX, CY)
            pump(root, 0.5)

            lines = read_report()
            target_got = "TARGET_DOWN" in lines
            cover_got = cover_hits["count"] > 0
            hit = "커버" if cover_got else ("아래 창" if target_got else "아무도 안 받음")
            ok = (cover_got, target_got) == expect_cover_clicks
            verdicts.append((label, ok, hit))
            print("  %-22s -> %-14s %s" % (label, hit, "OK" if ok else "실패 (기대: %s)" % expect))
            nchit = [l.split()[-1] for l in lines if "NCHITTEST" in l]
            if nchit:
                print("      아래 창이 받은 WM_NCHITTEST: %s" % ", ".join(nchit))
            cover_nchit = cover_hits["nchittest"]
            if cover_nchit:
                print("      커버가 받은 WM_NCHITTEST: %s"
                      % ", ".join(str(v) for v in cover_nchit))
            cover_hits["nchittest"] = []

        print("[대조군] 커버를 딴 데로 치움 -> 아래 창이 받아야 정상 (하네스 검증)")
        # withdraw/deiconify 를 쓰지 않는다. Tk 는 재매핑 시 래퍼 HWND 를 파괴·재생성할 수
        # 있고(제약 2), 그러면 이후 모든 핸들과 서브클래스가 어긋난다. 위치만 옮긴다.
        native.move_window(cover_hwnd, 0, 0, 100, 60)
        pump(root, 0.3)
        probe_hit = native.window_from_point(CX, CY)
        print("      이 지점의 창 = 0x%x (아래 창 = 0x%x)" % (probe_hit, target_hwnd))
        if probe_hit != target_hwnd:
            print("      [경고] 아래 창이 이 지점에 없습니다. 클릭 대상이 잘못됐습니다.")
        trial("커버 치움", "아래 창", (False, True))
        native.move_window(cover_hwnd, *RECT)
        pump(root, 0.3)

        print("\n[편집 모드] 클릭 통과 OFF -> 커버가 받아야 정상")
        native.set_click_through(cover_hwnd, False)
        pump(root, 0.2)
        print("      이 지점의 창 = 0x%x (커버 = 0x%x, 아래 창 = 0x%x)" % (
            native.window_from_point(CX, CY), cover_hwnd, target_hwnd))
        trial("편집 모드 (통과 OFF)", "커버", (True, False))
        print("      커버 ex-style = 0x%08x" %
              native.u32.GetWindowLongPtrW(cover_hwnd, native.GWL_EXSTYLE))

        print("\n[플레이 모드] 클릭 통과 ON -> 아래 창이 받아야 정상 (핵심 기능)")
        native.set_click_through(cover_hwnd, True)
        pump(root, 0.2)
        trial("플레이 모드 (통과 ON)", "아래 창", (False, True))
        print("      커버 ex-style = 0x%08x (TRANSPARENT=%s)" % (
            native.u32.GetWindowLongPtrW(cover_hwnd, native.GWL_EXSTYLE),
            bool(native.u32.GetWindowLongPtrW(cover_hwnd, native.GWL_EXSTYLE)
                 & native.WS_EX_TRANSPARENT)))

        print("\n[플레이 모드 + alpha] 레이어드 상태에서도 같은지")
        # Tk 는 alpha 를 바꿀 때 **캐시된 ex-style 로 GWL_EXSTYLE 을 통째로 다시 쓴다**
        # (제약 5). 그래서 시각 속성을 건드린 뒤에는 클릭 통과를 다시 걸어야 한다.
        # 앱의 _apply_visual_attributes() -> _sync_native() 순서를 그대로 흉내낸다.
        cover.attributes("-alpha", 0.6)
        root.update()
        native.set_click_through(cover_hwnd, True, opacity=0.6)
        pump(root, 0.2)
        print("      ex-style = 0x%08x (LAYERED=%s TRANSPARENT=%s)" % (
            native.u32.GetWindowLongPtrW(cover_hwnd, native.GWL_EXSTYLE),
            bool(native.u32.GetWindowLongPtrW(cover_hwnd, native.GWL_EXSTYLE)
                 & native.WS_EX_LAYERED),
            bool(native.u32.GetWindowLongPtrW(cover_hwnd, native.GWL_EXSTYLE)
                 & native.WS_EX_TRANSPARENT)))
        trial("플레이 모드 (alpha 0.6)", "아래 창", (False, True))

        print("\n[회귀] alpha 변경이 클릭 통과를 지우는지 (다시 안 걸고 그냥 클릭)")
        cover.attributes("-alpha", 0.8)   # 일부러 _sync_native 를 부르지 않는다
        pump(root, 0.2)
        ex_now = native.u32.GetWindowLongPtrW(cover_hwnd, native.GWL_EXSTYLE)
        trial("alpha 만 바꿈 (재적용 X)", "아래 창", (False, True))
        print("      ex-style = 0x%08x -> TRANSPARENT=%s (False 면 Tk 가 지운 것)" % (
            ex_now, bool(ex_now & native.WS_EX_TRANSPARENT)))

        try:
            native.u32.SetWindowLongPtrW(cover_child, native.GWLP_WNDPROC, old_proc)
        except BaseException:
            pass
    finally:
        if saved_cursor:
            native.u32.SetCursorPos(saved_cursor[0], saved_cursor[1])
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
        try:
            root.destroy()
        except tk.TclError:
            pass

    print("\n" + "=" * 84)
    failed = [v for v in verdicts if not v[1]]
    for label, _ok, hit in verdicts:
        print("  %-24s %s" % (label, hit))
    print("=" * 84)
    print("결과: %d개 중 %d개 실패" % (len(verdicts), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--target":
        sys.exit(run_target(
            (int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])),
            sys.argv[6],
        ))
    sys.exit(main())
