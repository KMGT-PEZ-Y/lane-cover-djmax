"""빌드 스크립트.

    python build.py              # 단일 exe (onefile, 기본)
    python build.py --onedir     # 폴더 형태 (시작 빠름, 백신 오탐 적음)
    python build.py --clean      # build/ 와 dist/ 를 지우고 새로 빌드
    python build.py --spec-only  # spec 만 생성하고 빌드는 하지 않음

spec 파일은 이 스크립트가 생성한다. onefile/onedir 을 같은 소스에서 전환하려면
spec 을 두 벌 관리하는 것보다 생성하는 편이 안전하기 때문이다.
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "CustomLaneCover"

# 이 PC 에 numpy/opencv/jupyter 가 설치되어 있어, 배제하지 않으면 exe 가 수백 MB 로 불어난다.
EXCLUDES = [
    "numpy", "cv2", "matplotlib", "scipy", "pandas",
    "IPython", "jupyter", "notebook", "nbformat", "nbconvert", "ipykernel",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "pytest", "setuptools", "pip", "PIL.ImageQt",
]

SPEC_TEMPLATE = '''# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec - build.py 가 생성한다. 직접 고치지 말 것."""

import os

ROOT = {root!r}
ONEFILE = {onefile!r}

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "assets", "icon.ico"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={excludes!r},
    noarchive=False,
)

pyz = PYZ(a.pure)

_common = dict(
    name={name!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    manifest=os.path.join(ROOT, "CustomLaneCover.manifest"),
)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        runtime_tmpdir=None,
        **_common,
    )
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_common)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name={name!r},
    )
'''


def spec_path():
    return os.path.join(ROOT, APP_NAME + ".spec")


def write_spec(onefile):
    text = SPEC_TEMPLATE.format(
        root=ROOT, name=APP_NAME, onefile=onefile, excludes=EXCLUDES
    )
    with open(spec_path(), "w", encoding="utf-8") as fh:
        fh.write(text)
    return spec_path()


def ensure_icon():
    icon = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.isfile(icon):
        return True
    script = os.path.join(ROOT, "tools", "make_icon.py")
    print("아이콘이 없어 생성합니다: %s" % script)
    result = subprocess.run([sys.executable, script], cwd=ROOT)
    if result.returncode != 0 or not os.path.isfile(icon):
        print("아이콘 생성에 실패했습니다. assets/icon.ico 를 준비하세요.", file=sys.stderr)
        return False
    return True


def preflight():
    """빌드를 막는 환경 문제를 미리 짚어 준다."""
    problems = []

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        problems.append(
            "PyInstaller 가 없습니다. 설치하세요:\n"
            "    python -m pip install -r requirements.txt"
        )

    try:
        import PIL  # noqa: F401
    except ImportError:
        problems.append(
            "Pillow 가 없습니다. 설치하세요:\n"
            "    python -m pip install -r requirements.txt"
        )

    # Microsoft Store 판 Python 은 실행 파일이 0 바이트 앱 실행 별칭이라 PyInstaller 가
    # python39.dll 을 찾지 못하고 실패한다 (PyInstaller 5.x 에서 수정된 알려진 이슈).
    executable = sys.executable or ""
    if "WindowsApps" in executable:
        try:
            size = os.path.getsize(executable)
        except OSError:
            size = -1
        if size == 0:
            problems.append(
                "Microsoft Store 판 Python 을 쓰고 있습니다 (실행 파일이 0 바이트인 앱 실행 별칭).\n"
                "PyInstaller 가 python39.dll 을 찾지 못해 실패할 가능성이 높습니다.\n"
                "해결: python.org 에서 정식 Python 을 설치한 뒤 그 인터프리터로 빌드하세요.\n"
                "    py -3.12 -m venv .venv\n"
                "    .venv\\Scripts\\python -m pip install -r requirements.txt\n"
                "    .venv\\Scripts\\python build.py"
            )
    return problems


def main():
    parser = argparse.ArgumentParser(description="Custom Lane Cover 빌드")
    parser.add_argument("--onedir", action="store_true",
                        help="단일 exe 대신 폴더 형태로 빌드")
    parser.add_argument("--clean", action="store_true", help="build/ 와 dist/ 삭제 후 빌드")
    parser.add_argument("--spec-only", action="store_true", help="spec 만 생성")
    parser.add_argument("--noconfirm", action="store_true",
                        help="PyInstaller 의 덮어쓰기 확인을 건너뜀")
    args = parser.parse_args()

    print("=" * 78)
    print("Custom Lane Cover 빌드 (%s)" % ("onedir" if args.onedir else "onefile"))
    print("=" * 78)
    print("Python: %s" % sys.executable)

    problems = preflight()
    if problems:
        print()
        for problem in problems:
            print("[환경 문제] %s\n" % problem)
        if any("PyInstaller 가 없습니다" in p or "Pillow 가 없습니다" in p for p in problems):
            return 2
        # Store 판 Python 경고는 치명적이지 않을 수 있으니 계속 진행한다 (시도해 보고 판단).
        print("경고를 무시하고 빌드를 시도합니다...\n")

    if not ensure_icon():
        return 2

    if args.clean:
        for name in ("build", "dist"):
            path = os.path.join(ROOT, name)
            if os.path.isdir(path):
                print("삭제: %s" % path)
                shutil.rmtree(path, ignore_errors=True)

    spec = write_spec(onefile=not args.onedir)
    print("spec 생성: %s" % spec)
    if args.spec_only:
        return 0

    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", spec]
    print("실행: %s\n" % " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print("\n빌드 실패 (종료 코드 %d)" % result.returncode, file=sys.stderr)
        if "WindowsApps" in (sys.executable or ""):
            print(
                "\nMicrosoft Store 판 Python 때문일 수 있습니다. 위의 [환경 문제] 안내를 참고하세요.",
                file=sys.stderr,
            )
        return result.returncode

    dist = os.path.join(ROOT, "dist")
    target = (os.path.join(dist, APP_NAME + ".exe") if not args.onedir
              else os.path.join(dist, APP_NAME))
    print("\n" + "=" * 78)
    if os.path.exists(target):
        if os.path.isdir(target):
            total = sum(
                os.path.getsize(os.path.join(base, name))
                for base, _dirs, files in os.walk(target) for name in files
            )
            print("빌드 완료: %s  (%.1f MB)" % (target, total / 1024 / 1024))
        else:
            print("빌드 완료: %s  (%.1f MB)" % (target, os.path.getsize(target) / 1024 / 1024))
    else:
        print("빌드는 끝났지만 결과물을 찾지 못했습니다: %s" % target)
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
