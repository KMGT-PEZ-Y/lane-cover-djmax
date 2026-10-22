"""트레이/exe 아이콘을 만든다.

    python tools/make_icon.py

결과물 ``assets/icon.ico`` 는 커밋해 두므로 빌드에 Pillow 가 필요하지 않다.
(빌드 환경에 Pillow 가 없어도 그대로 쓸 수 있게 하기 위함)

모티프: 어두운 라운드 사각형 + 상단의 밝은 커버 바 + 그 아래 레인 세로줄.
16x16 까지 축소되므로 획을 두껍게 잡는다.
"""

import os
import sys

from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]

BG = (16, 20, 30, 255)
BORDER = (0, 190, 230, 255)
BAR = (0, 220, 255, 255)
LANE = (72, 112, 145, 255)

MASTER = 256


def render(size=MASTER):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def px(fraction, minimum=1):
        return max(minimum, int(round(size * fraction)))

    pad = px(0.055)
    radius = px(0.15)
    stroke = px(0.035)

    d.rounded_rectangle(
        [pad, pad, size - 1 - pad, size - 1 - pad],
        radius=radius, fill=BG, outline=BORDER, width=stroke,
    )

    inset = pad + px(0.075)
    bar_top = pad + px(0.07)
    bar_height = px(0.19, 2)
    d.rounded_rectangle(
        [inset, bar_top, size - 1 - inset, bar_top + bar_height],
        radius=px(0.045), fill=BAR,
    )

    # 커버 바 아래로 내려가는 레인 세로줄
    lane_top = bar_top + bar_height + px(0.07)
    lane_bottom = size - 1 - pad - px(0.10)
    if lane_bottom > lane_top:
        lanes = 5
        span = (size - 1 - inset) - inset
        gap = span / lanes
        width = px(0.03, 1)
        for index in range(lanes):
            cx = inset + gap * (index + 0.5)
            d.line([cx, lane_top, cx, lane_bottom], fill=LANE, width=width)

    return img


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(root, "assets")
    os.makedirs(assets, exist_ok=True)

    master = render(MASTER)
    ico_path = os.path.join(assets, "icon.ico")
    master.save(ico_path, format="ICO", sizes=[(s, s) for s in SIZES])

    preview_path = os.path.join(assets, "icon-preview.png")
    master.save(preview_path, format="PNG")

    print("생성: %s (%d bytes)" % (ico_path, os.path.getsize(ico_path)))
    print("미리보기: %s" % preview_path)

    with Image.open(ico_path) as check:
        print("ICO 포함 크기: %s" % sorted(check.ico.sizes()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
