# -*- coding: utf-8 -*-
"""
水质智慧运维平台 — 前端界面中文合规检查
=================================================
每次改完前端 UI 后运行，确保：
  A. 无「新增英文可见文本」（JSX 文本节点 / 展示型属性）
  B. 枚举字段（status/level/metric/type/result/category/...）
     不得裸渲染原始英文值，必须走 src/services/constants.js 的集中映射
  C. Modal / Popconfirm / Modal.confirm 必须显式设置 okText / cancelText，
     避免 Ant Design 默认显示英文 OK / Cancel

用法：
  python check_ui_zh.py
退出码：
  发现 A 类或 C 类问题返回 1，否则 0（B 类为提示，不阻断）

排除项（非界面文案，允许保留英文）：
  - 网址/URL（http/https/www.）
  - 设备 serial / 编码样例（含数字+字母的长串，如 62313350-01HYDR）
  - 约定俗成的外来词（SUV / SLA 等，按项目惯例放行）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'react-vite', 'src')
EXTS = ('.jsx', '.js', '.tsx', '.ts')

# 展示型属性
ATTR_RE = re.compile(r"""(placeholder|tooltip|aria-label|alt|title|label)=("|')([^"']*)("|')""")
# JSX 文本节点：以字母开头，仅含字母/空格/标点
TEXT_RE = re.compile(r">([A-Za-z][A-Za-z ,.'\-/$%&()]*?)<")
# 裸枚举渲染： >{obj.field}< （未走映射）
ENUM_FIELDS = r'(level|status|metric|type|result|category|source|period|frequency|scope|severity|rule_type|flow_type|flow_status|role)'
RAW_ENUM_RE = re.compile(r">\s*\{[a-zA-Z_][a-zA-Z0-9_]*\." + ENUM_FIELDS + r"\}\s*<")

# Modal / Popconfirm 未设置中文按钮（跳过自定义 footer / footer=null）
MODAL_START_RE = re.compile(r"<(Modal|Popconfirm)\b")
MODAL_CONFIRM_RE = re.compile(r"Modal\.confirm\s*\(")
FOOTER_CUSTOM_RE = re.compile(r"footer\s*=\s*\{")
FOOTER_NULL_RE = re.compile(r"footer\s*=\s*\{null\}")

# 放行：网址 / 编码样例（含数字长串）/ 外来词
URL_RE = re.compile(r"https?://|www\.", re.I)
CODE_RE = re.compile(r"^[A-Za-z0-9\-./_ ]{6,}$")
LOAN = {'suv', 'sla', 'spc', 'url', 'id', 'api', 'gps', 'ph', 'z', 'cod', 'do', 'tp', 'tn', 'hy', 'dr'}
# 含中文字 → 视为「中文为主」（纯 url/编码样例另行放行）
CJK_RE = re.compile(r"[一-鿿]")

part_a = []   # (file, line, kind, text)
part_b = []   # (file, line, text)
part_c = []   # (file, line, snippet) 未设中文按钮的 Modal/Popconfirm


def is_english_word(s):
    return re.search(r"[A-Za-z]{2,}", s) is not None


def embedded_english(s):
    """中文句里夹带的英文单词（≥4 字母，且非约定外来符）。"""
    if not CJK_RE.search(s):
        return False
    words = re.findall(r"[A-Za-z]{4,}", s)
    if not words:
        return False
    return not all(w.lower() in LOAN for w in words)


def allowed(s):
    if URL_RE.search(s):
        return True
    if CODE_RE.match(s) and re.search(r"[0-9]", s):
        return True
    if CJK_RE.search(s):          # 含中文 → 中文为主
        # 但若夹带≥4字母英文单词（如 "SPC Z-score"），仍判为需处理
        if embedded_english(s):
            return False
        return True
    toks = re.findall(r"[A-Za-z]+", s)
    if toks and all(t.lower() in LOAN for t in toks):
        return True
    return False


def is_modal_confirm_button_chinese(lines, idx):
    """检查 idx 行开始的 Modal/Popconfirm/Modal.confirm 是否设置了 okText/cancelText
    或是否使用自定义 footer（footer=[...] / footer={null}）。
    返回 (True,) 表示已合规；返回 (False, snippet) 表示不合规。
    """
    block = '\n'.join(lines[idx:idx+80])
    if FOOTER_NULL_RE.search(block):
        return True, None
    if FOOTER_CUSTOM_RE.search(block):
        return True, None
    if 'okText' in block and 'cancelText' in block:
        return True, None
    return False, lines[idx].strip()


for cur, _, files in os.walk(SRC):
    for fn in files:
        if not fn.endswith(EXTS):
            continue
        p = os.path.join(cur, fn)
        try:
            lines = open(p, encoding='utf-8').read().split('\n')
        except Exception:
            continue
        rel = os.path.relpath(p, ROOT)
        for i, line in enumerate(lines, 1):
            for m in TEXT_RE.finditer(line):
                txt = m.group(1).strip()
                if is_english_word(txt) and not allowed(txt):
                    part_a.append((rel, i, 'TEXT', txt))
            for m in ATTR_RE.finditer(line):
                val = m.group(3)
                if is_english_word(val) and not allowed(val):
                    part_a.append((rel, i, m.group(1), val))
            for m in RAW_ENUM_RE.finditer(line):
                part_b.append((rel, i, m.group(0)))
            # C 类：Modal / Popconfirm / Modal.confirm 默认英文按钮
            if MODAL_START_RE.search(line) or MODAL_CONFIRM_RE.search(line):
                ok, snippet = is_modal_confirm_button_chinese(lines, i - 1)
                if not ok:
                    part_c.append((rel, i, snippet))

print("=" * 60)
print(" 水质平台 UI 中文合规检查")
print("=" * 60)

if part_a:
    print("\n【A 类 · 可见英文文本】须改为中文：")
    for rel, i, kind, txt in part_a:
        print(f"  L{i:<4} [{kind:8}] {rel}\n          -> {txt}")
else:
    print("\n【A 类】PASS：未发现可见英文文本。")

if part_b:
    print("\n【B 类 · 裸枚举渲染】请确认已走 services/constants 映射：")
    for rel, i, txt in part_b:
        print(f"  L{i:<4} {rel}\n          -> {txt}")
else:
    print("\n【B 类】PASS：无裸枚举渲染。")

if part_c:
    print("\n【C 类 · 默认英文按钮】Modal/Popconfirm 须显式设置 okText / cancelText：")
    for rel, i, snippet in part_c:
        print(f"  L{i:<4} {rel}\n          -> {snippet}")
else:
    print("\n【C 类】PASS：无默认英文按钮。")

print("\n" + "=" * 60)
if part_a or part_c:
    print(f"结果：FAIL（A 类 {len(part_a)} 处；C 类 {len(part_c)} 处）")
    sys.exit(1)
print(f"结果：PASS（A 类 0 处；B 类 {len(part_b)} 处提示；C 类 0 处）")
sys.exit(0)
