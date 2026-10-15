#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文修复行为语言学调研（只读，不改任何文件）。

调研一/二：在 conversations_chatgpt.json 上提取 user 消息统计。
调研三：在原始 conversations.json 上提取 REQUEST 文本抽样（避免转换改变文本）。

注意：本脚本是一次性调研脚本，其结论已固化在 README.md 与 调研与方案设计.md。
数据文件已于 2026-10-15 目录清理时迁移：
  - conversations_chatgpt.json -> G:/Tenshisama Code/_my_deepseek_data/
  - 原始 DeepSeek 导出 conversations.json 仍在 G:/Tenshisama Code/self-thinking/
重跑前请核对下方 CONV / ORIG 常量是否指向正确位置。
"""
import json
import re
import random

CONV = r"G:/Tenshisama Code/_my_deepseek_data/conversations_chatgpt.json"
ORIG = r"G:/Tenshisama Code/self-thinking/conversations.json"

# ---------- 提取 user 消息（转换后文件，复刻 parser 逻辑） ----------
def load_user_texts_converted(path):
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for conv in data:
        for node in conv.get("mapping", {}).values():
            msg = node.get("message")
            if not msg:
                continue
            if msg.get("author", {}).get("role") != "user":
                continue
            parts = msg.get("content", {}).get("parts", [])
            text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
            if text:
                out.append(text)
    return out

# ---------- 提取 user 消息（原始文件，REQUEST 片段） ----------
def load_user_texts_original(path):
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for conv in data:
        for node in conv.get("mapping", {}).values():
            msg = node.get("message")
            if not msg:
                continue
            for frag in (msg.get("fragments") or []):
                if isinstance(frag, dict) and frag.get("type") == "REQUEST" and frag.get("content"):
                    out.append(frag["content"])
    return out

user_conv = load_user_texts_converted(CONV)
user_orig = load_user_texts_original(ORIG)
print(f"转换后 user 消息数: {len(user_conv)}")
print(f"原始 user 消息数(REQUEST): {len(user_orig)}")
print()

# =====================================================================
# 调研一：结构绑定纠正标记
# =====================================================================
print("=" * 72)
print("【调研一】结构绑定纠正标记")
print("=" * 72)

markers = [
    ("并不", r"并不"),
    ("并不是", r"并不是"),
    ("并没有", r"并没有"),
    ("并不对", r"并不对"),
    ("又不是", r"又不是"),
    ("又没有", r"又没有"),
    ("又不对", r"又不对"),
    ("我的意思是", r"我的意思是"),
    ("我是说", r"我是说"),
    ("我说的是", r"我说的是"),
    ("不是这个意思", r"不是这个意思"),
    ("你理解错了", r"你理解错了"),
    ("你搞错了", r"你搞错了"),
    ("你误解", r"你误解"),
    ("就是", r"就是"),
]

for label, pat in markers:
    rx = re.compile(pat)
    matched = [t for t in user_conv if rx.search(t)]
    uniq = list(dict.fromkeys(matched))
    print(f"\n--- [{label}]  匹配={len(matched)}  去重={len(uniq)} ---")
    for t in uniq[:15]:
        disp = t if len(t) <= 90 else t[:90] + "…"
        print("   ·", disp.replace("\n", " ⏎ "))
    if len(uniq) > 15:
        print(f"   …(仅显示前15，共{len(uniq)}条)")

# =====================================================================
# 调研二：句式分布 + 关键词频率
# =====================================================================
print()
print("=" * 72)
print("【调研二】句式分布 + 关键词频率")
print("=" * 72)

imperative = 0
question = 0
statement = 0
start_ni = 0
imperative_starts = ("帮我", "给我", "请", "把", "写", "整理", "分析", "补充", "删除", "去掉",
                     "修改", "改成", "生成", "解释", "说明", "回答", "列出", "总结", "对比",
                     "翻译", "计算", "设计", "实现", "制定", "创建", "新建", "给出", "输出",
                     "重新", "不要", "别", "继续", "检查", "优化", "完善", "提取", "转换",
                     "合并", "拆分", "画", "做", "补全", "根据", "基于", "结合", "从", "用")
for t in user_conv:
    if t.startswith("你"):
        start_ni += 1
    if ("？" in t) or ("?" in t) or ("吗" in t) or ("呢" in t):
        question += 1
    elif t.startswith(imperative_starts):
        imperative += 1
    else:
        statement += 1

n = len(user_conv)
print(f"祈使句: {imperative} ({imperative/n:.1%})")
print(f"疑问句: {question} ({question/n:.1%})")
print(f"陈述句: {statement} ({statement/n:.1%})")
print(f"以「你」开头: {start_ni} ({start_ni/n:.1%})")

kw_groups = {
    "重述类": ["重新", "再", "换个", "改一下", "调整", "修改", "改成"],
    "否定类": ["不对", "不是", "错了", "不行", "不好"],
    "补充类": ["还要", "另外", "补充", "加上", "还有"],
    "格式类": ["格式", "排版", "换行", "markdown", "表格"],
}
print()
print("关键词频率（裸词，不限定位置；同一消息内多次命中计多次）:")
for grp, kws in kw_groups.items():
    parts = []
    for kw in kws:
        c = sum(t.count(kw) for t in user_conv)
        parts.append(f"{kw}={c}")
    print(f"  {grp}: " + "  ".join(parts))

# =====================================================================
# 调研三：隐形修复信号抽样
# =====================================================================
print()
print("=" * 72)
print("【调研三】隐形修复信号抽样（原始语料，30 条）")
print("=" * 72)

exclude = re.compile(r"不对|错了|重新|删除|去掉|不要|别用|修改|改成|精简|简洁|详细|具体|太长|太短|太啰嗦|缩短|展开|移除|删掉|不是这样")
turn_words = re.compile(r"但是|不过|其实|应该是|我觉得|可是|然而|却|反而|本来|应该|还是|就是")

candidates = []
for t in user_orig:
    if len(t) < 20:
        continue
    if exclude.search(t):
        continue
    if turn_words.search(t):
        candidates.append(t)

print(f"候选消息数（≥20字 & 无显式纠正词 & 含转折词）: {len(candidates)}")
random.seed(42)
sample = random.sample(candidates, min(30, len(candidates)))
for i, t in enumerate(sample, 1):
    disp = t if len(t) <= 150 else t[:150] + "…"
    print(f"\n[{i}] {disp.replace(chr(10), ' ⏎ ')}")
