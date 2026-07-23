# -*- coding: utf-8 -*-
# 生成微信 TabBar 双态线性图标（灰=普通 / 蓝=选中）
# 风格：圆角描边、现代简约（参照素材的移动端线性图标风格）
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), 'images')
os.makedirs(OUT, exist_ok=True)

NORMAL = (154, 160, 166, 255)   # #9AA0A6 微信未选中灰
SELECT = (43, 108, 255, 255)    # #2B6CFF 品牌蓝

S = 162  # 2x 画布


def canvas():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def home(color):
    img, d = canvas()
    w = 10
    # 屋顶
    d.line([(38, 78), (81, 36), (124, 78)], fill=color, width=w, joint='curve')
    # 墙体
    d.line([(38, 78), (38, 130)], fill=color, width=w, joint='curve')
    d.line([(124, 78), (124, 130)], fill=color, width=w, joint='curve')
    d.line([(38, 130), (124, 130)], fill=color, width=w, joint='curve')
    # 门
    d.line([(73, 104), (73, 130), (89, 130), (89, 104)], fill=color, width=8, joint='curve')
    return img


def inspection(color):
    img, d = canvas()
    w = 10
    # 放大镜：圆 + 手柄
    d.ellipse([(40, 40), (92, 92)], outline=color, width=w)
    d.line([(84, 84), (120, 120)], fill=color, width=w + 2, joint='curve')
    return img


def workorder(color):
    img, d = canvas()
    w = 10
    # 文档：圆角矩形 + 三条横线
    d.rounded_rectangle([(44, 38), (118, 132)], radius=14, outline=color, width=w)
    for y in (66, 88, 110):
        d.line([(62, y), (100, y)], fill=color, width=7, joint='curve')
    return img


def alert(color):
    img, d = canvas()
    w = 10
    # 铃铛：对称钟形轮廓
    pts = [(81, 34), (63, 52), (57, 98), (63, 116),
           (99, 116), (105, 98), (99, 52)]
    d.line(pts + [pts[0]], fill=color, width=w, joint='curve')
    # 底部铃舌横档
    d.line([(60, 116), (102, 116)], fill=color, width=w, joint='curve')
    # 铃舌
    d.ellipse([(75, 122), (87, 134)], outline=color, width=6)
    return img


def mine(color):
    img, d = canvas()
    w = 10
    # 头像：圆 + 肩弧
    d.ellipse([(59, 34), (103, 78)], outline=color, width=w)
    d.arc([(38, 104), (124, 196)], start=180, end=360, fill=color, width=w)
    return img


ICONS = {
    'tab-home': home,
    'tab-inspection': inspection,
    'tab-workorder': workorder,
    'tab-alert': alert,
    'tab-mine': mine,
}


def save(img, name):
    img = img.resize((81, 81), Image.LANCZOS)
    path = os.path.join(OUT, name)
    img.save(path, 'PNG')
    print('saved', path)


for base, fn in ICONS.items():
    save(fn(NORMAL), base + '.png')
    save(fn(SELECT), base + '-on.png')

print('DONE')
