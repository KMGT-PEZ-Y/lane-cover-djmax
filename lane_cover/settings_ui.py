"""설정 창.

커버 본체와 달리 **일반 장식이 있는 창**(제목 표시줄/작업표시줄 등록)으로 띄운다. 커버는
프레임이 없고 최상단이라, 설정 창이 그 뒤에 가려지면 조작할 수 없기 때문이다.
설정 창 자체도 topmost 로 올려 커버에 가리지 않게 한다.

편집은 사본에 하고 '저장'을 눌러야 반영한다. 슬라이더를 움직일 때마다 창이 바뀌면
되돌리기 어렵기 때문이다. 단, 투명도만은 즉시 미리보기를 제공한다.
"""

import os
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from . import config, native, patterns
from .log import get_logger, log_path

log = get_logger("settings")


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.working = config.Config.from_dict(app.cfg.to_dict())

        self.win = tk.Toplevel(app.root)
        self.win.title("Custom Lane Cover - 설정")
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.geometry("+%d+%d" % self._initial_position())

        self._build()
        self._load_into_widgets()

    # ------------------------------------------------------------------ 구성

    def _initial_position(self):
        vs = native.virtual_screen_rect() or (0, 0, 1280, 800)
        geom = self.app.cfg.geometry
        x = min(max(geom.x + geom.w + 24, vs[0] + 16), vs[0] + vs[2] - 560)
        y = min(max(geom.y + 40, vs[1] + 16), vs[1] + vs[3] - 640)
        return int(x), int(y)

    @property
    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def focus(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def _build(self):
        pad = {"padx": 8, "pady": 3}
        outer = ttk.Frame(self.win, padding=10)
        outer.pack(fill="both", expand=True)

        # --- 패턴 ---------------------------------------------------------
        pattern_box = ttk.LabelFrame(outer, text="패턴", padding=8)
        pattern_box.pack(fill="x", pady=(0, 8))
        pattern_box.columnconfigure(0, weight=1)

        self.image_var = tk.StringVar()
        row = ttk.Frame(pattern_box)
        row.grid(row=0, column=0, sticky="ew")
        row.columnconfigure(0, weight=1)
        ttk.Label(row, textvariable=self.image_var, anchor="w").grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="찾아보기", width=9, command=self._browse).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(row, text="지우기", width=7, command=self._clear_image).grid(row=0, column=2, padx=(4, 0))

        ttk.Label(
            pattern_box,
            text="창에 이미지 파일을 끌어다 놓아도 됩니다 (편집 모드에서만).",
            foreground="#666",
        ).grid(row=1, column=0, sticky="w", pady=(2, 6))

        ttk.Label(pattern_box, text="맞춤 방식").grid(row=2, column=0, sticky="w")
        fit_row = ttk.Frame(pattern_box)
        fit_row.grid(row=3, column=0, sticky="w")
        self.fit_var = tk.StringVar()
        for index, mode in enumerate(config.FIT_MODES):
            ttk.Radiobutton(
                fit_row, text=config.FIT_LABELS[mode], value=mode, variable=self.fit_var
            ).grid(row=index // 3, column=index % 3, sticky="w", padx=(0, 10))

        color_row = ttk.Frame(pattern_box)
        color_row.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(color_row, text="배경색 (이미지 없을 때 / 여백)").pack(side="left")
        self.bg_color_var = tk.StringVar()
        ttk.Entry(color_row, textvariable=self.bg_color_var, width=10).pack(side="left", padx=6)
        ttk.Button(color_row, text="선택", width=6,
                   command=lambda: self._pick_color(self.bg_color_var)).pack(side="left")

        # --- 표시 ---------------------------------------------------------
        display_box = ttk.LabelFrame(outer, text="표시", padding=8)
        display_box.pack(fill="x", pady=(0, 8))

        ttk.Label(display_box, text="투명도").grid(row=0, column=0, sticky="w")
        self.opacity_var = tk.DoubleVar()
        scale = ttk.Scale(
            display_box, from_=0.1, to=1.0, variable=self.opacity_var,
            orient="horizontal", length=220, command=self._on_opacity_preview,
        )
        scale.grid(row=0, column=1, sticky="w", padx=6)
        self.opacity_label = ttk.Label(display_box, width=5)
        self.opacity_label.grid(row=0, column=2, sticky="w")

        self.colorkey_var = tk.BooleanVar()
        ttk.Checkbutton(
            display_box, text="컬러키 투명 사용", variable=self.colorkey_var,
            command=self._update_colorkey_state,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        key_row = ttk.Frame(display_box)
        key_row.grid(row=2, column=0, columnspan=3, sticky="ew")
        ttk.Label(key_row, text="지정한 색을 완전히 투명하게 (구멍 뚫기)").pack(side="left")
        self.key_color_var = tk.StringVar()
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_color_var, width=10)
        self.key_entry.pack(side="left", padx=6)
        self.key_button = ttk.Button(key_row, text="선택", width=6,
                                     command=lambda: self._pick_color(self.key_color_var))
        self.key_button.pack(side="left")

        # --- 위치/크기 ----------------------------------------------------
        geom_box = ttk.LabelFrame(outer, text="위치 / 크기 (px)", padding=8)
        geom_box.pack(fill="x", pady=(0, 8))
        self.geom_vars = {}
        for index, (key, label) in enumerate(
            (("x", "X"), ("y", "Y"), ("w", "너비"), ("h", "높이"))
        ):
            ttk.Label(geom_box, text=label).grid(row=0, column=index * 2, sticky="e", padx=(0 if index == 0 else 10, 3))
            var = tk.StringVar()
            self.geom_vars[key] = var
            ttk.Entry(geom_box, textvariable=var, width=7).grid(row=0, column=index * 2 + 1, sticky="w")

        ttk.Button(geom_box, text="화면 상단 중앙에 배치", command=self._place_top_center).grid(
            row=1, column=0, columnspan=8, sticky="w", pady=(8, 0)
        )

        # --- 동작 ---------------------------------------------------------
        behaviour = ttk.LabelFrame(outer, text="동작", padding=8)
        behaviour.pack(fill="x", pady=(0, 8))

        ttk.Label(behaviour, text="최상단 재고정 주기 (ms, 0=끄기)").grid(row=0, column=0, sticky="w")
        self.topmost_var = tk.StringVar()
        ttk.Entry(behaviour, textvariable=self.topmost_var, width=8).grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(behaviour, text="그리드 스냅 (px, Shift 누르면 적용)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.grid_var = tk.StringVar()
        ttk.Entry(behaviour, textvariable=self.grid_var, width=8).grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))

        # --- 단축키 -------------------------------------------------------
        hotkey_box = ttk.LabelFrame(outer, text="전역 단축키", padding=8)
        hotkey_box.pack(fill="x", pady=(0, 8))

        self.hotkey_labels = {}
        for row_index, action in enumerate(config.DEFAULT_HOTKEYS):
            ttk.Label(hotkey_box, text=config.HOTKEY_LABELS[action]).grid(
                row=row_index, column=0, sticky="w"
            )
            label = ttk.Label(hotkey_box, text="", foreground="#0a6")
            label.grid(row=row_index, column=1, sticky="w", padx=(10, 0))
            self.hotkey_labels[action] = label

        self.hotkey_note = ttk.Label(hotkey_box, text="", foreground="#a60", wraplength=430, justify="left")
        self.hotkey_note.grid(row=len(config.DEFAULT_HOTKEYS), column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(
            hotkey_box,
            text="게임이 관리자 권한으로 실행 중이면 Windows UIPI 때문에 단축키가\n"
                 "등록되어 있어도 전달되지 않습니다. 이 경우 이 프로그램도 관리자로 실행하세요.",
            foreground="#666", justify="left",
        ).grid(row=len(config.DEFAULT_HOTKEYS) + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # --- 진단 ---------------------------------------------------------
        diag = ttk.LabelFrame(outer, text="진단", padding=8)
        diag.pack(fill="x", pady=(0, 8))
        ttk.Label(
            diag, justify="left", foreground="#666",
            text="DPI 인식: %s\n설정 파일: %s\n로그: %s" % (
                native.read_dpi_awareness(), config.config_path(), log_path()
            ),
        ).pack(anchor="w")

        # --- 버튼 ---------------------------------------------------------
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(4, 0))
        ttk.Button(buttons, text="저장", command=self.save).pack(side="right")
        ttk.Button(buttons, text="닫기", command=self.close).pack(side="right", padx=6)
        ttk.Button(buttons, text="기본값 복원", command=self._reset).pack(side="left")

    # ------------------------------------------------------------------ 값 <-> 위젯

    def _load_into_widgets(self):
        cfg = self.working
        self.image_var.set(patterns.describe(cfg.pattern.image))
        self.fit_var.set(cfg.pattern.fit)
        self.bg_color_var.set(cfg.pattern.color)
        self.opacity_var.set(cfg.opacity)
        self._on_opacity_preview()
        self.colorkey_var.set(cfg.color_key.enabled)
        self.key_color_var.set(cfg.color_key.color)
        self.geom_vars["x"].set(str(cfg.geometry.x))
        self.geom_vars["y"].set(str(cfg.geometry.y))
        self.geom_vars["w"].set(str(cfg.geometry.w))
        self.geom_vars["h"].set(str(cfg.geometry.h))
        self.topmost_var.set(str(cfg.topmost_reassert_ms))
        self.grid_var.set(str(cfg.snap.grid))

        for action, label in self.hotkey_labels.items():
            label.configure(text=cfg.hotkeys.get(action, ""), foreground="#0a6")
        self._update_colorkey_state()
        self._show_hotkey_status()

    def _update_colorkey_state(self):
        state = "normal" if self.colorkey_var.get() else "disabled"
        self.key_entry.configure(state=state)
        self.key_button.configure(state=state)

    def _on_opacity_preview(self, _value=None):
        """투명도만은 슬라이더를 움직이는 즉시 창에 반영해 미리보기를 준다."""
        value = self.opacity_var.get()
        self.opacity_label.configure(text="%.2f" % value)
        try:
            self.app.root.attributes("-alpha", value)
        except tk.TclError:
            pass

    def _pick_color(self, var):
        current = var.get()
        _rgb, hex_color = colorchooser.askcolor(color=current, parent=self.win)
        if hex_color:
            var.set(hex_color.upper())

    def _browse(self):
        path = filedialog.askopenfilename(
            parent=self.win,
            title="레인 커버 패턴 이미지 선택",
            filetypes=[("이미지", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("모든 파일", "*.*")],
        )
        if path:
            self.working.pattern.image = path
            self.image_var.set(patterns.describe(path))

    def _clear_image(self):
        self.working.pattern.image = None
        self.image_var.set(patterns.describe(None))

    def _place_top_center(self):
        vs = native.virtual_screen_rect()
        if not vs:
            return
        vx, vy, vw, _vh = vs
        try:
            width = int(self.geom_vars["w"].get())
        except ValueError:
            width = self.working.geometry.w
        self.geom_vars["x"].set(str(vx + (vw - width) // 2))
        self.geom_vars["y"].set(str(vy))

    def _reset(self):
        """모양 관련 설정만 기본값으로. 위치/크기와 모드는 유지한다."""
        if not messagebox.askyesno(
            "기본값 복원", "패턴·투명도·컬러키·동작·단축키를 기본값으로 되돌릴까요?\n"
                          "(위치와 크기는 유지됩니다)", parent=self.win,
        ):
            return
        fresh = config.Config()
        self.working.pattern = fresh.pattern
        self.working.opacity = fresh.opacity
        self.working.color_key = fresh.color_key
        self.working.hotkeys = fresh.hotkeys
        self.working.topmost_reassert_ms = fresh.topmost_reassert_ms
        self.working.snap = fresh.snap
        self._load_into_widgets()

    def _show_hotkey_status(self):
        shell = getattr(self.app, "shell", None)
        if shell is None:
            self.hotkey_note.configure(text="")
            return
        failed = getattr(shell, "failed_hotkeys", [])
        if not failed:
            self.hotkey_note.configure(text="")
            return
        lines = [
            "· %s (%s): %s" % (
                config.HOTKEY_LABELS.get(action, action), spec, reason
            )
            for action, spec, reason in failed
        ]
        self.hotkey_note.configure(
            text="등록하지 못한 단축키가 있습니다:\n" + "\n".join(lines)
        )
        for action in self.hotkey_labels:
            if any(action == f[0] for f in failed):
                self.hotkey_labels[action].configure(foreground="#c00")

    # ------------------------------------------------------------------ 저장

    def _collect(self):
        """위젯 값을 working 사본에 반영한다. 잘못된 입력은 기본값으로 되돌린다."""
        cfg = self.working
        cfg.pattern.fit = self.fit_var.get() or "stretch"
        cfg.pattern.color = self.bg_color_var.get()
        cfg.opacity = self.opacity_var.get()
        cfg.color_key.enabled = bool(self.colorkey_var.get())
        cfg.color_key.color = self.key_color_var.get()
        for key in ("x", "y", "w", "h"):
            setattr(cfg.geometry, key, self.geom_vars[key].get())
        cfg.topmost_reassert_ms = self.topmost_var.get()
        cfg.snap.grid = self.grid_var.get()

        # 문자열 -> 숫자 정규화 (config 의 관대한 파서를 그대로 재사용한다)
        cfg.geometry = config.Geometry.from_dict(cfg.geometry.to_dict())
        cfg.topmost_reassert_ms = config.Config.from_dict(
            {"topmost_reassert_ms": cfg.topmost_reassert_ms}
        ).topmost_reassert_ms
        cfg.snap = config.Snap.from_dict(cfg.snap.to_dict())
        cfg.pattern = config.Pattern.from_dict(cfg.pattern.to_dict())
        cfg.color_key = config.ColorKey.from_dict(cfg.color_key.to_dict())
        return cfg

    def save(self):
        before = self.app.cfg
        hotkeys_changed = dict(before.hotkeys) != dict(self.working.hotkeys)

        cfg = self._collect()
        cfg.mode = before.mode
        cfg.visible = before.visible

        if cfg.pattern.image and not os.path.isfile(cfg.pattern.image):
            messagebox.showwarning(
                "패턴", "이미지 파일을 찾을 수 없어 패턴을 비웁니다:\n%s" % cfg.pattern.image,
                parent=self.win,
            )
            cfg.pattern.image = None

        self.app.apply_config(cfg)
        self.app.save()
        self._load_into_widgets()

        if hotkeys_changed:
            callback = getattr(self.app, "on_hotkeys_changed", None)
            if callback is not None:
                callback()
                self._show_hotkey_status()
                messagebox.showinfo("단축키", "단축키를 다시 등록했습니다.", parent=self.win)
            else:
                messagebox.showinfo(
                    "단축키", "단축키 변경은 프로그램을 다시 시작한 뒤 적용됩니다.",
                    parent=self.win,
                )
        log.info("설정 저장 완료")

    def close(self):
        try:
            self.app.root.attributes("-alpha", self.app.cfg.opacity)
        except tk.TclError:
            pass
        self.app._settings = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass
