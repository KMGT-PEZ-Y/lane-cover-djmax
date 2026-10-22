"""트레이 아이콘과 전역 단축키를 담당하는 워커 스레드.

tkinter 는 메인 스레드의 메시지 펌프를 점유하고 스레드 안전하지도 않다. 그래서 별도
스레드가 **자기 윈도우와 자기 펌프**를 갖고, 결과는 ``queue.Queue`` 에 넣기만 한다.
메인 스레드는 그 큐를 폴링해 Tk 를 만진다.

숨김 창은 ``HWND_MESSAGE`` 를 쓰지 않는다. message-only 창은 **브로드캐스트를 받지 못해서**
``TaskbarCreated`` 를 놓치고, 탐색기가 재시작되면 트레이 아이콘이 영영 복구되지 않는다.
대신 화면에 보이지 않는 일반 톱레벨 창을 만든다. 이 창에는 ``WS_EX_NOACTIVATE`` 를 넣지
않는다 - 트레이 메뉴를 띄울 때 ``SetForegroundWindow`` 가 실패하기 때문이다.
"""

import ctypes
import threading
from ctypes import wintypes

from . import native
from .log import get_logger

log = get_logger("tray")

u32 = native.u32
k32 = native.k32
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# --------------------------------------------------------------------------- 상수

WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B

CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
WS_POPUP = 0x80000000

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIIF_INFO = 0x01
NIIF_WARNING = 0x02

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
MF_UNCHECKED = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

SM_CXSMICON = 49
SM_CYSMICON = 50

ID_TOGGLE_MODE = 1001
ID_TOGGLE_VISIBLE = 1002
ID_SETTINGS = 1003
ID_QUIT = 1004

# --------------------------------------------------------------------------- 단축키 파싱

VK_NAMES = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}

MODIFIER_NAMES = {
    "ctrl": native.MOD_CONTROL, "control": native.MOD_CONTROL,
    "alt": native.MOD_ALT,
    "shift": native.MOD_SHIFT,
    "win": native.MOD_WIN, "super": native.MOD_WIN, "meta": native.MOD_WIN,
}


def parse_hotkey(spec):
    """``"Ctrl+Alt+E"`` -> ``(mods, vk)``. 형식이 틀리면 ``ValueError``."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("빈 단축키")

    mods = 0
    vk = None
    for part in (p.strip() for p in spec.split("+")):
        if not part:
            continue
        low = part.lower()
        if low in MODIFIER_NAMES:
            mods |= MODIFIER_NAMES[low]
            continue
        if vk is not None:
            raise ValueError("키가 둘 이상 지정됨: %s" % spec)
        if low in VK_NAMES:
            vk = VK_NAMES[low]
        elif len(part) == 1 and (part.isalnum()):
            vk = ord(part.upper())
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
            vk = 0x70 + int(low[1:]) - 1
        else:
            raise ValueError("알 수 없는 키: %s" % part)

    if vk is None:
        raise ValueError("주 키가 없음: %s" % spec)
    if mods == 0:
        raise ValueError("수정자 키가 필요함: %s" % spec)
    return mods, vk


# --------------------------------------------------------------------------- 구조체


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


u32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
u32.CreateWindowExW.restype = wintypes.HWND
u32.DefWindowProcW.restype = ctypes.c_ssize_t
u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
u32.RegisterClassW.restype = wintypes.WORD
u32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
u32.RegisterWindowMessageW.restype = wintypes.UINT
u32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
u32.RegisterHotKey.restype = wintypes.BOOL
u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
u32.UnregisterHotKey.restype = wintypes.BOOL
u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
u32.GetMessageW.restype = ctypes.c_int
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.PostMessageW.restype = wintypes.BOOL
u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
u32.LoadImageW.restype = wintypes.HANDLE
# 두 번째 인자는 MAKEINTRESOURCE 라 정수가 포인터로 들어간다. LPCWSTR 로 선언하면
# ctypes 가 32512 를 문자열로 바꾸려다 실패한다.
u32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
u32.LoadIconW.restype = wintypes.HANDLE
u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
u32.TranslateMessage.restype = wintypes.BOOL
u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
u32.DispatchMessageW.restype = ctypes.c_ssize_t
u32.PostQuitMessage.argtypes = [ctypes.c_int]
u32.PostQuitMessage.restype = None
k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
k32.GetModuleHandleW.restype = wintypes.HMODULE
u32.CreatePopupMenu.restype = wintypes.HMENU
u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
u32.AppendMenuW.restype = wintypes.BOOL
u32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
u32.TrackPopupMenu.restype = wintypes.BOOL
u32.DestroyMenu.argtypes = [wintypes.HMENU]
u32.DestroyMenu.restype = wintypes.BOOL
u32.SetForegroundWindow.argtypes = [wintypes.HWND]
u32.SetForegroundWindow.restype = wintypes.BOOL
u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
u32.GetCursorPos.restype = wintypes.BOOL
u32.GetSystemMetrics.argtypes = [ctypes.c_int]
u32.GetSystemMetrics.restype = ctypes.c_int
u32.DestroyWindow.argtypes = [wintypes.HWND]
u32.DestroyWindow.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


# 콜백은 살아 있어야 한다. GC 되면 Windows 가 댕글링 포인터를 호출해 프로세스가 죽는다.
_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
_ALIVE_PROCS = []

_class_counter = [0]


class NativeShell:
    """트레이 아이콘 + 전역 단축키 + 메시지 펌프를 소유하는 워커 스레드."""

    def __init__(self, queue, hotkey_specs=None, icon_path=None,
                 tooltip="Custom Lane Cover"):
        self.queue = queue
        self.hotkey_specs = dict(hotkey_specs or {})
        self.icon_path = icon_path
        self.tooltip = tooltip

        self.failed_hotkeys = []  # (action, spec, reason)
        self.active_hotkeys = []
        self.tray_ok = False

        self.mode = "edit"
        self.visible = True

        self._hwnd = 0
        self._thread = None
        self._ready = threading.Event()
        self._proc = None
        self._class_name = None
        self._hicon = None
        self._taskbar_created_msg = 0
        self._hotkey_ids = {}  # id -> action

    # -- 상태 (메인 스레드가 갱신, 워커가 읽음. 문자열/불리언 대입은 원자적이다) --

    def set_state(self, mode=None, visible=None):
        if mode is not None:
            self.mode = mode
        if visible is not None:
            self.visible = visible

    # -- 수명주기 -----------------------------------------------------------

    def start(self, timeout=5.0):
        """스레드를 띄우고 창/단축키/트레이 초기화가 끝날 때까지 기다린다."""
        self._thread = threading.Thread(
            target=self._run, name="lanecover-shell", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            log.error("네이티브 셸 초기화 시간 초과")
            return False
        return True

    def stop(self, timeout=2.0):
        if self._hwnd:
            # 소유 스레드가 WM_CLOSE 를 처리하며 정리한다.
            u32.PostMessageW(self._hwnd, native.WM_CLOSE, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.warning("네이티브 셸 스레드가 종료되지 않았다")
        self._thread = None

    # -- 워커 스레드 ---------------------------------------------------------

    def _run(self):
        try:
            self._create_window()
            self._register_hotkeys()
            self._add_tray_icon()
            self._ready.set()
            self._pump()
        except BaseException:
            log.exception("네이티브 셸 스레드 실패")
            self._ready.set()
        finally:
            self._cleanup()

    def _create_window(self):
        _class_counter[0] += 1
        self._class_name = "CustomLaneCoverShell_%d" % _class_counter[0]

        @_WNDPROC
        def _proc(hwnd, msg, wparam, lparam):
            # C 콜백 프레임이다. 예외가 새어 나가면 프로세스가 죽고,
            # --noconsole 빌드에서는 표준 스트림이 None 이라 트레이스백조차 못 찍는다.
            try:
                return self._on_message(hwnd, msg, wparam, lparam)
            except BaseException:
                log.exception("셸 윈도우 프로시저 예외 (msg=0x%04x)", msg)
                return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = _proc
        _ALIVE_PROCS.append(_proc)

        hinstance = k32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = ctypes.cast(_proc, ctypes.c_void_p).value
        wc.hInstance = hinstance
        wc.lpszClassName = self._class_name

        if not u32.RegisterClassW(ctypes.byref(wc)):
            error = ctypes.get_last_error()
            if error != 1410:  # ERROR_CLASS_ALREADY_EXISTS 는 무시해도 된다
                raise OSError("RegisterClassW 실패 (err=%d)" % error)

        # 화면에 뜨지 않는 일반 톱레벨 창. HWND_MESSAGE 를 쓰면 TaskbarCreated 를 못 받는다.
        # WS_EX_NOACTIVATE 는 일부러 넣지 않는다 (트레이 메뉴의 SetForegroundWindow 실패).
        hwnd = u32.CreateWindowExW(
            native.WS_EX_TOOLWINDOW, self._class_name, self._class_name, WS_POPUP,
            0, 0, 0, 0, None, None, hinstance, None,
        )
        if not hwnd:
            raise OSError("CreateWindowExW 실패 (err=%d)" % ctypes.get_last_error())
        self._hwnd = hwnd

        # 탐색기 재시작을 감지하기 위한 브로드캐스트 메시지 ID.
        self._taskbar_created_msg = u32.RegisterWindowMessageW("TaskbarCreated")
        log.info("셸 창 생성: hwnd=0x%x class=%s", hwnd, self._class_name)

    def _register_hotkeys(self):
        for index, (action, spec) in enumerate(sorted(self.hotkey_specs.items())):
            try:
                mods, vk = parse_hotkey(spec)
            except ValueError as exc:
                self.failed_hotkeys.append((action, spec, str(exc)))
                log.warning("단축키 형식 오류 %s=%r: %s", action, spec, exc)
                continue

            hotkey_id = 0xA000 + index
            if u32.RegisterHotKey(self._hwnd, hotkey_id, mods | native.MOD_NOREPEAT, vk):
                self._hotkey_ids[hotkey_id] = action
                self.active_hotkeys.append((action, spec))
                continue

            error = ctypes.get_last_error()
            if error == native.ERROR_HOTKEY_ALREADY_REGISTERED:
                reason = "다른 프로그램이 이미 사용 중"
            else:
                reason = "등록 실패 (err=%d)" % error
            self.failed_hotkeys.append((action, spec, reason))
            log.warning("단축키 등록 실패 %s=%r: %s", action, spec, reason)

    def _load_icon(self):
        size = u32.GetSystemMetrics(SM_CXSMICON) or 16
        if self.icon_path:
            handle = u32.LoadImageW(
                None, self.icon_path, IMAGE_ICON, size, size, LR_LOADFROMFILE
            )
            if handle:
                return handle
            log.warning("아이콘 로드 실패: %s (err=%d)", self.icon_path, ctypes.get_last_error())
        return u32.LoadIconW(None, IDI_APPLICATION)

    def _notify_data(self, flags):
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAYICON
        if flags & NIF_ICON:
            data.hIcon = self._hicon
        if flags & NIF_TIP:
            data.szTip = self.tooltip
        return data

    def _add_tray_icon(self):
        if self._hicon is None:
            self._hicon = self._load_icon()
        data = self._notify_data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        self.tray_ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
        if self.tray_ok:
            log.info("트레이 아이콘 등록")
        else:
            log.warning("트레이 아이콘 등록 실패 (err=%d)", ctypes.get_last_error())
        return self.tray_ok

    def notify(self, title, text, warning=True):
        """풍선 알림. 트레이 아이콘이 없으면 무시된다."""
        if not self.tray_ok:
            return
        data = self._notify_data(NIF_INFO | NIF_ICON | NIF_TIP)
        data.szInfoTitle = title[:63]
        data.szInfo = text[:255]
        data.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _pump(self):
        msg = wintypes.MSG()
        while True:
            result = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:  # 0 = WM_QUIT, -1 = 오류
                break
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    def _cleanup(self):
        for hotkey_id in list(self._hotkey_ids):
            u32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._hotkey_ids.clear()
        if self.tray_ok:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._notify_data(0)))
            self.tray_ok = False
        if self._hwnd:
            u32.DestroyWindow(self._hwnd)
            self._hwnd = 0
        log.info("셸 정리 완료")

    # -- 메시지 -------------------------------------------------------------

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == native.WM_HOTKEY:
            action = self._hotkey_ids.get(wparam)
            if action:
                self.queue.put((action,))
            return 0

        if msg == self._taskbar_created_msg and self._taskbar_created_msg:
            # 탐색기가 재시작되면 아이콘이 사라진다. 다시 등록해야 살아난다.
            log.info("TaskbarCreated 수신 - 트레이 아이콘 재등록")
            self.tray_ok = False
            self._add_tray_icon()
            return 0

        if msg == WM_TRAYICON:
            if lparam == WM_RBUTTONUP or lparam == WM_CONTEXTMENU:
                self._show_menu()
            elif lparam == WM_LBUTTONDBLCLK:
                self.queue.put(("toggle_visible",))
            return 0

        if msg == native.WM_CLOSE:
            u32.DestroyWindow(hwnd)
            return 0

        if msg == native.WM_DESTROY:
            u32.PostQuitMessage(0)
            return 0

        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self):
        menu = u32.CreatePopupMenu()
        if not menu:
            return
        try:
            mode_label = "플레이 모드로 전환" if self.mode == "edit" else "편집 모드로 전환"
            visible_label = "숨기기" if self.visible else "표시하기"

            u32.AppendMenuW(menu, MF_STRING, ID_TOGGLE_MODE, mode_label)
            u32.AppendMenuW(menu, MF_STRING, ID_TOGGLE_VISIBLE, visible_label)
            u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            u32.AppendMenuW(menu, MF_STRING, ID_SETTINGS, "설정...")
            u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            u32.AppendMenuW(menu, MF_STRING, ID_QUIT, "종료")

            point = wintypes.POINT()
            u32.GetCursorPos(ctypes.byref(point))

            # 이 순서가 중요하다. SetForegroundWindow 없이 띄우면 메뉴가 바로 닫히고,
            # TrackPopupMenu 뒤의 WM_NULL 이 없으면 두 번째로 열 때 나타나자마자 사라진다.
            u32.SetForegroundWindow(self._hwnd)
            command = u32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                point.x, point.y, 0, self._hwnd, None,
            )
            u32.PostMessageW(self._hwnd, native.WM_NULL, 0, 0)

            if command == ID_TOGGLE_MODE:
                self.queue.put(("toggle_mode",))
            elif command == ID_TOGGLE_VISIBLE:
                self.queue.put(("toggle_visible",))
            elif command == ID_SETTINGS:
                self.queue.put(("open_settings",))
            elif command == ID_QUIT:
                self.queue.put(("quit",))
        finally:
            u32.DestroyMenu(menu)
