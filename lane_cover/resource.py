"""번들 리소스 경로 해석.

onefile 빌드에서는 실행마다 ``%TEMP%\\_MEIxxxxxx`` 로 새로 풀리므로 ``_MEIPASS`` 경로가
매 실행마다 달라진다. 따라서 이 경로를 설정 파일에 저장하면 안 된다 (다음 실행에서 깨진다).
"""

import os
import sys


def app_dir():
    """실행 파일(또는 소스 트리)이 있는 폴더.

    onefile 빌드에서도 ``_MEIPASS`` 가 아니라 **exe가 놓인 실제 폴더**를 돌려준다.
    설정 파일 폴백 위치로 쓴다.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts):
    """번들에 포함된 읽기 전용 리소스의 절대 경로."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = app_dir()
    return os.path.join(base, *parts)


def icon_path():
    """트레이/창 아이콘(.ico)."""
    return resource_path("assets", "icon.ico")
