"""Win32 제약 실측 프로브.

계획에서 세운 가정 중 실제로 깨질 수 있는 것들을 창을 띄워 직접 확인한다.
창은 1.5초 뒤 스스로 닫힌다.

    python tools/probe_win32.py
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402

from lane_cover import native  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%-4s %-46s %s" % ("PASS" if ok else "FAIL", name, detail))


def main():
    print("=" * 78)
    print("Win32 제약 실측 프로브")
    print("=" * 78)

    level = native.enable_dpi_awareness()
    check("DPI 인식 설정", level != "none", "요청=%s 실제=%s" % (level, native.read_dpi_awareness()))

    root = tk.Tk()

    # 제약 2: overrideredirect 는 매핑 전에 1회만. 매핑 후 바꾸면 래퍼가 재생성된다.
    root.overrideredirect(True)
    root.geometry("400x160+80+80")

    # 제약 5: alpha/transparentcolor 를 먼저 설정한 뒤 ex-style 비트를 OR 한다.
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.85)

    canvas = tk.Canvas(root, bg="#204060", highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    root.update_idletasks()

    child = native.child_hwnd(root)
    top = native.top_hwnd(root)

    print("\n--- 핸들 ---")
    print("winfo_id()  (자식)   = 0x%x" % child)
    print("wm_frame()  (래퍼)   = 0x%x" % top)
    print("GetParent(자식)      = 0x%x" % (native.u32.GetParent(child) or 0))

    # 제약 1: 둘은 반드시 달라야 한다. 같다면 SetWindowPos 대상이 잘못된 것이다.
    check("자식 HWND != 래퍼 HWND (제약 1)", child != top and top != 0,
          "자식=0x%x 래퍼=0x%x" % (child, top))
    check("GetParent(자식) == 래퍼 (제약 1)",
          (native.u32.GetParent(child) or 0) == top)

    exstyle = native.u32.GetWindowLongPtrW(top, native.GWL_EXSTYLE)
    check("래퍼에 WS_EX_LAYERED 있음 (제약 4: Tk 가 소유)",
          bool(exstyle & native.WS_EX_LAYERED), "exstyle=0x%08x" % exstyle)
    check("래퍼에 WS_EX_TOOLWINDOW 이미 있음 (제약 3: 추가 불필요)",
          bool(exstyle & native.WS_EX_TOOLWINDOW))
    check("래퍼에 WS_EX_APPWINDOW 없음 (작업표시줄 미노출)",
          not (exstyle & native.WS_EX_APPWINDOW))

    u32 = native.u32
    check("창이 보이는 상태 (alpha 적용 후 사라지지 않음, 제약 4)",
          bool(u32.IsWindowVisible(top)))

    print("\n--- 클릭 통과 토글 ---")
    native.set_click_through(top, True)
    ex_on = native.u32.GetWindowLongPtrW(top, native.GWL_EXSTYLE)
    check("TRANSPARENT 켜짐", bool(ex_on & native.WS_EX_TRANSPARENT),
          "exstyle=0x%08x" % ex_on)
    check("NOACTIVATE 켜짐", bool(ex_on & native.WS_EX_NOACTIVATE))
    check("LAYERED 유지 (Tk 상태 안 깨짐)", bool(ex_on & native.WS_EX_LAYERED))
    check("여전히 보임", bool(u32.IsWindowVisible(top)))

    native.set_click_through(top, False)
    ex_off = native.u32.GetWindowLongPtrW(top, native.GWL_EXSTYLE)
    check("TRANSPARENT/NOACTIVATE 해제됨",
          not (ex_off & (native.WS_EX_TRANSPARENT | native.WS_EX_NOACTIVATE)),
          "exstyle=0x%08x" % ex_off)
    check("해제 후에도 LAYERED 유지", bool(ex_off & native.WS_EX_LAYERED))

    print("\n--- SetWindowPos (제약 11: NOREDRAW 없이) ---")
    native.move_window(top, 120, 140, 500, 200)
    root.update_idletasks()
    rect = native.get_window_rect(top)
    check("move_window 로 위치/크기 반영", rect == (120, 140, 500, 200), "rect=%r" % (rect,))
    check("Tk 가 물리 픽셀 좌표를 보고 (DPI 인식)",
          (root.winfo_rootx(), root.winfo_rooty()) == (120, 140),
          "tk=(%d,%d) win32=(%d,%d)" % (root.winfo_rootx(), root.winfo_rooty(), rect[0], rect[1]))
    check("Tk 요청 크기도 동기화됨",
          (root.winfo_width(), root.winfo_height()) == (500, 200),
          "tk=%dx%d" % (root.winfo_width(), root.winfo_height()))

    # 제약 10: 음수 좌표는 geometry 문자열로 왕복할 수 없다 -> 정수+SetWindowPos 로 복원.
    native.move_window(top, -300, -200, 400, 160)
    root.update_idletasks()
    rect_neg = native.get_window_rect(top)
    check("음수 좌표 move_window (제약 10)",
          rect_neg is not None and rect_neg[0] == -300 and rect_neg[1] == -200,
          "rect=%r" % (rect_neg,))
    native.move_window(top, 80, 80, 400, 160)

    print("\n--- 파일 드롭 설치 (제약 7: 포인터 절단) ---")
    got = []

    def on_drop(paths):
        got.append(paths)

    ok = native.enable_file_drop(root, on_drop)
    check("드롭 서브클래싱 설치", ok)
    # Tk 는 래퍼/톱레벨 창/위젯 창을 따로 만든다. 셸이 어느 것으로 보낼지 모르므로 전부 건다.
    hooked = dict(native._drop_procs)
    check("래퍼와 자손 전부에 설치", len(hooked) >= 2,
          "창=%s" % ["0x%x" % h for h in hooked])
    check("기존 WNDPROC 포인터가 32비트로 잘리지 않음 (제약 7)",
          all(old and old > 0xFFFFFFFF for old, _proc in hooked.values()),
          "old_procs=%s" % ["0x%x" % old for old, _p in hooked.values()])

    native.disable_file_drop()
    check("서브클래싱 해제 후 원복",
          all(native.u32.GetWindowLongPtrW(hwnd, native.GWLP_WNDPROC) == old
              for hwnd, (old, _proc) in hooked.items()),
          "%s" % ["0x%x" % native.u32.GetWindowLongPtrW(hwnd, native.GWLP_WNDPROC)
                  for hwnd in hooked])

    def finish():
        print("\n" + "=" * 78)
        failed = [r for r in results if not r[1]]
        print("총 %d개 중 %d개 실패" % (len(results), len(failed)))
        for name, _, detail in failed:
            print("  FAIL: %s  %s" % (name, detail))
        print("=" * 78)
        root.destroy()

    root.after(1500, finish)
    root.mainloop()
    return 1 if any(not r[1] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
