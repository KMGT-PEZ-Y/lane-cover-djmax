"""파일 로깅.

``--noconsole`` 로 빌드하면 ``sys.stdout``/``sys.stderr`` 가 ``None`` 이 되고, 접근하면
``AttributeError`` 가 난다. ctypes 콜백이나 예외 처리 경로에서 그런 접근이 터지면
원래 오류를 가리므로, 진입점에서 곧바로 devnull 로 대체하고 진단은 파일에 남긴다.
"""

import logging
import logging.handlers
import os
import sys

from . import config

_LOGGER_NAME = "lanecover"
_LOG_FILE = "lanecover.log"
_configured = False


def ensure_std_streams():
    """``--noconsole`` 에서 None 이 된 표준 스트림을 devnull 로 대체한다."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass


def log_path():
    return os.path.join(config.config_dir(), _LOG_FILE)


def setup(level=logging.INFO):
    """루트 로거를 파일 핸들러로 초기화한다 (여러 번 불러도 한 번만)."""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    try:
        # 플레이 중 계속 켜두는 프로그램이라 무한정 커지지 않게 회전시킨다.
        handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=256 * 1024, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    except OSError:
        # 로그를 못 써도 프로그램은 돌아가야 한다.
        logger.addHandler(logging.NullHandler())
    _configured = True
    return logger


def get_logger(name=None):
    """모듈별 자식 로거."""
    if name:
        return logging.getLogger(_LOGGER_NAME).getChild(name)
    return logging.getLogger(_LOGGER_NAME)
