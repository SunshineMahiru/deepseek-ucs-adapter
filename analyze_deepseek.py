#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek conversations.json 结构分析脚本（只读，不修改任何文件）。"""
import json
import sys
from collections import Counter, defaultdict

PATH = r"G:/Tenshisama Code/self-thinking/conversations.json"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

print(f"顶层类型: {type(data).__name__}")
if isinstance(data, dict):
    print("顶层键:", list(data.keys())[:20])
    # 可能被包了一层
    for k in data.keys():
        v = data[k]
        if isinstance(v, list):
            data = v
            print(f"  从键 '{k}' 解包出 {len(v)} 个会话")
            break
print(f"总会话数: {len(data)}")

# 会话级字段
conv_keys = Counter()
node_keys = Counter()
msg_keys = Counter()
frag_types = Counter()
frag_keys_by_type = defaultdict(Counter)
multi_frag = 0
think_by_model = Counter()
frag_by_model = Counter()
msg_models = Counter()
msg_with_fragments = 0
msg_without_fragments = 0
msg_null = 0
nodes_total = 0
parent_field = Counter()  # 节点 parent 字段样式
id_sample = None

THINK_SAMPLE = []
SEARCH_SAMPLE = []
UNKNOWN_SAMPLE = []
multi_frag_samples = []

for ci, conv in enumerate(data):
    if not isinstance(conv, dict):
        print(f"会话[{ci}] 非 dict, 类型 {type(conv).__name__}")
        continue
    conv_keys.update(conv.keys())
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict):
        print(f"会话[{ci}] 缺少 mapping 或非 dict")
        continue
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        nodes_total += 1
        node_keys.update(node.keys())
        if id_sample is None and node_id != "root":
            id_sample = node_id
        # 检查 parent 字段
        if "parent" in node:
            parent_field["node.parent"] += 1
        msg = node.get("message")
        if msg is None:
            msg_null += 1
            continue
        if not isinstance(msg, dict):
            continue
        msg_keys.update(msg.keys())
        # 检查 parent_id
        if "parent_id" in msg:
            parent_field["message.parent_id"] += 1
        model = msg.get("model", "?")
        msg_models[model] += 1
        frags = msg.get("fragments")
        if frags is None:
            msg_without_fragments += 1
            continue
        if not isinstance(frags, list):
            continue
        if len(frags) == 0:
            msg_without_fragments += 1
            continue
        msg_with_fragments += 1
        if len(frags) > 1:
            multi_frag += 1
            if len(multi_frag_samples) < 5:
                multi_frag_samples.append((conv.get("title"), [f.get("type") for f in frags]))
        for frag in frags:
            if not isinstance(frag, dict):
                continue
            ft = frag.get("type", "<missing>")
            frag_types[ft] += 1
            frag_keys_by_type[ft].update(frag.keys())
            frag_by_model[(model, ft)] += 1
            if ft == "THINK":
                think_by_model[model] += 1
                if len(THINK_SAMPLE) < 3:
                    THINK_SAMPLE.append({k: (str(v)[:80]) for k, v in frag.items()})
            elif ft == "SEARCH" and len(SEARCH_SAMPLE) < 2:
                SEARCH_SAMPLE.append({k: (str(v)[:200]) for k, v in frag.items()})
            elif ft not in ("REQUEST", "RESPONSE", "THINK", "SEARCH") and len(UNKNOWN_SAMPLE) < 3:
                UNKNOWN_SAMPLE.append({k: (str(v)[:120]) for k, v in frag.items()})

print("\n" + "=" * 60)
print("【调研2-1】fragment.type 分布")
print("=" * 60)
for t, c in frag_types.most_common():
    print(f"  {t:12s} : {c}")

print("\n【调研2-2】各 fragment.type 的字段名")
for t in sorted(frag_types):
    print(f"  {t}: {sorted(frag_keys_by_type[t].keys())}")

print("\n【调研2-3】消息模型分布 (message.model)")
for m, c in msg_models.most_common():
    print(f"  {m:20s} : {c}")

print("\n【调研2-4】THINK fragment 按模型分布")
for m, c in think_by_model.most_common():
    print(f"  {m:20s} : {c}")

print("\n【调研2-5】fragment.type × model 交叉表 (top 20)")
for (m, ft), c in frag_by_model.most_common(20):
    print(f"  {m:22s} {ft:10s} : {c}")

print("\n【调研2-6】结构统计")
print(f"  总节点数: {nodes_total}")
print(f"  message == null 节点数: {msg_null}")
print(f"  含 fragments 的消息数: {msg_with_fragments}")
print(f"  无/空 fragments 的消息数: {msg_without_fragments}")
print(f"  多 fragments 消息数: {multi_frag}")

print("\n【调研2-7】节点字段 (node_keys)")
print("  ", dict(node_keys))
print("  parent 字段位置:", dict(parent_field))
print("  典型非root节点ID样例:", id_sample)

print("\n【调研2-8】会话级字段 (conv_keys)")
print("  ", dict(conv_keys))

print("\n【调研2-9】消息级字段 (msg_keys)")
print("  ", dict(msg_keys))

print("\n【样例】THINK fragment:")
for s in THINK_SAMPLE:
    print("  ", s)
print("\n【样例】SEARCH fragment:")
for s in SEARCH_SAMPLE:
    print("  ", s)
print("\n【样例】未知类型 fragment:")
for s in UNKNOWN_SAMPLE:
    print("  ", s)
print("\n【样例】多fragments消息 (title, types):")
for t, types in multi_frag_samples:
    print(f"  [{t}] -> {types}")
