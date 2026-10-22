"""설정 값과 영속화.

``%APPDATA%\\CustomLaneCover\\config.json`` 에 원자적으로 쓴다 (임시 파일 -> ``os.replace``).
강제 종료되더라도 반쯤 쓰인 JSON 이 남지 않게 하기 위함이다.

좌표는 **정수로만** 다룬다. Tk 의 geometry 문자열은 선행 ``-`` 를 "오른쪽/아래에서의 거리"로
해석하므로, 주 모니터 왼쪽/위에 있는 보조 모니터의 음수 좌표를 문자열로 왕복시키면 깨진다.
복원은 항상 ``SetWindowPos`` 로 한다.
"""

import json
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from typing import Dict, Optional

APP_NAME = "CustomLaneCover"
CONFIG_VERSION = 1
CONFIG_FILE = "config.json"

# 커버 최소 크기 (px)
MIN_W = 40
MIN_H = 20

VALID_MODES = ("edit", "play")
FIT_MODES = ("stretch", "fill", "fit", "tile", "center")
FIT_LABELS = {
    "stretch": "늘이기",
    "fill": "채우고 자르기",
    "fit": "맞추고 여백",
    "tile": "타일",
    "center": "원본 크기 가운데",
}

DEFAULT_HOTKEYS = {
    "toggle_mode": "Ctrl+Alt+E",
    "toggle_visible": "Ctrl+Alt+H",
    "open_settings": "Ctrl+Alt+S",
    "quit": "Ctrl+Alt+Q",
}
HOTKEY_LABELS = {
    "toggle_mode": "편집/플레이 전환",
    "toggle_visible": "표시/숨김",
    "open_settings": "설정 열기",
    "quit": "종료",
}

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

_config_dir_cache = None


def app_dir():
    """설정 폴더 폴백용. resource 모듈과 중복을 피하려고 여기서도 최소 구현."""
    from .resource import app_dir as _app_dir

    return _app_dir()


def config_dir():
    """설정 파일이 놓일 폴더. 쓰기 가능한 곳을 골라 캐시한다."""
    global _config_dir_cache
    if _config_dir_cache:
        return _config_dir_cache

    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(appdata, APP_NAME))
    candidates.append(os.path.join(app_dir(), "data"))

    last_error = None
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("")
            os.remove(probe)
            _config_dir_cache = path
            return path
        except OSError as exc:
            last_error = exc

    # 전부 실패하면 임시 폴더라도 쓴다. 설정이 저장 안 되는 것보다는 낫다.
    fallback = os.path.join(tempfile.gettempdir(), APP_NAME)
    os.makedirs(fallback, exist_ok=True)
    _config_dir_cache = fallback
    return fallback


def config_path():
    return os.path.join(config_dir(), CONFIG_FILE)


# --------------------------------------------------------------------------- 값 정규화


def _norm_color(value, fallback):
    if isinstance(value, str) and _HEX_COLOR.match(value.strip()):
        return value.strip().upper()
    return fallback


def _norm_int(value, fallback, lo=None, hi=None):
    try:
        out = int(value)
    except (TypeError, ValueError):
        return fallback
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _norm_float(value, fallback, lo=None, hi=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return fallback
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def _norm_choice(value, choices, fallback):
    if isinstance(value, str) and value in choices:
        return value
    return fallback


def _norm_bool(value, fallback):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    return fallback


def _as_dict(value):
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- 하위 구조


@dataclass
class Geometry:
    x: int = 100
    y: int = 0
    w: int = 720
    h: int = 260

    @classmethod
    def from_dict(cls, raw):
        raw = _as_dict(raw)
        return cls(
            x=_norm_int(raw.get("x"), 100),
            y=_norm_int(raw.get("y"), 0),
            w=_norm_int(raw.get("w"), 720, lo=MIN_W),
            h=_norm_int(raw.get("h"), 260, lo=MIN_H),
        )

    def to_dict(self):
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass
class Pattern:
    """커버에 입히는 이미지와 맞춤 방식.

    ``image`` 가 None 이면 ``color`` 단색으로 채운다 (최초 실행 상태).
    """

    image: Optional[str] = None
    fit: str = "stretch"
    color: str = "#101018"

    @classmethod
    def from_dict(cls, raw):
        raw = _as_dict(raw)
        image = raw.get("image")
        if not isinstance(image, str) or not image.strip():
            image = None
        return cls(
            image=image,
            fit=_norm_choice(raw.get("fit"), FIT_MODES, "stretch"),
            color=_norm_color(raw.get("color"), "#101018"),
        )

    def to_dict(self):
        return {"image": self.image, "fit": self.fit, "color": self.color}


@dataclass
class ColorKey:
    """지정한 색을 완전히 투명하게 만든다 (단색/템플릿 커버에 구멍 뚫기 용도)."""

    enabled: bool = False
    color: str = "#FF00FF"

    @classmethod
    def from_dict(cls, raw):
        raw = _as_dict(raw)
        return cls(
            enabled=_norm_bool(raw.get("enabled"), False),
            color=_norm_color(raw.get("color"), "#FF00FF"),
        )

    def to_dict(self):
        return {"enabled": self.enabled, "color": self.color}


@dataclass
class Snap:
    """드래그 이동/리사이즈 시 맞춰 붙일 그리드 크기 (px)."""

    grid: int = 10

    @classmethod
    def from_dict(cls, raw):
        raw = _as_dict(raw)
        return cls(grid=_norm_int(raw.get("grid"), 10, lo=1, hi=200))

    def to_dict(self):
        return {"grid": self.grid}


# --------------------------------------------------------------------------- 최상위


@dataclass
class Config:
    version: int = CONFIG_VERSION
    geometry: Geometry = field(default_factory=Geometry)
    opacity: float = 1.0
    mode: str = "edit"
    visible: bool = True
    pattern: Pattern = field(default_factory=Pattern)
    color_key: ColorKey = field(default_factory=ColorKey)
    hotkeys: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    topmost_reassert_ms: int = 1000
    snap: Snap = field(default_factory=Snap)

    @classmethod
    def from_dict(cls, raw):
        """관대한 파싱. 모르는 키는 무시하고 빠진 값은 기본값으로 채운다.

        사용자가 config.json 을 직접 손으로 고치는 걸 전제로 하므로, 값 하나가
        이상해도 전체가 기본값으로 리셋되지 않게 필드 단위로 방어한다.
        """
        raw = _as_dict(raw)

        hotkeys = dict(DEFAULT_HOTKEYS)
        for key, value in _as_dict(raw.get("hotkeys")).items():
            if key in DEFAULT_HOTKEYS and isinstance(value, str) and value.strip():
                hotkeys[key] = value.strip()

        return cls(
            version=_norm_int(raw.get("version"), CONFIG_VERSION),
            geometry=Geometry.from_dict(raw.get("geometry")),
            opacity=_norm_float(raw.get("opacity"), 1.0, lo=0.1, hi=1.0),
            mode=_norm_choice(raw.get("mode"), VALID_MODES, "edit"),
            visible=_norm_bool(raw.get("visible"), True),
            pattern=Pattern.from_dict(raw.get("pattern")),
            color_key=ColorKey.from_dict(raw.get("color_key")),
            hotkeys=hotkeys,
            topmost_reassert_ms=_norm_int(
                raw.get("topmost_reassert_ms"), 1000, lo=0, hi=60000
            ),
            snap=Snap.from_dict(raw.get("snap")),
        )

    def to_dict(self):
        return {
            "version": CONFIG_VERSION,
            "geometry": self.geometry.to_dict(),
            "opacity": round(float(self.opacity), 3),
            "mode": self.mode,
            "visible": bool(self.visible),
            "pattern": self.pattern.to_dict(),
            "color_key": self.color_key.to_dict(),
            "hotkeys": dict(self.hotkeys),
            "topmost_reassert_ms": int(self.topmost_reassert_ms),
            "snap": self.snap.to_dict(),
        }

    def copy(self, **changes):
        """일부 필드만 바꾼 사본 (중첩 구조는 얕은 공유)."""
        return replace(self, **changes)

    # -- 영속화 -------------------------------------------------------------

    @classmethod
    def load(cls, path=None):
        """설정을 읽는다. 파일이 없거나 깨졌으면 기본값을 돌려준다."""
        path = path or config_path()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return cls.from_dict(json.load(fh))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            # 깨진 파일은 덮어쓰기 전에 .bak 으로 남겨 사용자가 원인을 볼 수 있게 한다.
            try:
                os.replace(path, path + ".bak")
            except OSError:
                pass
            return cls()

    def save(self, path=None):
        """원자적으로 저장한다. 예외는 호출자가 판단하도록 그대로 올린다."""
        path = path or config_path()
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)

        fd, tmp = tempfile.mkstemp(prefix=".cfg-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
