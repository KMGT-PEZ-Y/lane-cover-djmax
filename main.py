"""Custom Lane Cover 진입점.

    python main.py

빌드된 exe 는 이 파일이 시작점이 된다.

호출 순서에 의미가 있다:
1. 표준 스트림 대체 - ``--noconsole`` 빌드에서는 ``sys.stdout`` 이 None 이라
   이후 어디서든 ``print``/트레이스백이 AttributeError 를 낸다.
2. 로깅 설정 - 이후 모든 진단이 파일로 남는다.
3. DPI 인식 - **Tk() 생성 전에** 호출해야 한다. 프로세스당 한 번만 성공한다.
"""

import queue
import sys


def main():
    from lane_cover import log

    log.ensure_std_streams()
    logger = log.setup()

    from lane_cover import config, native

    if not native.IS_WINDOWS:
        logger.error("이 프로그램은 Windows 전용입니다")
        print("Custom Lane Cover는 Windows에서만 동작합니다.", file=sys.stderr)
        return 1

    level = native.enable_dpi_awareness()
    logger.info(
        "DPI 인식 요청=%s 실제=%s, Python=%s",
        level, native.read_dpi_awareness(), sys.version.split()[0],
    )

    from lane_cover import tray
    from lane_cover.app import LaneCoverApp
    from lane_cover.resource import icon_path

    events = queue.Queue()

    def build_shell(hotkeys):
        shell = tray.NativeShell(
            queue=events,
            hotkey_specs=hotkeys,
            icon_path=icon_path(),
            tooltip="Custom Lane Cover",
        )
        shell.start()
        return shell

    app = None

    def shutdown_shell():
        if app is not None and app.shell is not None:
            app.shell.stop()

    app = LaneCoverApp(events=events, on_quit=shutdown_shell)

    app.shell = build_shell(app.cfg.hotkeys)
    app.shell.set_state(mode=app.cfg.mode, visible=app.cfg.visible)

    # 단축키와 트레이가 둘 다 죽으면 플레이 모드에서 나올 방법이 없다. 그 경우 진입을 막는다.
    shell = app.shell
    app.allow_play_mode = bool(shell.active_hotkeys or shell.tray_ok)
    if not app.allow_play_mode:
        logger.error("단축키와 트레이가 모두 실패 - 플레이 모드 진입을 차단한다")

    def on_hotkeys_changed():
        """설정 창에서 단축키를 바꿨을 때 셸을 재시작해 다시 등록한다."""
        app.shell.stop()
        app.shell = build_shell(app.cfg.hotkeys)
        app.shell.set_state(mode=app.cfg.mode, visible=app.cfg.visible)
        app.allow_play_mode = bool(app.shell.active_hotkeys or app.shell.tray_ok)

    app.on_hotkeys_changed = on_hotkeys_changed

    # 시작 모드가 'play' 인데 나올 수단이 없으면 편집 모드로 낮춘다.
    if app.cfg.mode == "play" and not app.allow_play_mode:
        app.cfg.mode = "edit"
    if app.cfg.mode == "play":
        app.set_mode("play", save=False)

    def report_problems():
        """트레이 아이콘이 생긴 뒤에 알린다."""
        problems = []
        if shell.failed_hotkeys:
            problems.append(
                "등록하지 못한 전역 단축키:\n"
                + "\n".join(
                    "  · %s (%s) - %s" % (config.HOTKEY_LABELS.get(a, a), s, r)
                    for a, s, r in shell.failed_hotkeys
                )
                + "\n\n설정 창에서 확인할 수 있습니다. 다른 조합으로 바꾸려면 "
                  "config.json 을 수정하세요."
            )
        if not shell.tray_ok:
            problems.append("트레이 아이콘을 만들지 못했습니다.")
        if problems:
            app.show_warning("Custom Lane Cover", "\n\n".join(problems))

    if shell.failed_hotkeys or not shell.tray_ok:
        app.root.after(400, report_problems)

    logger.info(
        "시작 완료: 단축키 %d개 활성, 트레이 %s, 모드=%s",
        len(shell.active_hotkeys), "OK" if shell.tray_ok else "실패", app.cfg.mode,
    )
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
