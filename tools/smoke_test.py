"""소스 실행 스모크 테스트.

실제 창/트레이/단축키를 띄우고 주요 동작을 순서대로 실행하며 상태를 검사한다.
약 12초 뒤 스스로 끝난다.

    python tools/smoke_test.py

사용자의 실제 설정을 건드리지 않도록 APPDATA 를 임시 폴더로 돌린다.
"""

import os
import shutil
import sys
import tempfile

# config 모듈이 폴더를 캐시하기 전에 돌려놓아야 한다.
_SANDBOX = tempfile.mkdtemp(prefix="lanecover-smoke-")
os.environ["APPDATA"] = _SANDBOX

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%-4s %-52s %s" % ("PASS" if ok else "FAIL", name, detail))


def make_test_image(path):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 160), (20, 24, 40))
    d = ImageDraw.Draw(img)
    for index, color in enumerate(
        [(230, 80, 90), (240, 170, 60), (90, 210, 130), (80, 170, 240), (170, 110, 230)]
    ):
        d.rectangle([index * 80, 0, index * 80 + 78, 159], fill=color)
    d.rectangle([0, 40, 399, 60], fill=(255, 255, 255))
    img.save(path)


PREVIEW_DIR = os.path.join(tempfile.gettempdir(), "lanecover-preview")


def write_fit_preview(test_image):
    """합성 결과를 PNG 로 떠서 눈으로 확인할 수 있게 남긴다 (Tk 불필요)."""
    from PIL import Image

    from lane_cover import patterns
    from lane_cover.patterns import _bg_rgba

    renderer = patterns.PatternRenderer()
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    sheet = Image.new("RGB", (360, (130 + 8) * 5), (45, 45, 45))
    source = Image.open(test_image).convert("RGBA")
    for index, fit in enumerate(("stretch", "fill", "fit", "tile", "center")):
        composed = renderer._compose(
            source, 360, 130, fit, _bg_rgba("#101018"), Image.Resampling.LANCZOS
        )
        sheet.paste(composed.convert("RGB"), (0, index * (130 + 8)))
    sheet.save(os.path.join(PREVIEW_DIR, "fit-modes.png"))
    print("맞춤 방식 미리보기: %s" % os.path.join(PREVIEW_DIR, "fit-modes.png"))


def send_wm_dropfiles(hwnd, path):
    """합성 WM_DROPFILES 를 실제로 보내 드롭 경로를 태운다.

    ``HDROP`` 는 **전역 메모리 핸들**이어야 한다. 파이썬 버퍼의 포인터를 넘기면 핸들러가
    ``DragFinish`` 로 해제할 때 전역 힙이 아닌 포인터를 건드려 프로세스가 죽는다.
    """
    import ctypes
    import struct
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    # DROPFILES: DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide;  => 20바이트 (x64)
    files_offset = 20
    payload = path.encode("utf-16-le") + b"\x00\x00\x00\x00"  # 이중 널 종료
    size = files_offset + len(payload)

    block = bytearray(size)
    struct.pack_into("<I", block, 0, files_offset)   # pFiles = 파일 목록 오프셋
    struct.pack_into("<i", block, 4, 0)              # pt.x
    struct.pack_into("<i", block, 8, 0)              # pt.y
    struct.pack_into("<i", block, 12, 0)             # fNC = FALSE
    struct.pack_into("<i", block, 16, 1)             # fWide = TRUE
    block[files_offset:] = payload

    handle = k32.GlobalAlloc(0x0002 | 0x0040, size)  # GMEM_MOVEABLE | GMEM_ZEROINIT
    if not handle:
        return False
    pointer = k32.GlobalLock(handle)
    if not pointer:
        return False
    ctypes.memmove(pointer, bytes(block), size)
    k32.GlobalUnlock(handle)

    # 여기서부터 메모리 소유권은 핸들러(=DragFinish)에게 넘어간다.
    u32.SendMessageW(hwnd, 0x0233, handle, 0)   # WM_DROPFILES
    return True


def check_fit_renders(test_image):
    """render() 는 PhotoImage 를 만들므로 Tk 루트가 존재한 뒤에만 호출할 수 있다."""
    from lane_cover import patterns

    renderer = patterns.PatternRenderer()
    for fit in ("stretch", "fill", "fit", "tile", "center"):
        photo = renderer.render(test_image, 360, 130, fit, "#101018", True)
        check("렌더: %s" % fit, photo is not None,
              "" if photo else "PhotoImage 생성 실패")
    check("캐시 적중 (같은 키 재요청)",
          renderer.render(test_image, 360, 130, "stretch", "#101018", True) is not None)


def main():
    import queue
    import tkinter as tk

    from lane_cover import app as app_mod
    from lane_cover import config, native, patterns, tray
    from lane_cover.resource import icon_path

    print("=" * 84)
    print("스모크 테스트 (sandbox=%s)" % _SANDBOX)
    print("=" * 84)

    test_image = os.path.join(_SANDBOX, "test-pattern.png")
    make_test_image(test_image)

    write_fit_preview(test_image)

    level = native.enable_dpi_awareness()
    check("DPI 인식", level != "none", level)

    events = queue.Queue()
    warnings = []

    app = app_mod.LaneCoverApp(events=events, on_quit=None)
    # 모달 대화상자가 테스트를 멈추지 않게 경고를 가로챈다.
    app.show_warning = lambda title, message: warnings.append((title, message))

    print("\n--- 창 ---")
    check("래퍼 HWND 확보", app._hwnd != 0, "0x%x" % app._hwnd)
    check("래퍼 != 자식", app._hwnd != native.child_hwnd(app.root))
    check("초기 모드 edit", app.cfg.mode == "edit")
    check("창이 보임", bool(native.u32.IsWindowVisible(app._hwnd)))
    check("드롭 서브클래싱 설치 (래퍼+자손 전부)", len(native._drop_procs) >= 3,
          "걸린 창=%s" % ["0x%x" % h for h in native._drop_procs])

    print("\n--- 패턴 렌더 (Tk 존재 후) ---")
    check_fit_renders(test_image)

    print("\n--- 트레이 / 단축키 ---")
    shell = tray.NativeShell(
        queue=events, hotkey_specs=app.cfg.hotkeys, icon_path=icon_path()
    )
    shell.start()
    app.shell = shell
    app.allow_play_mode = True
    check("단축키 4개 등록", len(shell.active_hotkeys) == 4,
          "활성=%r 실패=%r" % (shell.active_hotkeys, shell.failed_hotkeys))
    check("트레이 아이콘 등록", shell.tray_ok)
    check("셸 창은 HWND_MESSAGE 가 아님", shell._hwnd != 0)
    parsed = tray.parse_hotkey("Ctrl+Alt+E")
    check("단축키 파싱 Ctrl+Alt+E", parsed == (native.MOD_CONTROL | native.MOD_ALT, 0x45),
          "mods=0x%x vk=0x%x" % parsed)

    print("\n--- 모드 전환 / 클릭 통과 ---")

    def exstyle():
        return native.u32.GetWindowLongPtrW(app._hwnd, native.GWL_EXSTYLE)

    # 클릭 통과는 **LAYERED 와 TRANSPARENT 가 둘 다** 있어야 성립한다.
    # TRANSPARENT 단독은 아무 효과가 없다 (MSDN 상 그 비트는 그리기 순서 이야기이고,
    # 히트 테스트에서 빠지는 것은 레이어드 창의 성질이다). 순수 Win32 창 조합 전수 실측으로
    # 확인했다 (tools/probe_recipe.py). 실제 입력을 주입한 최종 확인은
    # tools/probe_clickthrough.py 가 담당한다 — 여기서는 비트 조합까지만 본다.
    # WindowFromPoint 는 래퍼가 아니라 자식을 돌려주므로 판정에 쓸 수 없다 (실측).
    app.cfg.opacity = 1.0
    app.cfg.color_key.enabled = False
    app.cfg.mode = "edit"
    app._apply_visual_attributes()
    app._sync_native()
    app.root.update()
    check("opacity 1.0 이면 Tk 는 LAYERED 를 안 켠다 (전제 확인)",
          not (exstyle() & native.WS_EX_LAYERED), "exstyle=0x%08x" % exstyle())
    check("edit: TRANSPARENT 없음", not (exstyle() & native.WS_EX_TRANSPARENT))
    check("edit: NOACTIVATE 없음 (키 입력 가능)",
          not (exstyle() & native.WS_EX_NOACTIVATE))
    check("edit: TOPMOST 유지", bool(exstyle() & native.WS_EX_TOPMOST))
    check("edit: TOOLWINDOW 유지 (Alt+Tab 숨김)", bool(exstyle() & native.WS_EX_TOOLWINDOW))

    app.set_mode("play", save=False)
    app.root.update()
    check("play 진입", app.cfg.mode == "play")
    check("play: LAYERED 켜짐 (TRANSPARENT 만으로는 통과 안 됨)",
          bool(exstyle() & native.WS_EX_LAYERED), "exstyle=0x%08x" % exstyle())
    check("play: TRANSPARENT 켜짐", bool(exstyle() & native.WS_EX_TRANSPARENT))
    check("play: NOACTIVATE 켜짐 (포커스 안 뺏음)",
          bool(exstyle() & native.WS_EX_NOACTIVATE))
    check("play: TOPMOST 유지 (항상 최상단)", bool(exstyle() & native.WS_EX_TOPMOST),
          "exstyle=0x%08x" % exstyle())
    check("play: TOOLWINDOW 유지", bool(exstyle() & native.WS_EX_TOOLWINDOW))
    check("play: 창이 보임 (LAYERED 를 직접 켰어도 사라지지 않음)",
          bool(native.u32.IsWindowVisible(app._hwnd)))

    app.set_mode("edit", save=False)
    app.root.update()
    check("edit 복귀", app.cfg.mode == "edit")
    check("edit: TRANSPARENT 해제", not (exstyle() & native.WS_EX_TRANSPARENT))
    check("edit: NOACTIVATE 해제 (키 입력 가능)",
          not (exstyle() & native.WS_EX_NOACTIVATE))
    check("edit: TOPMOST 유지", bool(exstyle() & native.WS_EX_TOPMOST))
    check("edit: TOOLWINDOW 유지", bool(exstyle() & native.WS_EX_TOOLWINDOW))

    # 제약 5 회귀: Tk 는 -alpha 를 처음 걸 때 ex-style 을 통째로 다시 쓴다.
    # 시각 속성을 바꾼 뒤 _sync_native 를 부르면 복구되어야 한다.
    app.set_mode("play", save=False)
    app.cfg.opacity = 0.7
    app._apply_visual_attributes()
    app._sync_native()
    app.root.update()
    check("투명도 변경 후에도 TRANSPARENT 유지 (제약 5)",
          bool(exstyle() & native.WS_EX_TRANSPARENT), "exstyle=0x%08x" % exstyle())
    check("투명도 변경 후에도 LAYERED 유지", bool(exstyle() & native.WS_EX_LAYERED))
    check("투명도 변경 후에도 TOOLWINDOW 유지", bool(exstyle() & native.WS_EX_TOOLWINDOW))
    app.cfg.opacity = 1.0
    app._apply_visual_attributes()
    app._sync_native()
    app.set_mode("edit", save=False)
    app.root.update()

    check("colorref 변환 '#123456'", native.colorref("#123456") == 0x563412,
          "0x%06x" % native.colorref("#123456"))
    check("colorref 변환 잘못된 값", native.colorref("red") == 0)
    check("assert_tool_window 멱등", native.assert_tool_window(app._hwnd) is None)

    print("\n--- 투명도 / 컬러키 (창이 사라지지 않는지) ---")
    for opacity in (0.5, 0.85, 1.0, 0.3):
        app.cfg.opacity = opacity
        app._apply_visual_attributes()
        app._sync_native()
        if not native.u32.IsWindowVisible(app._hwnd):
            check("투명도 %.2f 후 창 유지" % opacity, False, "창이 사라짐")
            break
    else:
        check("투명도 0.5/0.85/1.0/0.3 모두 창 유지", True)

    app.cfg.color_key.enabled = True
    app.cfg.color_key.color = "#FF00FF"
    app._apply_visual_attributes()
    app._sync_native()
    check("컬러키 켠 뒤 창 유지", bool(native.u32.IsWindowVisible(app._hwnd)))
    app.cfg.color_key.enabled = False
    app._apply_visual_attributes()
    app._sync_native()
    check("컬러키 해제 뒤 창 유지", bool(native.u32.IsWindowVisible(app._hwnd)))

    print("\n--- 패턴 ---")
    app.apply_pattern_path(test_image)
    check("패턴 적용", app.cfg.pattern.image == test_image)
    check("캔버스에 이미지 항목 있음", bool(app.canvas.find_withtag("pattern")))
    check("PhotoImage 참조 유지", app._photo is not None)

    for fit in config.FIT_MODES:
        app.cfg.pattern.fit = fit
        app._render_pattern(high_quality=True)
        if app._photo is None:
            check("맞춤 %s 렌더" % fit, False)
            break
    else:
        check("맞춤 방식 5종 모두 렌더", True)

    app.cfg.pattern.image = None
    app._render_pattern()
    check("패턴 지우기", not app.canvas.find_withtag("pattern"))

    # 드래그앤드롭 경로를 실제로 태운다. 셸은 커서 아래 **가장 깊은** 창으로 보내므로
    # (Canvas 가 자기 창을 가진다) 그 창에 보내야 실제와 같다.
    app.cfg.pattern.image = None
    app._render_pattern()
    drop_target = native.window_from_point(
        app._geom[0] + app._geom[2] // 2, app._geom[1] + app._geom[3] // 2
    )
    check("드롭 대상이 커버 계층 안에 있음",
          drop_target in native.widget_hwnds(app.root),
          "대상=0x%x 후보=%s" % (drop_target,
                                ["0x%x" % h for h in native.widget_hwnds(app.root)]))
    other_image = os.path.join(_SANDBOX, "dropped-pattern.png")
    make_test_image(other_image)
    check("WM_DROPFILES 전송", send_wm_dropfiles(drop_target, other_image))
    app.root.update()
    check("드롭으로 패턴이 바뀜 (핵심 기능)",
          app.cfg.pattern.image == other_image,
          "pattern=%r" % app.cfg.pattern.image)

    app.apply_pattern_path(test_image)

    print("\n--- 이동/리사이즈 ---")
    native.move_window(app._hwnd, 60, 40, 640, 200)
    app.root.update_idletasks()
    app._geom = list(native.get_window_rect(app._hwnd))
    check("move_window 반영", app._geom == [60, 40, 640, 200], "%r" % (app._geom,))
    check("Tk 가 물리 픽셀 일치",
          (app.root.winfo_rootx(), app.root.winfo_rooty()) == (60, 40),
          "tk=(%d,%d)" % (app.root.winfo_rootx(), app.root.winfo_rooty()))

    app._geom = [60, 40, 700, 260]
    app._apply_geom()
    check("크기 변경 후 Tk 동기화",
          (app.root.winfo_width(), app.root.winfo_height()) == (700, 260),
          "tk=%dx%d" % (app.root.winfo_width(), app.root.winfo_height()))

    clamped = app._clamp(-99999, -99999, 700, 260)
    vs = native.virtual_screen_rect()
    check("화면 밖으로 안 나감", clamped[0] > -99999 and clamped[1] > -99999,
          "clamp=%r 화면=%r" % (clamped, vs))

    print("\n--- 편집 크롬 ---")
    app.cfg.mode = "edit"
    app._draw_chrome()
    check("편집 모드에 크롬 있음", bool(app.canvas.find_withtag("chrome")))
    app.cfg.mode = "play"
    app._draw_chrome()
    check("플레이 모드에 크롬 없음", not app.canvas.find_withtag("chrome"))
    app.cfg.mode = "edit"
    app._draw_chrome()

    print("\n--- 설정 창 ---")
    app.open_settings()
    check("설정 창 생성", app._settings is not None and app._settings.alive)
    if app._settings:
        app._settings._load_into_widgets()
        check("설정 창 값 로드", app._settings.fit_var.get() in config.FIT_MODES,
              "fit=%s" % app._settings.fit_var.get())
        app._settings.working.pattern.image = test_image
        app._settings.save()
        check("설정 저장 동작", app.cfg.pattern.image == test_image)
        app._settings.close()
    check("설정 창 닫힘", app._settings is None)

    print("\n--- 표시/숨김 ---")
    app.set_visible(False, save=False)
    check("숨김", not bool(native.u32.IsWindowVisible(app._hwnd)))
    app.set_visible(True, save=False)
    check("표시 복귀", bool(native.u32.IsWindowVisible(app._hwnd)))
    check("표시 복귀 후 TRANSPARENT 없음 (edit)",
          not (exstyle() & native.WS_EX_TRANSPARENT))

    print("\n--- 이벤트 큐 (단축키 -> 메인 스레드) ---")
    events.put(("toggle_mode",))
    app._pump()
    app.root.update()
    check("큐 이벤트로 모드 전환됨", app.cfg.mode == "play", "mode=%s" % app.cfg.mode)
    events.put(("toggle_mode",))
    app._pump()
    app.root.update()
    check("큐 이벤트로 편집 복귀", app.cfg.mode == "edit", "mode=%s" % app.cfg.mode)

    print("\n--- 설정 영속성 ---")
    app._schedule_save(5000)
    check("저장이 예약됨 (드래그 후)", app._save_after is not None)
    app.cfg.opacity = 0.66
    app.cfg.pattern.color = "#123456"
    app.save()
    check("명시적 저장 시 예약 취소", app._save_after is None)
    check("설정 파일 생성", os.path.isfile(config.config_path()), config.config_path())
    reloaded = config.Config.load()
    check("투명도 저장/복원", abs(reloaded.opacity - 0.66) < 0.01, "%r" % reloaded.opacity)
    check("배경색 저장/복원", reloaded.pattern.color == "#123456", reloaded.pattern.color)
    check("패턴 경로 저장/복원", reloaded.pattern.image == test_image)

    # 1.0 을 넘거나 형식이 깨진 값도 관대하게 처리되는지
    broken = config.Config.from_dict(
        {"opacity": 99, "mode": "???", "pattern": {"fit": "nope", "color": "red"},
         "geometry": {"w": -5, "h": "abc"}, "hotkeys": {"quit": ""}}
    )
    check("깨진 값 방어: opacity 클램프", broken.opacity == 1.0, "%r" % broken.opacity)
    check("깨진 값 방어: mode 폴백", broken.mode == "edit")
    check("깨진 값 방어: fit 폴백", broken.pattern.fit == "stretch")
    check("깨진 값 방어: 색상 폴백", broken.pattern.color == "#101018")
    check("깨진 값 방어: 최소 크기", broken.geometry.w == config.MIN_W, "%r" % broken.geometry.w)
    check("깨진 값 방어: 빈 단축키 무시", broken.hotkeys["quit"] == "Ctrl+Alt+Q")

    print("\n--- 종료 ---")
    app.quit()          # on_quit 이 None 이라 셸은 직접 멈춘다
    shell.stop()
    check("셸 스레드 종료", not shell._thread or not shell._thread.is_alive())
    check("단축키 해제됨", not shell._hotkey_ids)
    check("트레이 아이콘 제거됨", not shell.tray_ok)
    app._shutdown()
    check("종료 시 드롭 해제", not native._drop_procs)
    app._shutdown()     # 멱등성
    check("_shutdown 멱등", not native._drop_procs)
    app.root.update()

    def finish():
        print("\n" + "=" * 84)
        failed = [r for r in results if not r[1]]
        print("총 %d개 중 %d개 실패" % (len(results), len(failed)))
        for name, _ok, detail in failed:
            print("  FAIL: %s  %s" % (name, detail))
        if warnings:
            print("\n경고 대화상자 %d건:" % len(warnings))
            for title, message in warnings:
                print("  [%s] %s" % (title, message.replace("\n", " ")[:100]))
        print("=" * 84)
        try:
            app.root.destroy()
        except tk.TclError:
            pass

    app.root.after(600, finish)
    try:
        app.root.mainloop()
    except tk.TclError:
        pass

    shutil.rmtree(_SANDBOX, ignore_errors=True)
    return 1 if any(not r[1] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
