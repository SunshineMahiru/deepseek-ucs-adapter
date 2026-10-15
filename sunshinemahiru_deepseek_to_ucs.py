#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sunshinemahiru_deepseek_to_ucs.py
=================================

将 DeepSeek 网页版导出的 conversations.json 转换为 ucs-parser 可解析的
ChatGPT mapping 格式。

背景
----
ucs-parser（v0.1.0）在 `_extract_messages` 中只读取三个字段：
    message.author.role、message.content.parts[]、message.create_time
DeepSeek 导出虽然也是 mapping 树结构，但消息内容存放在 message.fragments[]
中，导致 ucs-parser 提取不到任何信号，Fidelity 分数极低。

本脚本完成：
    1. fragments[] -> author.role + content.parts[]
    2. 短节点 ID -> 确定性 UUID v4（幂等，重复运行输出完全一致）
    3. 推断 current_node（最后一个叶子节点）
    4. ISO 8601 时间戳 -> Unix 浮点数
    5. 补充 ChatGPT 所需空字段

关键设计说明（务必阅读）：
- root 节点（message == null）**保留**。ucs-parser 的 `_extract_messages`
  有 `if not msg: continue` 保护，不会崩溃；保留它符合 ChatGPT 导出格式。
- 一条消息的 fragments 为空 / 全是无文本工具片段时，该节点会被**跳过**，
  并把它的子节点「旁路重挂」到其父节点上，避免树断链。
- fragment.type 完整类型集（实测 8 种）：REQUEST / RESPONSE / THINK /
  TOOL_OPEN / TOOL_SEARCH / TOOL_FIND / SEARCH / FILE。
  处理规则：REQUEST->user、RESPONSE->assistant、THINK->assistant(加
  [REASONING] 前缀，可用 --no-merge-think 丢弃)、FILE->保留文件名、
  其余工具类(TOOL_*/SEARCH)->丢弃（它们无文本，且 SEARCH.results 是
  Python repr 字符串）。

依赖：仅标准库（json / uuid / datetime / sys / argparse / hashlib / re）。

用法：
    python sunshinemahiru_deepseek_to_ucs.py <input.json> <output.json>
    python sunshinemahiru_deepseek_to_ucs.py in.json out.json --verbose
    python sunshinemahiru_deepseek_to_ucs.py in.json out.json --limit 10 --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def deterministic_uuid4(seed: str) -> str:
    """
    由 seed 生成确定性的 UUID v4 字符串（幂等）。

    用 sha256(seed) 取前 16 字节，再按 RFC 4122 置 version=4、variant 位。
    这样得到的是「版本号为 4」的合法 UUID，同时重复运行结果完全一致。
    """
    digest = hashlib.sha256(seed.encode("utf-8")).digest()[:16]
    b = bytearray(digest)
    b[6] = (b[6] & 0x0F) | 0x40   # version 4
    b[8] = (b[8] & 0x3F) | 0x80   # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(b)))


def to_unix(iso: Any) -> float:
    """
    把 ISO 8601 字符串转成 Unix 浮点时间戳（秒 + 微秒）。
    解析失败时返回 0.0（回退值）。

    注意：0.0 作为消息级 create_time 会让 ucs-parser 推断出 1970-01 的
    temporal 起点；但本数据时间戳全部合法，实际不会触发。
    """
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def extract_parts(
    fragments: list,
    merge_think: bool,
    warn: Callable[[str], None],
    stats: dict[str, int],
) -> list[str]:
    """把 fragments[] 转成 content.parts[]（字符串数组），保持顺序。"""
    parts: list[str] = []
    for frag in fragments:
        if not isinstance(frag, dict):
            stats["skipped_fragments"] += 1
            warn("fragment 不是 dict，跳过")
            continue

        ftype = str(frag.get("type") or "").upper()
        if not ftype:
            stats["skipped_fragments"] += 1
            warn("fragment 缺少 type，跳过")
            continue

        if ftype == "REQUEST":
            content = frag.get("content")
            if content is None:
                stats["skipped_fragments"] += 1
                warn("REQUEST fragment 缺少 content，跳过")
                continue
            stats["frag_REQUEST"] += 1
            text = str(content).strip()
            if text:
                parts.append(text)

        elif ftype == "RESPONSE":
            content = frag.get("content")
            if content is None:
                stats["skipped_fragments"] += 1
                warn("RESPONSE fragment 缺少 content，跳过")
                continue
            stats["frag_RESPONSE"] += 1
            text = str(content).strip()
            if text:
                parts.append(text)

        elif ftype == "THINK":
            content = frag.get("content")
            if not merge_think:
                stats["frag_THINK_dropped"] += 1
                continue
            if content is None:
                stats["skipped_fragments"] += 1
                warn("THINK fragment 缺少 content，跳过")
                continue
            stats["frag_THINK"] += 1
            text = str(content).strip()
            if text:
                parts.append(f"[REASONING] {text}")

        elif ftype == "FILE":
            # 保留附件文件名（用户上传的图片/文档）
            files = frag.get("files")
            if isinstance(files, list):
                for fobj in files:
                    if isinstance(fobj, dict) and fobj.get("file_name"):
                        stats["frag_FILE"] += 1
                        parts.append(f"[文件] {fobj['file_name']}")
            else:
                stats["frag_FILE"] += 1

        else:
            # TOOL_OPEN / TOOL_SEARCH / TOOL_FIND / SEARCH 及其他未知类型 -> 丢弃
            stats["frag_unknown"] += 1

    return parts


def find_current_node(mapping: dict[str, dict], root_uid: str) -> str:
    """从 root 出发 DFS，返回最后一个叶子节点（无 children 的节点）。"""
    leaves: list[str] = []

    def dfs(uid: str) -> None:
        node = mapping.get(uid)
        if node is None:
            return
        children = node.get("children", [])
        if not children:
            leaves.append(uid)
        else:
            for c in children:
                dfs(c)

    dfs(root_uid)
    return leaves[-1] if leaves else root_uid


# ---------------------------------------------------------------------------
# 会话转换
# ---------------------------------------------------------------------------

def convert_conversation(
    conv: dict,
    merge_think: bool,
    warn: Callable[[str], None],
    stats: dict[str, int],
) -> Optional[dict]:
    """转换单个会话，失败返回 None。"""
    conv_id = str(conv.get("id") or "")
    title = conv.get("title")
    raw = conv.get("mapping")
    if not isinstance(raw, dict) or not raw:
        warn(f"会话 '{title}' (id={conv_id}) 缺少 mapping，跳过")
        stats["skipped_conversations"] += 1
        return None

    # 定位 root：parent 为 null 的节点（数据里 id 为 "root"）
    root_ids = [nid for nid, n in raw.items()
                if isinstance(n, dict) and n.get("parent") is None]
    if not root_ids:
        root_ids = [nid for nid in raw if nid == "root"]
    if not root_ids:
        warn(f"会话 '{title}' (id={conv_id}) 无 root 节点，跳过")
        stats["skipped_conversations"] += 1
        return None
    root_id = root_ids[0]

    # ---- 第一遍：分类 kept/skipped，提取 role + parts ----
    kept: dict[str, bool] = {}
    role_parts: dict[str, tuple[Optional[str], Optional[list]]] = {}
    for nid, node in raw.items():
        if not isinstance(node, dict):
            continue
        msg = node.get("message")
        if msg is None:
            # root 节点或占位节点：保留，message 为 null
            kept[nid] = True
            role_parts[nid] = (None, None)
            continue
        frags = msg.get("fragments") or []
        types = [f.get("type") for f in frags if isinstance(f, dict)]
        role = "user" if "REQUEST" in types else "assistant"
        parts = extract_parts(frags, merge_think, warn, stats)
        role_parts[nid] = (role, parts)
        if parts:
            kept[nid] = True
        else:
            # 空文本节点：跳过，并在第二遍旁路重挂
            kept[nid] = False
            stats["skipped_nodes"] += 1

    # root 强制保留（防御性：即使 root 的 message 意外非 null 且无文本）
    kept[root_id] = True

    # ---- 第二遍：旁路重挂（把被跳过节点的子节点挂到其父节点） ----
    def eff_children(nid: str) -> list[str]:
        out: list[str] = []
        node = raw.get(nid)
        if not isinstance(node, dict):
            return out
        for c in node.get("children", []) or []:
            if c not in raw:
                continue
            if kept.get(c):
                out.append(c)
            else:
                out.extend(eff_children(c))
        return out

    def eff_parent(nid: str) -> Optional[str]:
        node = raw.get(nid)
        p = node.get("parent") if isinstance(node, dict) else None
        while p is not None and p in raw and not kept.get(p):
            pn = raw.get(p)
            p = pn.get("parent") if isinstance(pn, dict) else None
        return p

    # ---- 第三遍：构建输出 mapping（确定性 UUID） ----
    id_map: dict[str, str] = {}
    for nid in raw:
        if kept.get(nid):
            id_map[nid] = deterministic_uuid4(f"{conv_id}:{nid}")

    out_mapping: dict[str, dict] = {}
    for nid in raw:
        if not kept.get(nid):
            continue
        uid = id_map[nid]
        role, parts = role_parts[nid]
        if role is None and parts is None:
            # root 节点：message 为 null，parent 为 null
            out_mapping[uid] = {
                "id": uid,
                "message": None,
                "parent": None,
                "children": [id_map[c] for c in eff_children(nid)],
            }
        else:
            parent_nid = eff_parent(nid)
            node = raw[nid]
            msg_ts = node.get("message", {}).get("inserted_at")
            out_mapping[uid] = {
                "id": uid,
                "message": {
                    "id": uid,
                    "author": {"role": role, "name": None, "metadata": {}},
                    "content": {"content_type": "text", "parts": parts},
                    "create_time": to_unix(msg_ts),
                    "update_time": None,
                    "metadata": {},
                },
                "parent": id_map[parent_nid] if parent_nid is not None else None,
                "children": [id_map[c] for c in eff_children(nid)],
            }
            stats["message_nodes"] += 1

    root_uid = id_map[root_id]
    current_node = find_current_node(out_mapping, root_uid)

    return {
        "title": title,
        "create_time": to_unix(conv.get("inserted_at")),
        "update_time": to_unix(conv.get("updated_at")),
        "mapping": out_mapping,
        "current_node": current_node,
        "conversation_id": conv_id,
        "moderation_results": [],
        "is_archived": False,
        "safe_urls": [],
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sunshinemahiru_deepseek_to_ucs",
        description="DeepSeek conversations.json -> ChatGPT/ucs-parser mapping 转换器",
    )
    ap.add_argument("input", help="DeepSeek 导出的 conversations.json 路径")
    ap.add_argument("output", help="输出的 ChatGPT 格式 JSON 路径")
    ap.add_argument("--verbose", action="store_true", help="打印每个会话的转换统计")
    ap.add_argument("--dry-run", action="store_true", help="只解析和统计，不写文件")
    ap.add_argument("--limit", type=int, default=None, help="只转换前 N 个会话（用于快速测试）")
    ap.add_argument(
        "--merge-think", action=argparse.BooleanOptionalAction, default=True,
        help="保留 THINK 推理片段并加 [REASONING] 前缀（默认开启）；--no-merge-think 丢弃",
    )
    args = ap.parse_args(argv)

    # 读取输入
    try:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"错误：无法读取输入文件：{e}", file=sys.stderr)
        return 2

    # 顶层必须是数组；否则尝试包装
    if not isinstance(data, list):
        print(f"警告：输入顶层不是数组（{type(data).__name__}），已包装为单元素数组", file=sys.stderr)
        data = [data]

    if args.limit is not None:
        data = data[: args.limit]

    total = len(data)

    stats: dict[str, int] = {
        "skipped_conversations": 0,
        "message_nodes": 0,
        "skipped_nodes": 0,
        "skipped_fragments": 0,
        "frag_REQUEST": 0,
        "frag_RESPONSE": 0,
        "frag_THINK": 0,
        "frag_THINK_dropped": 0,
        "frag_FILE": 0,
        "frag_unknown": 0,
    }
    warnings: list[str] = []

    def warn(msg: str) -> None:
        warnings.append(msg)

    out: list[dict] = []
    for i, conv in enumerate(data, start=1):
        if not isinstance(conv, dict):
            warn(f"第 {i} 个会话不是 dict，跳过")
            stats["skipped_conversations"] += 1
            continue

        result = convert_conversation(conv, args.merge_think, warn, stats)
        if result is not None:
            out.append(result)

        if args.verbose:
            print(f"[{i}/{total}] '{conv.get('title')}' -> "
                  f"{len(result['mapping']) if result else 0} 节点")

        if total >= 50 and (i % 50 == 0 or i == total):
            print(f"已处理 {i}/{total} 个会话")

    # ---- 输出统计 ----
    print("\n" + "=" * 60)
    print("转换统计")
    print("=" * 60)
    print(f"  总会话数            : {total}")
    print(f"  成功转换会话数      : {len(out)}")
    print(f"  跳过的会话数        : {stats['skipped_conversations']}")
    print(f"  总消息节点数(不含root): {stats['message_nodes']}")
    print(f"  跳过的空节点数      : {stats['skipped_nodes']}")
    print(f"  跳过的问题 fragment  : {stats['skipped_fragments']}")
    print("  fragment 类型计数:")
    print(f"    REQUEST          : {stats['frag_REQUEST']}")
    print(f"    RESPONSE         : {stats['frag_RESPONSE']}")
    print(f"    THINK(保留)      : {stats['frag_THINK']}")
    print(f"    THINK(丢弃)      : {stats['frag_THINK_dropped']}")
    print(f"    FILE(文件名)     : {stats['frag_FILE']}")
    print(f"    unknown/工具类    : {stats['frag_unknown']}")
    print(f"  警告/跳过记录       : {len(warnings)} 条")

    # ---- 写日志 ----
    log_path = args.output + ".log"
    if not args.dry_run:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("sunshinemahiru_deepseek_to_ucs 转换日志\n")
            f.write(f"输入: {args.input}\n")
            f.write(f"输出: {args.output}\n")
            f.write(f"警告/跳过 {len(warnings)} 条\n")
            f.write("-" * 60 + "\n")
            for w in warnings:
                f.write(w + "\n")
        if warnings:
            print(f"  日志已写入: {log_path}")
    else:
        if warnings:
            print(f"  [dry-run] 跳过 {len(warnings)} 条，未写日志")

    # ---- 写输出 ----
    if args.dry_run:
        print("\n[dry-run] 未写入输出文件")
        return 0

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n输出已写入: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
