"""레인 커버 창 본체.

프레임 없는 오버레이 창을 만들고 이동/리사이즈/모드 전환/패턴 적용을 담당한다.

**초기화 순서가 중요하다.** Tk 는 톱레벨마다 래퍼 윈도우를 만들고 Tk 윈도우를 그 자식으로
재부모화하는데, 창이 매핑된 뒤 ``overrideredirect`` 를 바꾸면 래퍼를 파괴하고 새로 만든다.
그러면 캐시해 둔 HWND 와 설치한 서브클래스가 전부 무효가 된다. 그래서:

    Tk() -> overrideredirect(1회, 매핑 전) -> -topmost -> -alpha/-transparentcolor
        -> Canvas -> update_idletasks() -> HWND 캐시 -> 클릭통과/드롭 설치 -> 좌표 복원

순서를 지킨다. ``-alpha`` 계열을 **먼저** 설정하는 것도 의도적이다. Tk 는 레이어링을 처음
켤 때 **캐시된** ex-style 로 ``GWL_EXSTYLE`` 을 통째로 쓰기 때문에, 우리가 OR 하는
``WS_EX_TRANSPARENT`` 를 나중에 덮어쓴다.
"""

import tkinter as tk

from . import config, native, patterns
from .log import get_logger

log = get_logger("app")

# 가장자리/모서리 판정 두께 (px)
HANDLE = 6
# 드래그 중 창이 화면 밖으로 완전히 사라지지 않도록 남겨 둘 최소 폭 (px)
KEEP_VISIBLE = 80
# 화면 가장자리/중앙 자석 스냅 임계값 (px)
SNAP_PX = 12
# 리사이즈 중 패턴 재렌더 스로틀 (ms). 창 이동은 즉시 반영하고 이미지만 제한한다.
RENDER_THROTTLE_MS = 40

CHROME_COLOR = "#00E5FF"
CHROME_DIM = "#0A2A33"
BADGE_BG = "#0B1A20"
BADGE_FG = "#9FE8F5"

CURSORS = {
    "nw": "size_nw_se", "se": "size_nw_se",
    "ne": "size_ne_sw", "sw": "size_ne_sw",
    "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
    "w": "sb_h_double_arrow", "e": "sb_h_double_arrow",
    "move": "fleur",
}

HANDLE_OFFSETS = (
    ("nw", 0.0, 0.0), ("n", 0.5, 0.0), ("ne", 1.0, 0.0),
    ("w", 0.0, 0.5), ("e", 1.0, 0.5),
    ("sw", 0.0, 1.0), ("s", 0.5, 1.0), ("se", 1.0, 1.0),
)


class LaneCoverApp:
    def __init__(self, events=None, on_quit=None):
        self.cfg = config.Config.load()
        self.renderer = patterns.PatternRenderer()
        self.events = events
        self.on_quit = on_quit

        self._hwnd = 0
        self._drag = None
        self._render_after = None
        self._render_hq = False
        self._save_after = None
        self._topmost_after = None
        self._pump_after = None
        self._settings = None
        self._menu = None
        self._photo = None  # 현재 캔버스에 걸린 PhotoImage 참조

        # main.py 가 채운다. 트레이/단축키 셸과, 단축키 재등록용 콜백.
        self.shell = None
        self.on_hotkeys_changed = None
        # 전역 단축키와 트레이가 **둘 다** 실패하면 플레이 모드에서 나올 방법이 없어진다.
        # 그 경우 main.py 가 False 로 내려 플레이 모드 진입을 막는다.
        self.allow_play_mode = True

        self._build_window()

        geom = self.cfg.geometry
        self._geom = [geom.x, geom.y, geom.w, geom.h]
        self.apply_config(self.cfg, initial=True)

    # ------------------------------------------------------------------ 창 생성

    def _build_window(self):
        self.root = tk.Tk()
        self.root.title("Custom Lane Cover")

        # 매핑 전에 1회. 이후 절대 건드리지 않는다 (래퍼 재생성 방지).
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # 레이어링 속성을 먼저 건다. Tk 가 여기서 WS_EX_LAYERED 를 소유한다.
        self._apply_visual_attributes()

        self.root.configure(bg=self.cfg.pattern.color)
        self.canvas = tk.Canvas(
            self.root, highlightthickness=0, bd=0, bg=self.cfg.pattern.color
        )
        self.canvas.pack(fill="both", expand=True)

        self.root.update_idletasks()

        self._hwnd = native.top_hwnd(self.root)
        child = native.child_hwnd(self.root)
        log.info(
            "창 생성: 래퍼 hwnd=0x%x 자식 hwnd=0x%x (서로 달라야 정상)",
            self._hwnd, child,
        )
        if self._hwnd == child:
            log.error("래퍼와 자식 HWND 가 같다 - Tk 내부 구조 가정이 깨졌다")

        # Canvas 의 HWND 는 update_idletasks() 로는 아직 없다. 완전한 update() 를 한 번
        # 돌려야 위젯 창이 실제로 생기고, 그래야 드롭을 **실제 대상**에 걸 수 있다.
        self.root.update()
        native.enable_file_drop(self.root, self._on_file_drop)
        self._bind_events()

    def _bind_events(self):
        c = self.canvas
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Motion>", self._on_hover)
        c.bind("<Button-3>", self._on_context_menu)

        for seq, handler in (
            ("<Escape>", lambda e: self.set_mode("play")),
            ("<Control-s>", lambda e: self.save()),
            ("<Control-v>", self._on_paste),
            ("<Control-e>", lambda e: self.toggle_mode()),
            ("<Control-q>", lambda e: self.quit()),
            ("<Left>", self._on_nudge), ("<Right>", self._on_nudge),
            ("<Up>", self._on_nudge), ("<Down>", self._on_nudge),
        ):
            self.root.bind(seq, handler)

        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    # ------------------------------------------------------------------ 네이티브 동기화

    def _apply_visual_attributes(self):
        """투명도 관련 Tk 속성만 설정한다 (ex-style 비트는 건드리지 않는다)."""
        try:
            self.root.attributes("-alpha", self.cfg.opacity)
        except tk.TclError:
            log.exception("-alpha 설정 실패")

        key = self.cfg.color_key
        try:
            # 빈 문자열이면 컬러키 해제.
            self.root.attributes("-transparentcolor", key.color if key.enabled else "")
        except tk.TclError:
            log.warning("-transparentcolor %r 설정 실패", key.color)

    def _refresh_hwnd(self):
        """래퍼 HWND 를 다시 읽는다. 바뀌었으면 경고를 남긴다 (진단용)."""
        current = native.top_hwnd(self.root)
        if current and current != self._hwnd:
            log.warning(
                "래퍼 HWND 가 바뀌었다: 0x%x -> 0x%x (Tk 가 래퍼를 재생성함)",
                self._hwnd, current,
            )
            self._hwnd = current
        return self._hwnd

    def _sync_native(self, why=""):
        """ex-style 과 z-order 를 현재 모드에 맞춰 다시 적용한다.

        ``-alpha`` 를 1.0 으로 되돌리거나 컬러키를 해제할 때 Tk 가 ex-style 을 다시 쓰면서
        우리가 OR 한 비트를 지울 수 있어, 시각 속성을 건드린 뒤에는 항상 이걸 부른다.
        """
        hwnd = self._refresh_hwnd()
        if not hwnd:
            return
        key = self.cfg.color_key
        native.set_click_through(
            hwnd,
            self.cfg.mode == "play",
            opacity=self.cfg.opacity,
            color_key=key.color if key.enabled else None,
        )
        native.assert_topmost(hwnd)
        # Tk 는 -alpha 를 처음 걸 때 ex-style 을 통째로 다시 쓰면서 TOOLWINDOW 를 잃는다.
        native.assert_tool_window(hwnd)
        # Tk 가 위젯 창을 지연 생성하므로, 새로 생긴 창이 있으면 드롭을 추가로 건다.
        native.enable_file_drop(self.root, self._on_file_drop)
        if why:
            log.debug("네이티브 상태 재적용 (%s)", why)

    # ------------------------------------------------------------------ 패턴 렌더

    def _render_pattern(self, high_quality=True):
        """현재 크기에 맞춰 패턴을 다시 그린다."""
        self.canvas.delete("pattern")
        self._photo = None

        width, height = self._geom[2], self._geom[3]
        if width <= 0 or height <= 0:
            return

        photo = self.renderer.render(
            self.cfg.pattern.image, width, height,
            self.cfg.pattern.fit, self.cfg.pattern.color, high_quality,
        )
        if photo is not None:
            self._photo = photo  # GC 방지
            self.canvas.create_image(0, 0, image=photo, anchor="nw", tags="pattern")
            self.canvas.tag_lower("pattern")
        else:
            self.canvas.configure(bg=self.cfg.pattern.color)

    def _schedule_render(self, high_quality):
        """리사이즈 중 재렌더를 스로틀한다. 마지막 요청이 고품질이면 승격한다."""
        self._render_hq = self._render_hq or high_quality
        if self._render_after is None:
            self._render_after = self.root.after(RENDER_THROTTLE_MS, self._flush_render)

    def _flush_render(self):
        self._render_after = None
        high_quality = self._render_hq
        self._render_hq = False
        self._render_pattern(high_quality=high_quality)
        self._draw_chrome()

    # ------------------------------------------------------------------ 편집 크롬

    def _font_size(self):
        """DPI 배율에 맞춘 폰트 크기. per-monitor v2 에서 winfo_fpixels('1i') 가 실제 PPI."""
        try:
            scale = self.root.winfo_fpixels("1i") / 96.0
        except tk.TclError:
            scale = 1.0
        return max(8, int(round(10 * scale)))

    def _draw_chrome(self):
        """편집 모드 전용 테두리/핸들/배지를 그린다. 플레이 모드에서는 아무것도 없다."""
        self.canvas.delete("chrome")
        if self.cfg.mode != "edit":
            return

        width, height = self._geom[2], self._geom[3]
        if width < 4 or height < 4:
            return

        self.canvas.create_rectangle(
            1, 1, width - 2, height - 2,
            outline=CHROME_COLOR, dash=(6, 4), width=2, tags="chrome",
        )

        radius = max(3, HANDLE // 2 + 1)
        for _name, fx, fy in HANDLE_OFFSETS:
            cx, cy = fx * (width - 1), fy * (height - 1)
            self.canvas.create_rectangle(
                cx - radius, cy - radius, cx + radius, cy + radius,
                fill=CHROME_COLOR, outline=CHROME_DIM, tags="chrome",
            )

        if self.cfg.pattern.image:
            badge = "편집 모드 · %d×%d · %s 로 플레이" % (
                width, height, self.cfg.hotkeys.get("toggle_mode", "Ctrl+Alt+E"),
            )
        else:
            badge = "이 창에 PNG/JPG 파일을 끌어다 놓으세요 · %d×%d" % (width, height)

        font = ("Segoe UI", self._font_size())
        text_id = self.canvas.create_text(
            10, 9, text=badge, anchor="nw", fill=BADGE_FG, font=font, tags="chrome",
        )
        x0, y0, x1, y1 = self.canvas.bbox(text_id)
        badge_bg = self.canvas.create_rectangle(
            x0 - 6, y0 - 3, x1 + 6, y1 + 3,
            fill=BADGE_BG, outline=CHROME_DIM, tags="chrome",
        )
        self.canvas.tag_raise(text_id, badge_bg)

        if not self.cfg.pattern.image and height > 60:
            self.canvas.create_text(
                width // 2, height // 2 + 18,
                text="드래그앤드롭 또는 Ctrl+V 로 이미지 적용",
                anchor="center", fill=BADGE_FG, font=font, tags="chrome",
            )

    # ------------------------------------------------------------------ 좌표

    def _apply_geom(self):
        """현재 ``self._geom`` 을 창에 반영하고 Tk 가 처리할 시간을 준다."""
        x, y, w, h = self._geom
        native.move_window(self._hwnd, x, y, w, h)
        self.root.update_idletasks()

    def _clamp(self, x, y, w, h):
        """창이 화면 밖으로 완전히 나가지 않게 한다 (멀티모니터 포함)."""
        vs = native.virtual_screen_rect()
        if not vs:
            return x, y, w, h
        vx, vy, vw, vh = vs
        lo_x, hi_x = vx - w + KEEP_VISIBLE, vx + vw - KEEP_VISIBLE
        lo_y, hi_y = vy - h + KEEP_VISIBLE, vy + vh - KEEP_VISIBLE
        if lo_x <= hi_x:
            x = min(max(x, lo_x), hi_x)
        else:
            x = lo_x
        if lo_y <= hi_y:
            y = min(max(y, lo_y), hi_y)
        else:
            y = lo_y
        return x, y, w, h

    def _snap_targets(self, axis_len, origin, span):
        """해당 축에서 자석이 붙을 좌표 후보 (앞/뒤 가장자리, 가운데)."""
        return [origin, origin + span - axis_len, origin + (span - axis_len) // 2]

    def _nearest(self, value, targets, threshold):
        best, best_delta = value, threshold + 1
        for target in targets:
            delta = abs(value - target)
            if delta < best_delta:
                best, best_delta = target, delta
        return best

    def _snap(self, x, y, w, h, zone, grid, use_grid):
        """그리드(Shift) 및 화면 가장자리/중앙 자석 스냅."""
        vs = native.virtual_screen_rect()
        if zone == "move":
            if use_grid and grid > 1:
                x = int(round(x / grid)) * grid
                y = int(round(y / grid)) * grid
            if vs:
                vx, vy, vw, vh = vs
                x = self._nearest(x, self._snap_targets(w, vx, vw), SNAP_PX)
                y = self._nearest(y, self._snap_targets(h, vy, vh), SNAP_PX)
            return x, y, w, h

        # 리사이즈: 움직이는 가장자리만 스냅한다.
        if vs:
            vx, vy, vw, vh = vs
            if "w" in zone:
                nx = self._nearest(x, [vx, vx + vw // 2], SNAP_PX)
                w -= nx - x
                x = nx
            elif "e" in zone:
                right = self._nearest(x + w, [vx + vw, vx + vw // 2], SNAP_PX)
                w = right - x
            if "n" in zone:
                ny = self._nearest(y, [vy, vy + vh // 2], SNAP_PX)
                h -= ny - y
                y = ny
            elif "s" in zone:
                bottom = self._nearest(y + h, [vy + vh, vy + vh // 2], SNAP_PX)
                h = bottom - y
        if use_grid and grid > 1:
            w = max(config.MIN_W, int(round(w / grid)) * grid)
            h = max(config.MIN_H, int(round(h / grid)) * grid)
        return x, y, w, h

    # ------------------------------------------------------------------ 드래그

    def _hit_zone(self, x, y):
        w, h = self._geom[2], self._geom[3]
        left, right = x <= HANDLE, x >= w - HANDLE
        top, bottom = y <= HANDLE, y >= h - HANDLE
        if top and left:
            return "nw"
        if top and right:
            return "ne"
        if bottom and left:
            return "sw"
        if bottom and right:
            return "se"
        if left:
            return "w"
        if right:
            return "e"
        if top:
            return "n"
        if bottom:
            return "s"
        return "move"

    def _on_hover(self, event):
        if self.cfg.mode != "edit" or self._drag is not None:
            return
        self.canvas.configure(cursor=CURSORS[self._hit_zone(event.x, event.y)])

    def _on_press(self, event):
        if self.cfg.mode != "edit":
            return
        self._drag = {
            "zone": self._hit_zone(event.x, event.y),
            "px": self.root.winfo_pointerx(),
            "py": self.root.winfo_pointery(),
            "geom": list(self._geom),
            "moved": False,
        }

    def _on_drag(self, event):
        drag = self._drag
        if drag is None or self.cfg.mode != "edit":
            return

        dx = self.root.winfo_pointerx() - drag["px"]
        dy = self.root.winfo_pointery() - drag["py"]
        if dx == 0 and dy == 0:
            return
        drag["moved"] = True

        x, y, w, h = drag["geom"]
        zone = drag["zone"]
        left, top, right, bottom = x, y, x + w, y + h

        if zone == "move":
            left, right = left + dx, right + dx
            top, bottom = top + dy, bottom + dy
        else:
            if "w" in zone:
                left += dx
            if "e" in zone:
                right += dx
            if "n" in zone:
                top += dy
            if "s" in zone:
                bottom += dy
            # 최소 크기 보장: 반대편 가장자리를 고정한다.
            if right - left < config.MIN_W:
                if "w" in zone:
                    left = right - config.MIN_W
                else:
                    right = left + config.MIN_W
            if bottom - top < config.MIN_H:
                if "n" in zone:
                    top = bottom - config.MIN_H
                else:
                    bottom = top + config.MIN_H

        use_grid = bool(event.state & 0x0001)  # ShiftMask
        x, y, w, h = self._snap(
            left, top, right - left, bottom - top, zone,
            self.cfg.snap.grid, use_grid,
        )
        x, y, w, h = self._clamp(x, y, w, h)

        if [x, y, w, h] == self._geom:
            return
        resized = (w, h) != (self._geom[2], self._geom[3])
        self._geom = [x, y, w, h]

        # 창 이동/크기는 즉시 반영한다 (SetWindowPos 는 layered 창에서 저렴하다).
        self._apply_geom()
        if resized:
            self._schedule_render(high_quality=False)
        else:
            self._draw_chrome()

    def _on_release(self, event):
        drag, self._drag = self._drag, None
        if drag is None:
            return
        if drag["moved"]:
            rect = native.get_window_rect(self._hwnd)
            if rect:
                self._geom = list(rect)
            self._render_pattern(high_quality=True)  # 최종본은 고품질로 한 번 더
            self._draw_chrome()
            self._schedule_save()
            if self.cfg.mode == "edit":
                self.canvas.configure(cursor=CURSORS[self._hit_zone(event.x, event.y)])

    def _on_nudge(self, event):
        if self.cfg.mode != "edit":
            return
        step = 10 if (event.state & 0x0001) else 1
        dx = dy = 0
        if event.keysym == "Left":
            dx = -step
        elif event.keysym == "Right":
            dx = step
        elif event.keysym == "Up":
            dy = -step
        elif event.keysym == "Down":
            dy = step

        x, y, w, h = self._geom
        if event.state & 0x0004:  # ControlMask -> 크기 조절
            w = max(config.MIN_W, w + dx)
            h = max(config.MIN_H, h + dy)
        else:
            x += dx
            y += dy
        x, y, w, h = self._clamp(x, y, w, h)
        self._geom = [x, y, w, h]
        self._apply_geom()
        self._render_pattern(high_quality=True)
        self._draw_chrome()
        self._schedule_save()

    # ------------------------------------------------------------------ 명령

    def set_mode(self, mode, save=True):
        if mode not in config.VALID_MODES or mode == self.cfg.mode:
            return
        if self._drag is not None:
            # 드래그 중에 WS_EX_TRANSPARENT 를 켜면 자식이 마우스 메시지를 못 받아
            # <ButtonRelease> 가 오지 않고 드래그가 영구히 걸린다.
            log.info("드래그 중이라 모드 전환을 보류한다")
            return

        if mode == "play" and not self.allow_play_mode:
            # 클릭 통과 상태에서는 창을 조작할 수 없으므로, 나올 수단이 없으면 들어가면 안 된다.
            self.show_warning(
                "플레이 모드로 전환할 수 없습니다",
                "전역 단축키와 트레이 아이콘이 모두 동작하지 않아, 플레이 모드에서 "
                "편집 모드로 돌아올 방법이 없습니다.\n\n"
                "설정 창의 '진단' 항목과 로그를 확인하세요.",
            )
            return

        self.cfg.mode = mode
        self._sync_native("모드 전환 -> %s" % mode)
        self._draw_chrome()
        if mode == "play":
            self.canvas.configure(cursor="")
        if self.shell is not None:
            self.shell.set_state(mode=mode)
        log.info("모드: %s", mode)
        if save:
            self.save()

    def toggle_mode(self):
        self.set_mode("play" if self.cfg.mode == "edit" else "edit")

    def set_visible(self, visible, save=True):
        if visible == self.cfg.visible:
            return
        self.cfg.visible = visible
        if visible:
            self.root.deiconify()
            # deiconify 로 래퍼가 재생성됐을 수 있으니 네이티브 상태를 전부 다시 건다.
            self._sync_native("표시")
            self._apply_geom()
            self._render_pattern(high_quality=True)
            self._draw_chrome()
        else:
            self.root.withdraw()
        if self.shell is not None:
            self.shell.set_state(visible=visible)
        log.info("표시: %s", visible)
        if save:
            self.save()

    def toggle_visible(self):
        self.set_visible(not self.cfg.visible)

    def apply_config(self, cfg, initial=False):
        """설정을 창에 반영한다. 설정 창의 '저장'에서도 호출된다."""
        self.cfg = cfg
        self._geom = [cfg.geometry.x, cfg.geometry.y, cfg.geometry.w, cfg.geometry.h]
        self._geom = list(self._clamp(*self._geom))

        self._apply_visual_attributes()
        self.canvas.configure(bg=cfg.pattern.color)
        self._apply_geom()
        self._render_pattern(high_quality=True)
        self._draw_chrome()
        self._sync_native("설정 적용")

        if not initial:
            self._restart_topmost_timer()

    def apply_pattern_path(self, path):
        """패턴 이미지를 바꾼다 (드롭/파일 선택/붙여넣기 공통 경로)."""
        if not path:
            return
        self.cfg.pattern.image = path
        self.renderer.invalidate()
        self._render_pattern(high_quality=True)
        self._draw_chrome()
        self.save()
        log.info("패턴 적용: %s", path)

    def clear_pattern(self):
        self.cfg.pattern.image = None
        self._render_pattern(high_quality=True)
        self._draw_chrome()
        self.save()

    def _on_file_drop(self, paths):
        """드롭 콜백. WM_DROPFILES 는 Tk 메시지 루프가 도는 메인 스레드로 오지만,
        윈도우 프로시저 안에서 Tk 를 직접 만지는 건 재진입 위험이 있어 idle 로 미룬다."""
        path = paths[0]
        self.root.after_idle(lambda: self.apply_pattern_path(path))

    def _on_paste(self, event=None):
        data = patterns.grab_clipboard()
        if data is None:
            log.info("클립보드에 이미지가 없다")
            return
        try:
            if isinstance(data, str):
                self.apply_pattern_path(data)
            else:
                self.apply_pattern_path(patterns.save_image(data, prefix="clipboard"))
        except patterns.PatternError as exc:
            log.warning("붙여넣기 실패: %s", exc)

    def save(self):
        if self._save_after is not None:
            self.root.after_cancel(self._save_after)
            self._save_after = None
        # 실제 창 좌표를 우선 신뢰한다 (Win32 가 미세 조정했을 수 있다).
        rect = native.get_window_rect(self._hwnd)
        if rect:
            self._geom = list(rect)
        self.cfg.geometry = config.Geometry(*self._geom)
        try:
            self.cfg.save()
        except OSError:
            log.exception("설정 저장 실패")

    def _schedule_save(self, delay_ms=800):
        """드래그/리사이즈 직후 잦은 저장을 피하려고 살짝 미뤄 저장한다.

        종료할 때만 저장하면 강제 종료되거나 크래시했을 때 배치를 통째로 잃는다.
        """
        if self._save_after is not None:
            self.root.after_cancel(self._save_after)
        self._save_after = self.root.after(delay_ms, self.save)

    # ------------------------------------------------------------------ 최상단 타이머

    def _restart_topmost_timer(self):
        if self._topmost_after is not None:
            self.root.after_cancel(self._topmost_after)
            self._topmost_after = None
        if self.cfg.topmost_reassert_ms > 0:
            self._topmost_after = self.root.after(
                self.cfg.topmost_reassert_ms, self._topmost_tick
            )

    def _topmost_tick(self):
        self._topmost_after = None
        if self.cfg.visible:
            native.assert_topmost(self._hwnd)
        self._restart_topmost_timer()

    # ------------------------------------------------------------------ 이벤트 펌프

    def _pump(self):
        """트레이/단축키 스레드가 넣은 이벤트를 소비한다.

        tkinter 는 스레드 안전하지 않으므로 워커 스레드는 큐에 넣기만 하고,
        Tk 호출은 전부 메인 스레드인 여기서 한다.
        """
        self._pump_after = None
        if self.events is not None:
            import queue

            while True:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_event(event)
                except Exception:
                    log.exception("이벤트 처리 실패: %r", event)
        self._pump_after = self.root.after(50, self._pump)

    def _handle_event(self, event):
        action = event[0] if isinstance(event, (tuple, list)) else event
        if action == "toggle_mode":
            self.toggle_mode()
        elif action == "toggle_visible":
            self.toggle_visible()
        elif action == "open_settings":
            self.open_settings()
        elif action == "quit":
            self.quit()
        else:
            log.debug("알 수 없는 이벤트: %r", event)

    # ------------------------------------------------------------------ 메뉴/설정

    def _on_context_menu(self, event):
        if self.cfg.mode != "edit":
            return
        self.open_context_menu(event.x_root, event.y_root)

    def open_context_menu(self, x_root=None, y_root=None):
        if self._menu is None:
            self._menu = tk.Menu(self.root, tearoff=0)
        menu = self._menu
        menu.delete(0, "end")
        menu.add_command(label="설정...", command=self.open_settings)
        menu.add_separator()
        menu.add_command(
            label="이미지 선택...", command=self.choose_pattern_file
        )
        menu.add_command(label="클립보드에서 붙여넣기", command=self._on_paste)
        menu.add_command(label="패턴 지우기", command=self.clear_pattern)
        menu.add_separator()
        menu.add_command(label="플레이 모드로", command=lambda: self.set_mode("play"))
        menu.add_command(label="숨기기", command=lambda: self.set_visible(False))
        menu.add_separator()
        menu.add_command(label="종료", command=self.quit)
        try:
            menu.tk_popup(
                x_root if x_root is not None else self.root.winfo_pointerx(),
                y_root if y_root is not None else self.root.winfo_pointery(),
            )
        finally:
            menu.grab_release()

    def choose_pattern_file(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="레인 커버 패턴 이미지 선택",
            filetypes=[
                ("이미지", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                ("모든 파일", "*.*"),
            ],
        )
        if path:
            self.apply_pattern_path(path)

    def open_settings(self):
        from . import settings_ui

        if self._settings is not None and self._settings.alive:
            self._settings.focus()
            return
        self._settings = settings_ui.SettingsWindow(self)

    def show_warning(self, title, message):
        """사용자에게 알려야 하는 문제 (단축키 충돌 등)."""
        from tkinter import messagebox

        log.warning("%s: %s", title, message)
        try:
            messagebox.showwarning(title, message, parent=self.root)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 실행/종료

    def run(self):
        self._restart_topmost_timer()
        self._pump()
        try:
            self.root.mainloop()
        finally:
            self._shutdown()

    def _shutdown(self):
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        if self._save_after is not None:
            # 예약된 저장이 있으면 취소하지 말고 지금 흘려보낸다.
            self.save()
        for attr in ("_pump_after", "_topmost_after", "_render_after"):
            handle = getattr(self, attr, None)
            if handle is not None:
                try:
                    self.root.after_cancel(handle)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        native.disable_file_drop()
        log.info("종료")

    def quit(self):
        self.save()
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:
                log.exception("종료 콜백 실패")
        try:
            self.root.quit()
        except tk.TclError:
            pass
