"""로고 타입 + 브랜드 분위기 샘플 이미지 생성 (PIL)
실행: python generate_logo_samples.py
"""
import os
import math
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.join(os.path.dirname(__file__), 'static', 'img', 'logo_samples')
os.makedirs(OUT_DIR, exist_ok=True)

W, H = 300, 200


def save(img, name):
    img.save(os.path.join(OUT_DIR, name))
    print(f'  saved {name}')


def base(bg):
    img = Image.new('RGB', (W, H), bg)
    return img, ImageDraw.Draw(img)


# ─────────────────────────────────────────────
# LOGO TYPES
# ─────────────────────────────────────────────

def type_wordmark():
    img, d = base('#FFFFFF')
    # 회사명처럼 보이는 가로 텍스트 3줄 (선으로 표현)
    for i, (y, w, thick) in enumerate([(70, 220, 22), (100, 160, 14), (126, 100, 9)]):
        x = (W - w) // 2
        d.rounded_rectangle([x, y, x + w, y + thick], radius=4, fill='#1a1a2e')
    d.rectangle([0, 0, W-1, H-1], outline='#e0e0e0', width=1)
    save(img, 'type_wordmark.png')


def type_lettermark():
    img, d = base('#FFFFFF')
    # 원 안에 굵은 이니셜 한 글자
    cx, cy, r = W//2, H//2, 60
    d.ellipse([cx-r, cy-r, cx+r, cy+r], fill='#1a1a2e')
    # 알파벳 M 모양 (선으로)
    pts = [(cx-28, cy+22), (cx-28, cy-22), (cx, cy+5), (cx+28, cy-22), (cx+28, cy+22)]
    d.line(pts, fill='#FFFFFF', width=8)
    d.rectangle([0, 0, W-1, H-1], outline='#e0e0e0', width=1)
    save(img, 'type_lettermark.png')


def type_emblem():
    img, d = base('#FFFFFF')
    cx, cy = W//2, H//2
    # 외곽 배지 원
    d.ellipse([cx-70, cy-70, cx+70, cy+70], outline='#1a1a2e', width=5)
    d.ellipse([cx-58, cy-58, cx+58, cy+58], outline='#1a1a2e', width=2)
    # 안쪽 별 모양
    pts = []
    for i in range(10):
        angle = math.radians(i * 36 - 90)
        r = 32 if i % 2 == 0 else 18
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    d.polygon(pts, fill='#1a1a2e')
    d.rectangle([0, 0, W-1, H-1], outline='#e0e0e0', width=1)
    save(img, 'type_emblem.png')


def type_combination():
    img, d = base('#FFFFFF')
    # 왼쪽 아이콘 사각형
    icon_x, icon_y, icon_s = 55, H//2 - 35, 70
    d.rounded_rectangle([icon_x, icon_y, icon_x+icon_s, icon_y+icon_s], radius=10, fill='#e8355a')
    # 아이콘 안 심플 도형
    ix, iy = icon_x + icon_s//2, icon_y + icon_s//2
    d.ellipse([ix-16, iy-16, ix+16, iy+16], fill='#FFFFFF')
    d.ellipse([ix-8, iy-8, ix+8, iy+8], fill='#e8355a')
    # 오른쪽 텍스트 바
    tx = icon_x + icon_s + 18
    for y, w in [(H//2 - 20, 110), (H//2 + 2, 80), (H//2 + 20, 60)]:
        d.rounded_rectangle([tx, y, tx+w, y+13], radius=3, fill='#1a1a2e')
    d.rectangle([0, 0, W-1, H-1], outline='#e0e0e0', width=1)
    save(img, 'type_combination.png')


def type_mascot():
    img, d = base('#FFF9F0')
    cx, cy = W//2, H//2
    # 몸통
    d.ellipse([cx-30, cy+10, cx+30, cy+60], fill='#FFB347')
    # 얼굴
    face_r = 42
    d.ellipse([cx-face_r, cy-face_r-10, cx+face_r, cy+face_r-10], fill='#FFD580', outline='#E8A020', width=3)
    # 눈
    for ex in [cx-14, cx+14]:
        d.ellipse([ex-7, cy-22, ex+7, cy-8], fill='#1a1a2e')
        d.ellipse([ex-3, cy-21, ex, cy-17], fill='#FFFFFF')
    # 미소
    d.arc([cx-16, cy-4, cx+16, cy+14], start=10, end=170, fill='#1a1a2e', width=4)
    # 귀
    for ex, sign in [(cx-face_r+4, -1), (cx+face_r-4, 1)]:
        d.ellipse([ex-12, cy-face_r-18, ex+12, cy-face_r+6], fill='#FFD580', outline='#E8A020', width=2)
    d.rectangle([0, 0, W-1, H-1], outline='#e0e0e0', width=1)
    save(img, 'type_mascot.png')


# ─────────────────────────────────────────────
# BRAND VIBES
# ─────────────────────────────────────────────

def vibe_modern_minimal():
    img, d = base('#F8F8F8')
    cx = W//2
    # 얇은 수평선 + 작은 원 포인트
    d.line([(50, H//2), (W-50, H//2)], fill='#111111', width=2)
    d.ellipse([cx-6, H//2-6, cx+6, H//2+6], fill='#111111')
    # 모서리 작은 사각형
    sq = 18
    d.rectangle([30, 30, 30+sq, 30+sq], fill='#111111')
    d.rectangle([W-30-sq, H-30-sq, W-30, H-30], outline='#111111', width=2)
    d.rectangle([0, 0, W-1, H-1], outline='#dddddd', width=1)
    save(img, 'vibe_modern_minimal.png')


def vibe_vintage_classic():
    img, d = base('#F5ECD7')
    # 이중 테두리
    d.rectangle([12, 12, W-13, H-13], outline='#5C3A1E', width=3)
    d.rectangle([20, 20, W-21, H-21], outline='#5C3A1E', width=1)
    # 중앙 메달리온 (원형)
    cx, cy = W//2, H//2
    d.ellipse([cx-45, cy-45, cx+45, cy+45], outline='#8B5E3C', width=3)
    d.ellipse([cx-32, cy-32, cx+32, cy+32], fill='#8B5E3C')
    # 안쪽 꽃잎 패턴
    for i in range(8):
        angle = math.radians(i * 45)
        x1 = cx + 14 * math.cos(angle)
        y1 = cy + 14 * math.sin(angle)
        d.ellipse([x1-8, y1-8, x1+8, y1+8], fill='#F5ECD7')
    d.ellipse([cx-6, cy-6, cx+6, cy+6], fill='#F5ECD7')
    # 코너 장식
    for (rx, ry) in [(30, 30), (W-30, 30), (30, H-30), (W-30, H-30)]:
        d.ellipse([rx-5, ry-5, rx+5, ry+5], fill='#5C3A1E')
    d.rectangle([0, 0, W-1, H-1], outline='#c8a97a', width=1)
    save(img, 'vibe_vintage_classic.png')


def vibe_cute_friendly():
    img, d = base('#FFF0F5')
    # 버블 원들
    bubbles = [
        (80, 90, 50, '#FF8FAB'),
        (160, 110, 40, '#FFB347'),
        (230, 85, 45, '#A8D8EA'),
        (130, 155, 30, '#C9B1FF'),
    ]
    for bx, by, br, col in bubbles:
        d.ellipse([bx-br, by-br, bx+br, by+br], fill=col)
    # 별 모양 포인트
    for sx, sy in [(50, 50), (260, 150), (60, 160), (250, 45)]:
        pts = []
        for i in range(10):
            angle = math.radians(i * 36 - 90)
            r = 10 if i % 2 == 0 else 5
            pts.append((sx + r * math.cos(angle), sy + r * math.sin(angle)))
        d.polygon(pts, fill='#FFE44D')
    d.rectangle([0, 0, W-1, H-1], outline='#ffb6c1', width=2)
    save(img, 'vibe_cute_friendly.png')


def vibe_tech_bold():
    img, d = base('#0D1117')
    # 그리드 라인
    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill='#1E2A3A', width=1)
    for y in range(0, H, 40):
        d.line([(0, y), (W, y)], fill='#1E2A3A', width=1)
    # 네온 육각형
    cx, cy = W//2, H//2
    hex_pts = []
    for i in range(6):
        angle = math.radians(i * 60 - 30)
        hex_pts.append((cx + 55 * math.cos(angle), cy + 55 * math.sin(angle)))
    d.polygon(hex_pts, outline='#00D4FF', width=3)
    hex_pts2 = []
    for i in range(6):
        angle = math.radians(i * 60 - 30)
        hex_pts2.append((cx + 35 * math.cos(angle), cy + 35 * math.sin(angle)))
    d.polygon(hex_pts2, fill='#00D4FF')
    # 대각선 액센트
    d.line([(20, H-20), (80, H-60)], fill='#FF4466', width=3)
    d.line([(W-20, 20), (W-80, 60)], fill='#FF4466', width=3)
    d.rectangle([0, 0, W-1, H-1], outline='#00D4FF', width=1)
    save(img, 'vibe_tech_bold.png')


def vibe_natural_warm():
    img, d = base('#F4F0E8')
    # 잎사귀 형태 (베지어 근사)
    cx, cy = W//2, H//2
    leaf_color = '#5A7A4A'
    for angle_offset in [0, 60, 120, 180, 240, 300]:
        angle = math.radians(angle_offset)
        x1 = cx + 15 * math.cos(angle)
        y1 = cy + 15 * math.sin(angle)
        x2 = cx + 55 * math.cos(angle)
        y2 = cy + 55 * math.sin(angle)
        # 타원으로 잎 표현
        lx = (x1 + x2) / 2
        ly = (y1 + y2) / 2
        rot_angle = math.degrees(angle)
        leaf_w, leaf_h = 22, 40
        # 단순 타원으로 표현
        d.ellipse([lx-leaf_h//2, ly-leaf_w//2, lx+leaf_h//2, ly+leaf_w//2],
                  fill=leaf_color, outline='#3D5A2E', width=1)
    # 중앙 원
    d.ellipse([cx-18, cy-18, cx+18, cy+18], fill='#C17F3A')
    d.ellipse([cx-10, cy-10, cx+10, cy+10], fill='#F4F0E8')
    d.rectangle([0, 0, W-1, H-1], outline='#c8b89a', width=1)
    save(img, 'vibe_natural_warm.png')


def vibe_luxury_premium():
    img, d = base('#0A0A0A')
    gold = '#C9A84C'
    cx, cy = W//2, H//2
    # 얇은 금 테두리
    d.rectangle([15, 15, W-16, H-16], outline=gold, width=1)
    d.rectangle([22, 22, W-23, H-23], outline=gold, width=1)
    # 중앙 다이아몬드
    pts = [(cx, cy-45), (cx+30, cy), (cx, cy+45), (cx-30, cy)]
    d.polygon(pts, outline=gold, width=2)
    pts_inner = [(cx, cy-28), (cx+18, cy), (cx, cy+28), (cx-18, cy)]
    d.polygon(pts_inner, fill=gold)
    # 코너 L자 장식
    s = 18
    for (ox, oy, sx, sy) in [(25, 25, 1, 1), (W-25, 25, -1, 1),
                              (25, H-25, 1, -1), (W-25, H-25, -1, -1)]:
        d.line([(ox, oy), (ox+sx*s, oy)], fill=gold, width=2)
        d.line([(ox, oy), (ox, oy+sy*s)], fill=gold, width=2)
    d.rectangle([0, 0, W-1, H-1], outline='#333333', width=1)
    save(img, 'vibe_luxury_premium.png')


if __name__ == '__main__':
    print('=== 로고 타입 샘플 ===')
    type_wordmark()
    type_lettermark()
    type_emblem()
    type_combination()
    type_mascot()

    print('=== 브랜드 분위기 샘플 ===')
    vibe_modern_minimal()
    vibe_vintage_classic()
    vibe_cute_friendly()
    vibe_tech_bold()
    vibe_natural_warm()
    vibe_luxury_premium()

    print('완료!')
