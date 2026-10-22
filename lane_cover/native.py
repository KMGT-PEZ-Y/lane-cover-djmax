"""Win32/ctypes 단일 계층.

이 프로그램에서 OS와 직접 맞닿는 부분은 전부 여기에 모은다. tkinter를 아는 코드는
이 모듈의 함수만 호출하고 ctypes를 직접 만지지 않는다.

Tk 8.6 소스(``win/tkWinWm.c``)와 MSDN을 근거로 확인한 제약이 몇 가지 있고, 아래 구현은
그것들을 전제로 짜여 있다. 각 항목에 이유를 주석으로 남겨 둔다.
"""

import ctypes
from ctypes import wintypes

from .log import get_logger

log = get_logger("native")

# --------------------------------------------------------------------------- DLL

# use_last_error=True 여야 RegisterHotKey 실패의 1409(ERROR_HOTKEY_ALREADY_REGISTERED)를
# ctypes.get_last_error() 로 정확히 읽을 수 있다. kernel32.GetLastError() 를 나중에 부르는
# 방식은 그 사이 다른 호출이 에러를 덮어써서 믿을 수 없다.
u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# 64비트에서 LONG_PTR. ctypes 는 restype 기본값이 c_int 라서, 이걸 명시하지 않으면
# GetWindowLongPtrW(GWLP_WNDPROC) 가 포인터를 32비트로 잘라내고, 그 값을 CallWindowProcW 에
# 넘기는 순간 쓰레기 주소로 점프해 프로세스가 죽는다.
LONG_PTR = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t

u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
u32.GetWindowLongPtrW.restype = LONG_PTR
u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
u32.SetWindowLongPtrW.restype = LONG_PTR

u32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
u32.SetWindowPos.restype = wintypes.BOOL

u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.GetWindowRect.restype = wintypes.BOOL

u32.GetParent.argtypes = [wintypes.HWND]
u32.GetParent.restype = wintypes.HWND

u32.CallWindowProcW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
u32.CallWindowProcW.restype = LRESULT

u32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
u32.DefWindowProcW.restype = LRESULT

shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
shell32.DragAcceptFiles.restype = None
shell32.DragQueryFileW.argtypes = [
    ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT,
]
shell32.DragQueryFileW.restype = wintypes.UINT
shell32.DragFinish.argtypes = [ctypes.c_void_p]
shell32.DragFinish.restype = None

# --------------------------------------------------------------------------- 상수

GWL_EXSTYLE = -20
GWLP_WNDPROC = -4

WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_NOOWNERZORDER = 0x0200
SWP_NOSENDCHANGING = 0x0400

HWND_TOPMOST = -1

WM_DROPFILES = 0x0233
WM_HOTKEY = 0x0312
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_TRAYICON = 0x0400 + 1  # WM_APP + 1

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# DPI_AWARENESS_CONTEXT_* 는 음수 HANDLE 이다.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_PER_MONITOR_DPI_AWARE = 2

# 드롭 대상으로 인정할 확장자. 커버 텍스처로 쓸 만한 것만.
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")

IS_WINDOWS = hasattr(ctypes, "WinDLL")


# --------------------------------------------------------------------------- 핸들


_ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
u32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUMPROC, wintypes.LPARAM]
u32.EnumChildWindows.restype = wintypes.BOOL


def child_hwnds(hwnd):
    """``hwnd`` 의 모든 자손 HWND (깊이 우선 순서).

    Tk 톱레벨의 계층은 **3단**이다: ``TkTopLevel``(래퍼) -> ``TkChild``(톱레벨 창) ->
    ``TkChild``(**Canvas 등 위젯 창**). 실제로 마우스와 드롭을 받는 것은 가장 깊은 창이다.
    그래서 특정 창 하나에만 걸면 안 되고 전부 훑어야 한다.
    """
    found = []
    if not hwnd:
        return found

    def _collect(child, _lparam):
        found.append(child)
        return True

    callback = _ENUMPROC(_collect)
    try:
        u32.EnumChildWindows(hwnd, callback, 0)
    finally:
        del callback  # 호출이 동기라 이 시점이면 안전하다
    return found


def top_hwnd(widget):
    """진짜 톱레벨 HWND (Tk 의 래퍼 윈도우).

    Tk 는 톱레벨마다 래퍼 윈도우를 만들고 Tk 윈도우를 그 **자식**으로 재부모화한다.
    ``winfo_id()`` 가 돌려주는 건 자식이고, 래퍼만이 실제 z-order 와 ex-style 을 가진다.
    ``SetWindowPos``/``GetSetWindowLongPtrW`` 는 반드시 이 핸들에 써야 한다.
    """
    try:
        # WmFrameCmd 가 "0x%zx" 형식으로 wmPtr->wrapper 를 돌려준다.
        return int(widget.wm_frame(), 16)
    except Exception:
        # 이론상 오지 않는 경로. GetParent 로 보정한다.
        try:
            child = widget.winfo_id()
            return u32.GetParent(child) or child
        except Exception:
            return 0


def child_hwnd(widget):
    """Tk 윈도우 자체의 HWND (래퍼의 자식)."""
    try:
        return widget.winfo_id()
    except Exception:
        return 0


def widget_hwnds(widget):
    """위젯의 래퍼와 그 자손 전부.

    마우스와 드롭을 실제로 받는 창이 어느 것인지 미리 알 수 없으므로(Canvas 등 위젯이
    자기 창을 가진다) 입력을 걸 때는 항상 이 목록 전체에 건다.
    """
    root = top_hwnd(widget)
    return [h for h in [root] + child_hwnds(root) if h]


# --------------------------------------------------------------------------- DPI


def enable_dpi_awareness():
    """프로세스를 DPI 인식으로 만든다. **``Tk()`` 생성 전에** 호출해야 한다.

    프로세스당 한 번만 성공하며, 두 번째 호출은 ERROR_ACCESS_DENIED 로 실패한다.
    DPI 인식이 되면 Tk 좌표가 물리 픽셀이 되어 저장한 x/y/w/h 가 배율 변경에도 안정적이다.
    (``tk scaling`` 은 폰트의 포인트->픽셀 변환만 담당하고 geometry 에는 영향이 없다.)

    실제 적용된 수준을 문자열로 돌려준다 (진단용).
    """
    # 빌드된 exe 에는 PerMonitorV2 매니페스트가 박혀 있어 이 함수에 오기 전에 이미 설정된다.
    # 그 상태에서 다시 설정하려 하면 ERROR_ACCESS_DENIED 로 실패하고, 아래 폴백 체인이
    # 엉뚱하게 "system" 을 돌려주어 진단을 오해하게 만든다. 먼저 현재 상태를 확인한다.
    if read_dpi_awareness() == "per-monitor":
        return "already-per-monitor (매니페스트)"

    # per-monitor v2. SetProcessDpiAwareness(2) 는 v1 이라 v2 는 이 API 로만 설정할 수 있다.
    try:
        fn = u32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wintypes.BOOL
        if fn(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            return "per-monitor-v2"
    except AttributeError:
        pass

    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return "per-monitor-v1"
    except (AttributeError, OSError):
        pass

    try:
        if u32.SetProcessDPIAware():
            return "system"
    except AttributeError:
        pass
    return "none"


def read_dpi_awareness():
    """실제로 적용된 인식 수준을 읽어 온다 (로그/설정 창 진단용)."""
    names = {0: "unaware", 1: "system", 2: "per-monitor"}
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.GetProcessDpiAwareness.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        shcore.GetProcessDpiAwareness.restype = ctypes.c_long
        value = ctypes.c_int()
        if shcore.GetProcessDpiAwareness(None, ctypes.byref(value)) == 0:
            return names.get(value.value, "unknown(%d)" % value.value)
    except (AttributeError, OSError):
        pass
    return "unknown"


# --------------------------------------------------------------------------- 창 조작


def _get_exstyle(hwnd):
    return u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)


LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

u32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD,
]
u32.SetLayeredWindowAttributes.restype = wintypes.BOOL


def colorref(hex_color):
    """``'#RRGGBB'`` -> ``COLORREF``(0x00BBGGRR). 형식이 틀리면 0."""
    text = (hex_color or "").lstrip("#")
    if len(text) != 6:
        return 0
    try:
        red = int(text[0:2], 16)
        green = int(text[2:4], 16)
        blue = int(text[4:6], 16)
    except ValueError:
        return 0
    return red | (green << 8) | (blue << 16)


def apply_layered_attributes(hwnd, opacity=1.0, color_key=None):
    """레이어드 창을 보이게 만든다 (알파/컬러키 설정).

    ``WS_EX_LAYERED`` 만 켜고 이걸 부르지 않으면 창이 **보이지 않는다** (MSDN).
    Tk 가 ``-alpha``/``-transparentcolor`` 로 이미 레이어링을 켠 경우에는 Tk 가 같은 일을
    하므로 부를 필요가 없다. 우리가 대신 켤 때만 쓴다.
    """
    # Tk 와 같은 반올림을 쓴다 (Tk 는 0.3 -> 77 로 올림한다). 값이 어긋나면 나중에 Tk 가
    # 같은 알파를 다시 설정할 때 "이미 그 값" 이라고 판단해 건너뛸 수 있다.
    alpha = max(0, min(255, int(float(opacity) * 255 + 0.5)))
    flags = LWA_ALPHA
    key = 0
    if color_key:
        key = colorref(color_key)
        if key:
            flags |= LWA_COLORKEY
    u32.SetLayeredWindowAttributes(hwnd, key, alpha, flags)


def set_click_through(hwnd, enabled, opacity=1.0, color_key=None):
    """마우스 입력을 창 아래로 통과시키고 포커스를 뺏지 않게 한다.

    **``WS_EX_LAYERED`` 와 ``WS_EX_TRANSPARENT`` 가 둘 다 있어야 통과한다.**
    ``WS_EX_TRANSPARENT`` 단독은 아무 효과가 없다 — MSDN 상 그 비트의 본래 뜻은 "형제 창이
    다 칠해질 때까지 칠하지 않는다"이고, 히트 테스트에서 빠지는 것은 **레이어드 창**의
    성질이기 때문이다. 실제 마우스 입력을 주입해 조합을 전수 실측해서 확인했다
    (``tools/probe_recipe.py``: LAYERED+TRANSPARENT 만 통과, 각각 단독은 실패).

    그런데 Tk 는 ``-alpha`` 가 1.0 이고 컬러키도 없으면 래퍼에 ``WS_EX_LAYERED`` 를
    **켜지 않는다**. 그래서 그 경우에는 우리가 직접 켜고, 창이 사라지지 않도록
    ``SetLayeredWindowAttributes`` 로 지금 설정과 같은 알파/컬러키를 걸어 준다.

    자식 HWND 는 건드리지 않는다. 히트 테스트가 자식에서 멈추긴 하지만 래퍼에 켠
    LAYERED+TRANSPARENT 만으로 클릭이 통과하는 것을 실측으로 확인했다.
    """
    if not hwnd:
        return
    ex = _get_exstyle(hwnd)
    if enabled:
        new = ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    else:
        # WS_EX_NOACTIVATE 를 반드시 같이 끈다. 켜둔 채로 두면 키 입력이 Tk 에 닿지 않아
        # 편집 모드의 방향키 미세조정이 먹통이 된다. LAYERED 는 남겨 둔다 — 단독으로는
        # 클릭을 통과시키지 않고(실측), Tk 가 자기 ex-style 을 쓸 때 알아서 정리한다.
        new = ex & ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
    if new != ex:
        u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)

    # Tk 가 레이어링을 켜지 않았을 때만 우리가 대신 보이게 만든다.
    if enabled and not (ex & WS_EX_LAYERED):
        apply_layered_attributes(hwnd, opacity, color_key)

    # 프레임 스타일 변경에만 SWP_FRAMECHANGED 가 필수지만, 비용이 없어 보험으로 건다.
    u32.SetWindowPos(
        hwnd, None, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def assert_tool_window(hwnd):
    """작업표시줄과 Alt+Tab 에서 숨긴다.

    override-redirect 창은 원래 ``WS_EX_TOOLWINDOW`` 를 갖지만, **Tk 는 ``-alpha`` 를 처음
    설정할 때 캐시된 ex-style 로 ``GWL_EXSTYLE`` 을 통째로 덮어써 이 비트를 잃는다** (실측:
    0x00000088 -> 0x00080008). 그대로 두면 커버가 Alt+Tab 과 작업표시줄에 나타난다.
    ``WS_EX_APPWINDOW`` 는 절대 켜지 않는다 — 그러면 반대로 확실히 나타난다.
    """
    if not hwnd:
        return
    ex = _get_exstyle(hwnd)
    if ex & WS_EX_TOOLWINDOW:
        return
    u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW)


def assert_topmost(hwnd):
    """최상단을 다시 주장한다.

    게임이 전체화면 창 모드에서 topmost 를 다시 가져가는 경우를 대비해 주기적으로 호출한다.
    ``SWP_NOACTIVATE`` 로 게임 포커스를 절대 뺏지 않는다.
    """
    if not hwnd:
        return
    u32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING,
    )


def move_window(hwnd, x, y, w, h):
    """드래그 중 위치/크기 적용.

    ``SWP_NOREDRAW`` 를 쓰지 않는다. Tk 의 래퍼 윈도우 클래스는 ``CS_HREDRAW|CS_VREDRAW`` 라
    리사이즈 시 전체 무효화로 다시 그리는 것에 의존하는데, 무효화를 억제하면 새로 드러난
    영역이 깨진 채 남는다. layered 창은 시스템이 합성하므로 tearing 도 없다.

    ``SetWindowPos`` 는 ``WM_WINDOWPOSCHANGED`` 를 동기 전송하고 Tk 의 ``ConfigureTopLevel()``
    이 그것을 처리해 내부 상태를 맞추므로, 메인 스레드에서 부르는 한 Tk 와 어긋나지 않는다.
    """
    if not hwnd:
        return
    u32.SetWindowPos(
        hwnd, None, int(x), int(y), int(w), int(h), SWP_NOACTIVATE | SWP_NOZORDER
    )


def get_window_rect(hwnd):
    """``(x, y, w, h)`` 정수. 음수 좌표(주 모니터 왼쪽/위 모니터)도 그대로 나온다."""
    rect = wintypes.RECT()
    if hwnd and u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    return None


# POINT 를 값으로 받는다 (byref 아님).
u32.WindowFromPoint.argtypes = [wintypes.POINT]
u32.WindowFromPoint.restype = wintypes.HWND


def window_from_point(x, y):
    """해당 화면 좌표에서 가장 깊은 창.

    **클릭 통과 판정에 쓰면 안 된다.** Tk 톱레벨은 래퍼 밑에 자식(TkChild)을 두는데 이 함수는
    그 자식을 돌려주고, ``WS_EX_TRANSPARENT`` 를 래퍼에 켜도 여기서는 그대로 나온다.
    (실측 확인. 통과 여부는 실제 입력을 주입해서만 알 수 있다: ``tools/probe_clickthrough.py``.)

    지금은 "이 좌표에 어떤 창이 있는가"를 보는 진단용으로만 쓴다.
    """
    return u32.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0


def is_descendant_of(hwnd, ancestor):
    """``hwnd`` 가 ``ancestor`` 이거나 그 자손인지. 진단용."""
    if not hwnd or not ancestor:
        return False
    current = hwnd
    for _ in range(64):  # 순환 방지
        if current == ancestor:
            return True
        parent = u32.GetParent(current)
        if not parent:
            return False
        current = parent
    return False


SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

u32.GetSystemMetrics.argtypes = [ctypes.c_int]
u32.GetSystemMetrics.restype = ctypes.c_int


def virtual_screen_rect():
    """모든 모니터를 아우르는 가상 화면 ``(x, y, w, h)``.

    주 모니터의 ``winfo_screenwidth()`` 를 쓰면 보조 모니터가 사각지대가 되어 커버를
    화면 밖으로 잃어버릴 수 있다. 클램프와 스냅은 이 값을 기준으로 한다.
    """
    try:
        x = u32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = u32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = u32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = u32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return (x, y, w, h)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- 파일 드롭

# WNDPROC 는 반드시 살아 있어야 한다. GC 로 사라지면 Windows 가 댕글링 포인터를 호출해
# 프로세스가 즉사한다. 모듈 전역에 붙잡아 둔다.
_WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

_drop_callback = None

# hwnd -> (원래 프로시저, 우리 프로시저). Tk 는 위젯마다 창을 만들기 때문에 여러 개다.
_drop_procs = {}


def _read_drop_paths(hdrop):
    """HDROP 에서 파일 경로 목록을 뽑는다."""
    # iFile 에 0xFFFFFFFF 를 주면 전체 개수를 돌려준다.
    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    paths = []
    for index in range(count):
        # lpszFile=NULL, cch=0 이면 널 문자를 뺀 필요 길이를 돌려준다.
        need = shell32.DragQueryFileW(hdrop, index, None, 0)
        if not need:
            continue
        buf = ctypes.create_unicode_buffer(need + 1)
        if shell32.DragQueryFileW(hdrop, index, buf, need + 1):
            if buf.value:
                paths.append(buf.value)
    return paths


def _handle_wm_dropfiles(hdrop):
    try:
        paths = _read_drop_paths(hdrop)
    except BaseException:
        log.exception("드롭 경로 추출 실패")
        return
    images = [p for p in paths if p.lower().endswith(IMAGE_EXTS)]
    if not images:
        log.info("드롭됐지만 이미지가 아님: %r", paths)
        return
    callback = _drop_callback
    if callback is None:
        return
    try:
        callback(images)
    except BaseException:
        # 콜백은 Tk 메인 스레드에서 실행되므로 Tk 를 만져도 되지만, 예외를 밖으로
        # 흘리면 안 된다 (여기는 C 콜백 프레임 안이다).
        log.exception("드롭 콜백 실패")


def enable_file_drop(widget, callback):
    """``widget`` 에 파일 드롭을 받게 한다.

    **래퍼와 모든 자손 HWND 에 건다.** Tk 는 위젯마다 창을 만들고(Canvas 가 그렇다), 셸은
    커서 아래의 **가장 깊은** 창으로 드롭을 보내기 때문에 한 곳에만 걸면 드롭이 닿지 않는다.

    **반복 호출해도 안전하고, 그때마다 아직 안 건 창을 추가로 건다.** Tk 는 위젯 창을
    지연 생성하기 때문에(= Canvas 의 HWND 는 완전한 ``update()`` 전에는 없다) 생성 직후
    한 번만 걸면 **실제 드롭 대상이 빠진다**. 그래서 상태가 바뀔 때마다 다시 부른다.

    ``WS_EX_TRANSPARENT`` 가 켜져 있으면 창이 히트 테스트에서 빠지므로 드롭이 닿지 않는다.
    즉 플레이(클릭 통과) 모드에서는 드롭이 동작하지 않는 것이 정상이며, 편집 모드에서만 쓴다.
    """
    global _drop_callback

    _drop_callback = callback
    added = 0
    for hwnd in widget_hwnds(widget):
        if hwnd in _drop_procs:
            continue
        old = u32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)
        if not old:
            log.warning("파일 드롭 설치 건너뜀: WNDPROC 조회 실패 (hwnd=0x%x)", hwnd)
            continue

        # previous 를 인자로 묶는다. 클로저로 참조하면 루프가 돌면서 전부 마지막 값이 된다.
        def _make_proc(previous):
            @_WNDPROC
            def _proc(h, msg, wparam, lparam):
                # 여기는 C 콜백 프레임 안이다. 예외가 새어 나가면 프로세스가 죽고,
                # --noconsole 빌드에서는 표준 스트림이 None 이라 트레이스백 출력조차
                # 에러 경로가 된다.
                try:
                    if msg == WM_DROPFILES:
                        _handle_wm_dropfiles(wparam)
                        shell32.DragFinish(wparam)
                        return 0
                except BaseException:
                    pass
                return u32.CallWindowProcW(previous, h, msg, wparam, lparam)
            return _proc

        proc = _make_proc(old)
        u32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p).value)
        shell32.DragAcceptFiles(hwnd, True)
        _drop_procs[hwnd] = (old, proc)
        added += 1

    if added:
        log.info("파일 드롭 설치: %d개 창 %s",
                 len(_drop_procs), ["0x%x" % h for h in _drop_procs])
    return bool(_drop_procs)


def disable_file_drop():
    """서브클래싱을 해제하고 원래 윈도우 프로시저를 복원한다."""
    global _drop_callback

    if not _drop_procs:
        return
    for hwnd, (old, _proc) in list(_drop_procs.items()):
        try:
            shell32.DragAcceptFiles(hwnd, False)
        except BaseException:
            pass
        try:
            u32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, old)
        except BaseException:
            log.exception("WNDPROC 복원 실패 (hwnd=0x%x)", hwnd)
    _drop_procs.clear()
    _drop_callback = None
    log.info("파일 드롭 해제")
