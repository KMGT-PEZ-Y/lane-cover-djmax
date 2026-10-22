"""커버에 입힐 이미지 로드와 맞춤 방식별 렌더링.

맞춤 방식 5종:

===========  ==========================================
``stretch``  늘이기 — 비율 무시하고 정확히 채움
``fill``     채우고 자르기 — 비율 유지, 넘치는 부분은 잘라냄
``fit``      맞추고 여백 — 비율 유지, 남는 곳은 배경색
``tile``     타일 — 원본 크기로 반복
``center``   원본 크기 가운데 — 축소 없이 중앙 배치
===========  ==========================================

Pillow 가 없으면 tkinter ``PhotoImage`` 로 폴백한다 (PNG/GIF 만, 정수배 확대/축소).
최소 동작은 보장하되 품질이 떨어진다는 사실을 로그로 남긴다.
"""

import os
import time
from collections import OrderedDict

from . import config
from .log import get_logger
from .resource import app_dir

log = get_logger("patterns")

try:
    from PIL import Image, ImageColor, ImageGrab, ImageOps

    HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow 는 requirements 에 포함되어 있다
    Image = ImageColor = ImageGrab = ImageOps = None
    HAS_PIL = False
    log.warning("Pillow 없음 - PhotoImage 폴백으로 동작한다 (PNG/GIF 만, 품질 저하)")

if HAS_PIL:
    try:
        _RES_BEST = Image.Resampling.LANCZOS
        _RES_FAST = Image.Resampling.BILINEAR
    except AttributeError:  # Pillow < 9.1
        _RES_BEST = Image.LANCZOS
        _RES_FAST = Image.BILINEAR

_SOURCE_CACHE_MAX = 4
_RENDER_CACHE_MAX = 16


class PatternError(Exception):
    """이미지를 쓸 수 없을 때."""


def _patterns_dir():
    path = os.path.join(config.config_dir(), "patterns")
    os.makedirs(path, exist_ok=True)
    return path


def _bg_rgba(color):
    if HAS_PIL:
        try:
            return ImageColor.getrgb(color) + (255,)
        except ValueError:
            pass
    return (16, 16, 24, 255)


class PatternRenderer:
    """이미지 로드/합성을 캐시와 함께 수행한다.

    리사이즈 드래그 중에는 저품질로 빠르게 그리고, 드래그가 끝나면 고품질로 한 번 더
    그려 덮어쓴다. 큰 이미지에서도 드래그가 끊기지 않게 하기 위함이다.
    """

    def __init__(self):
        self._sources = OrderedDict()  # (path, mtime) -> PIL.Image
        self._renders = OrderedDict()  # (path, mtime, w, h, fit, bg) -> PhotoImage
        self._fallback_source = None  # Pillow 없을 때의 PhotoImage

    # -- 소스 ---------------------------------------------------------------

    def _load_source(self, path):
        """원본 이미지를 RGBA 로 읽어 캐시한다. 파일이 바뀌면 자동으로 다시 읽는다."""
        try:
            mtime = os.path.getmtime(path)
        except OSError as exc:
            raise PatternError("파일을 찾을 수 없습니다: %s" % path) from exc

        key = (path, mtime)
        cached = self._sources.get(key)
        if cached is not None:
            return cached

        try:
            with Image.open(path) as handle:
                handle.load()
                image = handle.convert("RGBA")
        except (OSError, ValueError) as exc:
            raise PatternError("이미지를 읽을 수 없습니다: %s" % exc) from exc

        self._sources[key] = image
        while len(self._sources) > _SOURCE_CACHE_MAX:
            self._sources.popitem(last=False)
        return image

    def _compose(self, image, width, height, fit, bg, resample):
        """원본을 (width, height) 한 장으로 합성한다. 배경색까지 여기서 채운다."""
        if fit == "stretch":
            return image.resize((width, height), resample)

        if fit == "fill":
            return ImageOps.fit(image, (width, height), method=resample, centering=(0.5, 0.5))

        canvas = Image.new("RGBA", (width, height), bg)

        if fit == "fit":
            scaled = image.copy()
            scaled.thumbnail((width, height), resample)
            canvas.paste(scaled, ((width - scaled.width) // 2, (height - scaled.height) // 2), scaled)

        elif fit == "center":
            placed = image
            if placed.width > width or placed.height > height:
                # 원본 크기를 유지한 채 중앙 부분만 남긴다.
                left = (placed.width - width) // 2
                top = (placed.height - height) // 2
                placed = placed.crop(
                    (left, top, left + min(width, placed.width), top + min(height, placed.height))
                )
            canvas.paste(
                placed, ((width - placed.width) // 2, (height - placed.height) // 2), placed
            )

        elif fit == "tile":
            tile_w, tile_h = image.size
            if tile_w > 0 and tile_h > 0:
                for y in range(0, height, tile_h):
                    for x in range(0, width, tile_w):
                        canvas.paste(image, (x, y), image)

        else:  # 알 수 없는 값 - 늘이기로 처리
            return image.resize((width, height), resample)

        return canvas

    # -- 렌더 ---------------------------------------------------------------

    def render(self, path, width, height, fit, bg_color, high_quality=True):
        """``tk.PhotoImage`` 를 돌려준다. ``path`` 가 없으면 None.

        돌려준 객체는 캐시가 붙잡고 있으므로 GC 로 사라지지 않는다.
        """
        if not path or width <= 0 or height <= 0:
            return None

        if not HAS_PIL:
            return self._render_fallback(path, width, height)

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None

        key = (path, mtime, width, height, fit, bg_color)
        cached = self._renders.get(key)
        if cached is not None:
            self._renders.move_to_end(key)
            return cached

        import tkinter as tk

        try:
            source = self._load_source(path)
        except PatternError as exc:
            log.warning("%s", exc)
            return None

        resample = _RES_BEST if high_quality else _RES_FAST
        composed = self._compose(source, width, height, fit, _bg_rgba(bg_color), resample)
        try:
            photo = _to_photoimage(composed)
        except Exception:
            log.exception("PhotoImage 변환 실패")
            return None

        self._renders[key] = photo
        while len(self._renders) > _RENDER_CACHE_MAX:
            self._renders.popitem(last=False)
        return photo

    def _render_fallback(self, path, width, height):
        """Pillow 없는 환경용. PNG/GIF 를 정수배로만 확대/축소한다."""
        import tkinter as tk

        try:
            if self._fallback_source is None or self._fallback_source[0] != path:
                self._fallback_source = (path, tk.PhotoImage(file=path))
        except Exception as exc:
            log.warning("PhotoImage 로드 실패(%s): %s", path, exc)
            return None

        photo = self._fallback_source[1]
        factor = 1
        while photo.width() * factor * 2 <= width and photo.height() * factor * 2 <= height:
            factor *= 2
        scaled = photo.zoom(factor) if factor > 1 else photo
        while scaled.width() > width * 2 and scaled.height() > height * 2:
            scaled = scaled.subsample(2)
        return scaled

    def invalidate(self):
        """캐시를 비운다 (설정 초기화 등)."""
        self._sources.clear()
        self._renders.clear()
        self._fallback_source = None


def _to_photoimage(pil_image):
    """PIL.Image -> tk.PhotoImage.

    PPM 으로 직렬화해 넘긴다. Pillow 의 ImageTk 를 쓰지 않는 이유는 ImageTk 가
    tkinter 가 만들어진 뒤에 import 되어야 하고, PyInstaller 번들에서 별도 처리가
    필요한 반면 PPM 경로는 의존성이 없기 때문이다.
    """
    import tkinter as tk

    from io import BytesIO

    buf = BytesIO()
    pil_image.convert("RGB").save(buf, format="PPM")
    return tk.PhotoImage(data=buf.getvalue())


# --------------------------------------------------------------------------- 유틸


def is_image_file(path):
    from .native import IMAGE_EXTS

    return path.lower().endswith(IMAGE_EXTS)


def save_image(image, prefix="pattern"):
    """PIL 이미지를 패턴 폴더에 PNG 로 저장하고 경로를 돌려준다.

    클립보드에서 붙여넣은 비트맵처럼 원본 파일이 없는 경우에 쓴다. 설정에는 항상
    파일 경로만 들어가므로 이후 실행에서도 그대로 복원된다.
    """
    if not HAS_PIL:
        raise PatternError("Pillow 가 없어 이미지를 저장할 수 없습니다")
    name = "%s-%s.png" % (prefix, time.strftime("%Y%m%d-%H%M%S"))
    path = os.path.join(_patterns_dir(), name)
    image.convert("RGBA").save(path, format="PNG")
    log.info("패턴 저장: %s", path)
    return path


def grab_clipboard():
    """클립보드에서 이미지를 가져온다.

    비트맵이면 PIL 이미지를, 파일 목록이면 그중 첫 이미지 파일 경로를 돌려준다.
    없으면 None.
    """
    if not HAS_PIL:
        return None
    try:
        data = ImageGrab.grabclipboard()
    except Exception:
        log.exception("클립보드 읽기 실패")
        return None

    if isinstance(data, Image.Image):
        return data
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, str) and is_image_file(entry) and os.path.isfile(entry):
                return entry
    return None


def describe(path):
    """설정 창에 보여줄 짧은 설명. 실패해도 예외를 올리지 않는다."""
    if not path:
        return "없음 (단색 배경)"
    if not os.path.isfile(path):
        return "파일 없음: %s" % path
    if HAS_PIL:
        try:
            with Image.open(path) as handle:
                return "%s  (%dx%d)" % (os.path.basename(path), handle.width, handle.height)
        except (OSError, ValueError):
            pass
    return os.path.basename(path)
