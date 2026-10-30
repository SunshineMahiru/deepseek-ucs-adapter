#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deepseek_to_ucs.py
=================================

Convert DeepSeek web-export conversations.json into ChatGPT mapping format
that ucs-parser can parse. UI text is bilingual (Chinese / English), switchable
via `--lang zh|en`.

将 DeepSeek 网页版导出的 conversations.json 转换为 ucs-parser 可解析的
ChatGPT mapping 格式。界面文案支持中英双语，用 `--lang zh|en` 切换。

背景 / Background
----
ucs-parser（v0.1.0）在 `_extract_messages` 中只读取三个字段：
    message.author.role、message.content.parts[]、message.create_time
DeepSeek 导出虽然也是 mapping 树结构，但消息内容存放在 message.fragments[]
中，导致 ucs-parser 提取不到任何信号，Fidelity 分数极低。

本脚本完成 / What it does:
    1. fragments[] -> author.role + content.parts[] + message.metadata
    2. 短节点 ID -> 确定性 UUID v4（幂等，重复运行输出完全一致）
    3. 推断 current_node（create_time 最新的叶子节点）
    4. ISO 8601 时间戳 -> Unix 浮点数
    5. 补齐 ChatGPT 会话与消息字段

关键设计说明（务必阅读）/ Design notes:
- 默认是保真转换：节点与父子关系沿用源数据，parts 只放请求与回复的原文，
  不裁剪、不插入标记。THINK 进 message.metadata.reasoning，附件信息进
  metadata.attachments，工具结果进 metadata.tool_results 或 tool_calls；
  每条消息的 metadata 还记录 original_id、original_parent 与 model。
- 需要旧的前缀写法时加 --legacy-tags；需要跳过空节点并把子节点旁路到
  最近的保留祖先时加 --flatten-empty。两个开关都关闭时不动源结构。
- fragment.type 完整类型集（实测 8 种）：REQUEST / RESPONSE / THINK /
  TOOL_OPEN / TOOL_SEARCH / TOOL_FIND / SEARCH / FILE。
  角色规则：含 REQUEST 的节点为 user；只含 FILE 且没有 RESPONSE 或 THINK
  的节点也是 user；其余为 assistant。

依赖 / Dependencies: 仅标准库（json / uuid / datetime / sys / argparse /
hashlib / unicodedata / os / pathlib）。

用法 / Usage:
    python deepseek_to_ucs.py <input.json> <output.json>
    python deepseek_to_ucs.py in.json out.json --verbose
    python deepseek_to_ucs.py in.json out.json --limit 10 --dry-run
    python deepseek_to_ucs.py in.json out.json --lang en
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# i18n：界面文案（中英双语）
# ---------------------------------------------------------------------------

_LANG: str = "zh"

# 已知但没有任何文本内容的工具类片段，与真正未知的类型分开统计
_TOOL_TYPES = {"TOOL_OPEN", "TOOL_SEARCH", "TOOL_FIND", "SEARCH"}

MSG: dict[str, dict[str, str]] = {
    # argparse 帮助文案
    "prog_desc": {
        "zh": "DeepSeek conversations.json -> ChatGPT/ucs-parser mapping 转换器",
        "en": "Convert DeepSeek conversations.json into ChatGPT/ucs-parser mapping",
    },
    "arg_input": {"zh": "DeepSeek 导出的 conversations.json 路径", "en": "Path to the DeepSeek conversations.json export"},
    "arg_output": {"zh": "输出的 ChatGPT 格式 JSON 路径", "en": "Output path for the ChatGPT-format JSON"},
    "arg_verbose": {"zh": "打印每个会话的转换统计", "en": "Print per-conversation statistics"},
    "arg_dry_run": {"zh": "只解析和统计，不写文件", "en": "Parse and report only, without writing files"},
    "arg_limit": {"zh": "只转换前 N 个会话（用于快速测试）", "en": "Convert only the first N conversations (for quick testing)"},
    "arg_merge_think": {
        "zh": "保留 THINK 推理片段并写入 message.metadata.reasoning（默认开启）；--no-merge-think 丢弃",
        "en": "Keep THINK fragments in message.metadata.reasoning (default on); --no-merge-think drops them",
    },
    "arg_keep_tools": {
        "zh": "配合 --legacy-tags，把 SEARCH 结果写成 [工具] 行（默认关闭）",
        "en": "With --legacy-tags, write SEARCH results as [TOOL] lines (default off)",
    },
    "arg_legacy_tags": {
        "zh": "沿用旧的 [REASONING] / [文件] / [工具] 前缀，把注记写进 parts",
        "en": "Use the old [REASONING] / [FILE] / [TOOL] prefixes inside parts",
    },
    "arg_flatten_empty": {
        "zh": "跳过没有文本的节点，并把子节点旁路到最近的保留祖先",
        "en": "Skip nodes without text and reattach their children to the nearest kept ancestor",
    },
    "arg_lang": {"zh": "界面语言（zh=中文，en=English）", "en": "UI language (zh=Chinese, en=English)"},

    # 警告（fragment / 会话）
    "warn_frag_not_dict": {"zh": "fragment 不是 dict，跳过", "en": "fragment is not a dict, skipped"},
    "warn_frag_no_type": {"zh": "fragment 缺少 type，跳过", "en": "fragment missing type, skipped"},
    "warn_req_no_content": {"zh": "REQUEST fragment 缺少 content，跳过", "en": "REQUEST fragment missing content, skipped"},
    "warn_resp_no_content": {"zh": "RESPONSE fragment 缺少 content，跳过", "en": "RESPONSE fragment missing content, skipped"},
    "warn_think_no_content": {"zh": "THINK fragment 缺少 content，跳过", "en": "THINK fragment missing content, skipped"},
    "warn_file_not_list": {"zh": "FILE fragment 的 files 不是数组，跳过", "en": "FILE fragment has a non-list files field, skipped"},
    "warn_file_no_name": {"zh": "FILE fragment 没有可用的文件名，跳过", "en": "FILE fragment has no usable file name, skipped"},
    "warn_tool_no_results": {"zh": "工具片段的结果无法解析，跳过", "en": "tool fragment results could not be parsed, skipped"},
    "warn_conv_no_mapping": {"zh": "会话 '{title}' (id={id}) 缺少 mapping，跳过", "en": "conversation '{title}' (id={id}) missing mapping, skipped"},
    "warn_conv_no_root": {"zh": "会话 '{title}' (id={id}) 无 root 节点，跳过", "en": "conversation '{title}' (id={id}) has no root node, skipped"},
    "warn_conv_not_dict": {"zh": "第 {i} 个会话不是 dict，跳过", "en": "conversation #{i} is not a dict, skipped"},
    "warn_conv_root_not_dict": {"zh": "会话 '{title}' (id={id}) 的 root 节点不是 dict，跳过", "en": "conversation '{title}' (id={id}) has a non-dict root node, skipped"},
    "warn_node_not_dict": {"zh": "节点 {id} 不是 dict，跳过", "en": "node {id} is not a dict, skipped"},
    "warn_msg_not_dict": {"zh": "节点 {id} 的 message 不是 dict，跳过", "en": "message of node {id} is not a dict, skipped"},

    # 错误
    "err_read_input": {"zh": "错误：无法读取输入文件：{e}", "en": "Error: cannot read input file: {e}"},
    "err_write_log": {"zh": "错误：无法写入日志文件：{e}", "en": "Error: cannot write log file: {e}"},
    "err_write_output": {"zh": "错误：无法写入输出文件：{e}", "en": "Error: cannot write output file: {e}"},
    "err_bad_limit": {"zh": "错误：--limit 必须是正整数，收到 {n}", "en": "Error: --limit must be a positive integer, got {n}"},
    "err_not_conversation": {"zh": "错误：输入不是会话数组，也不是包含 mapping 的单个会话对象", "en": "Error: input is neither a conversation array nor a single conversation object with a mapping"},
    "err_no_output": {"zh": "错误：没有任何会话转换成功，输出为空数组", "en": "Error: no conversation was converted, the output is an empty array"},
    "warn_top_not_array": {"zh": "警告：输入顶层不是数组（{t}），已包装为单元素数组", "en": "Warning: top-level input is not an array ({t}), wrapped as a single-element array"},

    # 进度 / verbose
    "verbose_conv": {"zh": "[{i}/{total}] '{title}' -> {n} 节点", "en": "[{i}/{total}] '{title}' -> {n} nodes"},
    "progress": {"zh": "已处理 {i}/{total} 个会话", "en": "Processed {i}/{total} conversations"},

    # 统计
    "stats_title": {"zh": "转换统计", "en": "Conversion statistics"},
    "stats_total": {"zh": "总会话数", "en": "Total conversations"},
    "stats_processed": {"zh": "本次处理会话数", "en": "Processed in this run"},
    "stats_success": {"zh": "成功转换会话数", "en": "Successfully converted"},
    "stats_skipped_conv": {"zh": "跳过的会话数", "en": "Skipped conversations"},
    "stats_msg_nodes": {"zh": "总消息节点数(不含root)", "en": "Message nodes (excl. root)"},
    "stats_skipped_nodes": {"zh": "跳过的空节点数", "en": "Skipped empty nodes"},
    "stats_skipped_frags": {"zh": "跳过的问题 fragment", "en": "Skipped malformed fragments"},
    "stats_frag_title": {"zh": "fragment 类型计数", "en": "Fragment type counts"},
    "stats_frag_request": {"zh": "REQUEST", "en": "REQUEST"},
    "stats_frag_response": {"zh": "RESPONSE", "en": "RESPONSE"},
    "stats_frag_think_kept": {"zh": "THINK(保留)", "en": "THINK (kept)"},
    "stats_frag_think_dropped": {"zh": "THINK(丢弃)", "en": "THINK (dropped)"},
    "stats_frag_file": {"zh": "FILE(文件名)", "en": "FILE (filenames)"},
    "stats_frag_tool": {"zh": "工具类片段", "en": "tool fragments"},
    "stats_frag_unknown": {"zh": "未识别类型", "en": "unrecognised types"},
    "stats_warnings": {"zh": "警告/跳过记录", "en": "Warnings/skips"},

    # 写入消息内容的标记
    "tag_reasoning": {"zh": "[REASONING] {text}", "en": "[REASONING] {text}"},
    "tag_file": {"zh": "[文件] {name}", "en": "[FILE] {name}"},
    "tag_tool": {"zh": "[工具] {text}", "en": "[TOOL] {text}"},

    # 日志 / 输出
    "log_header": {"zh": "deepseek_to_ucs 转换日志", "en": "deepseek_to_ucs conversion log"},
    "log_input": {"zh": "输入: {path}", "en": "Input: {path}"},
    "log_output": {"zh": "输出: {path}", "en": "Output: {path}"},
    "log_warn_count": {"zh": "警告/跳过 {n} 条", "en": "Warnings/skips: {n}"},
    "log_written": {"zh": "日志已写入: {path}", "en": "Log written to: {path}"},
    "dryrun_no_log": {"zh": "[dry-run] 跳过 {n} 条，未写日志", "en": "[dry-run] {n} item(s) skipped, no log written"},
    "warn_more": {"zh": "…另有 {n} 条未显示", "en": "... {n} more not shown"},
    "dryrun_no_output": {"zh": "[dry-run] 未写入输出文件", "en": "[dry-run] output file not written"},
    "output_written": {"zh": "输出已写入: {path}", "en": "Output written to: {path}"},
}


def set_lang(lang: str) -> None:
    """设置界面语言：'zh' 或 'en'。"""
    global _LANG
    _LANG = lang if lang in ("zh", "en") else "zh"


def t(key: str, **kwargs: Any) -> str:
    """返回当前语言下的消息文案，并应用格式化参数。"""
    fmt = MSG.get(key, {}).get(_LANG, key)
    return fmt.format(**kwargs) if kwargs else fmt


def display_width(text: str) -> int:
    """按终端显示列数计算宽度：全角与宽字符占两列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def deterministic_uuid4(seed: str) -> str:
    """由 seed 生成确定性的 UUID v4 字符串（幂等）。"""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()[:16]
    b = bytearray(digest)
    b[6] = (b[6] & 0x0F) | 0x40   # version 4
    b[8] = (b[8] & 0x3F) | 0x80   # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(b)))


def to_unix(iso: Any) -> Optional[float]:
    """把 ISO 8601 字符串转成 Unix 浮点时间戳，缺失或无法解析时返回 None。

    不带时区的时间按 UTC 解释，保证同一份导出在不同机器上得到相同的值。
    Python 3.10 的 fromisoformat 不接受结尾的 Z，这里先改写为 +00:00。
    """
    if not iso:
        return None
    text = str(iso).strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_tool_results(raw: Any) -> Optional[list[dict[str, str]]]:
    """解析 SEARCH / TOOL_SEARCH 的 results，返回 [{"title": ..., "url": ...}]。

    DeepSeek 导出的 results 是 Python repr 字符串，需要 literal_eval；
    解析失败或没有可用条目时返回 None。
    """
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(raw, list):
        return None

    items: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if title or url:
            items.append({"title": title, "url": url})
    return items or None


def format_tool_lines(items: list[dict[str, str]]) -> str:
    """把解析后的工具结果拼成多行文本，供旧的前缀写法使用。"""
    lines: list[str] = []
    for item in items:
        if item["title"] and item["url"]:
            lines.append(f"{item['title']} — {item['url']}")
        elif item["title"]:
            lines.append(item["title"])
        else:
            lines.append(item["url"])
    return "\n".join(lines)


def extract_fragments(
    fragments: list,
    merge_think: bool,
    keep_tools: bool,
    legacy_tags: bool,
    warn: Callable[[str], None],
    stats: dict[str, int],
) -> tuple[list[str], dict[str, Any]]:
    """把 fragments[] 拆成 (content.parts, message.metadata)。

    默认路径只把正文写进 parts：REQUEST 与 RESPONSE 的原文按顺序追加，
    THINK 进 metadata.reasoning，附件信息进 metadata.attachments，
    工具结果进 metadata.tool_results 或 metadata.tool_calls。
    legacy_tags 打开时沿用旧的 [REASONING] / [文件] / [工具] 前缀写法。
    """
    parts: list[str] = []
    reasoning: list[str] = []
    attachments: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    tool_calls: list[str] = []
    unparsed_tools: list[dict[str, Any]] = []
    for frag in fragments:
        if not isinstance(frag, dict):
            stats["skipped_fragments"] += 1
            warn(t("warn_frag_not_dict"))
            continue

        ftype = str(frag.get("type") or "").upper()
        if not ftype:
            stats["skipped_fragments"] += 1
            warn(t("warn_frag_no_type"))
            continue

        if ftype in ("REQUEST", "RESPONSE"):
            content = frag.get("content")
            if content is None:
                stats["skipped_fragments"] += 1
                warn(t("warn_req_no_content" if ftype == "REQUEST" else "warn_resp_no_content"))
                continue
            stats["frag_REQUEST" if ftype == "REQUEST" else "frag_RESPONSE"] += 1
            text = str(content)
            if text.strip():
                parts.append(text)

        elif ftype == "THINK":
            content = frag.get("content")
            if not merge_think:
                stats["frag_THINK_dropped"] += 1
                continue
            if content is None:
                stats["skipped_fragments"] += 1
                warn(t("warn_think_no_content"))
                continue
            stats["frag_THINK"] += 1
            text = str(content)
            if not text.strip():
                continue
            if legacy_tags:
                parts.append(t("tag_reasoning", text=text))
            else:
                reasoning.append(text)

        elif ftype == "FILE":
            # 保留附件文件名（用户上传的图片/文档）
            files = frag.get("files")
            if not isinstance(files, list):
                stats["skipped_fragments"] += 1
                warn(t("warn_file_not_list"))
                continue
            produced = 0
            for fobj in files:
                if not isinstance(fobj, dict) or not fobj.get("file_name"):
                    continue
                produced += 1
                stats["frag_FILE"] += 1
                name = str(fobj["file_name"])
                record = {k: fobj[k] for k in ("file_id", "file_name", "file_size") if k in fobj}
                attachments.append(record)
                if legacy_tags:
                    parts.append(t("tag_file", name=name))
                else:
                    parts.append(name)
            if not produced:
                stats["skipped_fragments"] += 1
                warn(t("warn_file_no_name"))

        else:
            # 已知的工具类片段与真正未知的类型分开计数
            if ftype in _TOOL_TYPES:
                stats["frag_tool"] += 1
                if ftype in ("TOOL_SEARCH", "SEARCH"):
                    items = parse_tool_results(frag.get("results"))
                    if items is None:
                        stats["skipped_fragments"] += 1
                        warn(t("warn_tool_no_results"))
                        unparsed_tools.append({"type": ftype, "raw": frag.get("results")})
                        continue
                    tool_results.append({"type": ftype, "items": items})
                    if legacy_tags and keep_tools:
                        parts.append(t("tag_tool", text=format_tool_lines(items)))
                else:
                    tool_calls.append(ftype)
            else:
                stats["frag_unknown"] += 1

    meta: dict[str, Any] = {}
    if reasoning:
        meta["reasoning"] = reasoning
    if attachments:
        meta["attachments"] = attachments
    if tool_results:
        meta["tool_results"] = tool_results
    if tool_calls:
        meta["tool_calls"] = tool_calls
    if unparsed_tools:
        meta["unparsed_tools"] = unparsed_tools
    return parts, meta


def find_current_node(mapping: dict[str, dict], root_uid: str) -> str:
    """返回 create_time 最大的叶子节点；时间相同或全部缺失时取遍历顺序靠后的。"""
    best_uid = root_uid
    best_time = float("-inf")
    best_order = -1
    order = 0

    stack = [root_uid]
    while stack:
        uid = stack.pop()
        node = mapping.get(uid)
        if node is None:
            continue
        order += 1
        children = node.get("children", [])
        if children:
            stack.extend(reversed(children))
            continue

        msg = node.get("message")
        ts = msg.get("create_time") if isinstance(msg, dict) else None
        ts = float(ts) if isinstance(ts, (int, float)) else float("-inf")
        if ts > best_time or (ts == best_time and order > best_order):
            best_uid = uid
            best_time = ts
            best_order = order

    return best_uid


# ---------------------------------------------------------------------------
# 会话转换
# ---------------------------------------------------------------------------

def convert_conversation(
    conv: dict,
    merge_think: bool,
    warn: Callable[[str], None],
    stats: dict[str, int],
    keep_tools: bool = False,
    legacy_tags: bool = False,
    flatten_empty: bool = False,
) -> Optional[dict]:
    """转换单个会话，失败返回 None。

    默认保留全部节点与原始父子关系；flatten_empty 打开时退回旧行为，
    跳过没有文本的节点并把子节点旁路到最近的保留祖先。
    """
    conv_id = str(conv.get("id") or "")
    title = conv.get("title")
    raw = conv.get("mapping")
    if not isinstance(raw, dict) or not raw:
        warn(t("warn_conv_no_mapping", title=title, id=conv_id))
        stats["skipped_conversations"] += 1
        return None

    # 定位 root：parent 为 null 的节点（数据里 id 为 "root"）
    root_ids = [nid for nid, n in raw.items()
                if isinstance(n, dict) and n.get("parent") is None]
    if not root_ids:
        root_ids = [nid for nid in raw if nid == "root"]
    if not root_ids:
        warn(t("warn_conv_no_root", title=title, id=conv_id))
        stats["skipped_conversations"] += 1
        return None
    root_id = root_ids[0]
    if not isinstance(raw.get(root_id), dict):
        warn(t("warn_conv_root_not_dict", title=title, id=conv_id))
        stats["skipped_conversations"] += 1
        return None

    # ---- 第一遍：解析每个节点，默认全部保留 ----
    kept: dict[str, bool] = {}
    node_data: dict[str, tuple[Optional[str], list[str], dict[str, Any]]] = {}
    for nid, node in raw.items():
        if not isinstance(node, dict):
            kept[nid] = False
            stats["skipped_nodes"] += 1
            warn(t("warn_node_not_dict", id=nid))
            continue
        msg = node.get("message")
        if msg is None:
            if nid == root_id:
                kept[nid] = True
                node_data[nid] = (None, [], {})
            elif flatten_empty:
                kept[nid] = False
                stats["skipped_nodes"] += 1
            else:
                # 保真模式：保留占位节点，输出 message 为 null
                kept[nid] = True
                node_data[nid] = (None, [], {})
            continue
        if not isinstance(msg, dict):
            warn(t("warn_msg_not_dict", id=nid))
            if nid == root_id:
                kept[nid] = True
                node_data[nid] = (None, [], {})
            elif flatten_empty:
                kept[nid] = False
                stats["skipped_nodes"] += 1
            else:
                kept[nid] = True
                node_data[nid] = ("assistant", [], {"broken_message": True})
            continue
        frags = msg.get("fragments") or []
        types = {str(f.get("type") or "").upper() for f in frags if isinstance(f, dict)}
        if "REQUEST" in types:
            role = "user"
        elif "FILE" in types and not types & {"RESPONSE", "THINK"}:
            # 纯附件节点：用户上传的文件，没有助手回复或推理
            role = "user"
        else:
            role = "assistant"
        parts, meta = extract_fragments(
            frags, merge_think, keep_tools, legacy_tags, warn, stats
        )
        node_data[nid] = (role, parts, meta)
        if flatten_empty and not parts:
            kept[nid] = False
            stats["skipped_nodes"] += 1
        else:
            kept[nid] = True

    # root 强制保留（防御性：即使第一遍把它判为跳过）
    kept[root_id] = True
    if root_id not in node_data:
        node_data[root_id] = (None, [], {})

    # ---- 第二遍：父子关系。默认沿用源数据，flatten_empty 时旁路重挂 ----
    def source_children(nid: str) -> list[str]:
        node = raw.get(nid)
        if not isinstance(node, dict):
            return []
        return [c for c in (node.get("children") or [])
                if isinstance(c, str) and c in raw and c != nid]

    def eff_children(nid: str) -> list[str]:
        kids = source_children(nid)
        if not flatten_empty:
            return [c for c in kids if kept.get(c)]
        out: list[str] = []
        seen = {nid}
        stack = list(reversed(kids))
        while stack:
            c = stack.pop()
            if c in seen or c not in raw:
                continue
            seen.add(c)
            if kept.get(c):
                out.append(c)
                continue
            child_node = raw.get(c)
            more = child_node.get("children", []) if isinstance(child_node, dict) else []
            stack.extend(reversed(more or []))
        return out

    def eff_parent(nid: str) -> Optional[str]:
        node = raw.get(nid)
        p = node.get("parent") if isinstance(node, dict) else None
        if p is None or p not in raw:
            return None
        if not flatten_empty:
            return p if kept.get(p) else None
        seen = {nid}
        while p is not None:
            # parent 指向不存在的节点，或父链成环：当作独立根节点
            if p in seen or p not in raw:
                return None
            seen.add(p)
            if kept.get(p):
                return p
            pn = raw.get(p)
            p = pn.get("parent") if isinstance(pn, dict) else None
        return None

    # ---- 第三遍：构建输出 mapping（确定性 UUID + 溯源 metadata） ----
    id_map: dict[str, str] = {}
    for nid in raw:
        if kept.get(nid):
            id_map[nid] = deterministic_uuid4(f"{conv_id}:{nid}")

    out_mapping: dict[str, dict] = {}
    for nid in raw:
        if not kept.get(nid):
            continue
        uid = id_map[nid]
        role, parts, meta = node_data[nid]
        children = eff_children(nid)
        parent_nid = eff_parent(nid)
        node = raw[nid]
        msg = node.get("message") if isinstance(node, dict) else None

        if role is None:
            # root 或没有 message 的占位节点
            out_mapping[uid] = {
                "id": uid,
                "message": None,
                "parent": None if nid == root_id else (
                    id_map[parent_nid] if parent_nid is not None else None
                ),
                "children": [id_map[c] for c in children],
            }
            continue

        msg_meta: dict[str, Any] = {"original_id": nid}
        if parent_nid is not None:
            msg_meta["original_parent"] = parent_nid
        if isinstance(msg, dict) and msg.get("model"):
            msg_meta["model"] = msg["model"]
        msg_meta.update(meta)

        model = msg.get("model") if isinstance(msg, dict) else None
        author_name = str(model) if role == "assistant" and model else None
        msg_ts = msg.get("inserted_at") if isinstance(msg, dict) else None

        out_mapping[uid] = {
            "id": uid,
            "message": {
                "id": uid,
                "author": {"role": role, "name": author_name, "metadata": {}},
                "content": {"content_type": "text", "parts": parts},
                "create_time": to_unix(msg_ts),
                "update_time": None,
                "end_turn": not children,
                "metadata": msg_meta,
                "recipient": "all",
                "status": "finished_successfully",
            },
            "parent": id_map[parent_nid] if parent_nid is not None else None,
            "children": [id_map[c] for c in children],
        }
        stats["message_nodes"] += 1

    root_uid = id_map[root_id]
    current_node = find_current_node(out_mapping, root_uid)

    return {
        "id": conv_id,
        "title": title,
        "create_time": to_unix(conv.get("inserted_at")),
        "update_time": to_unix(conv.get("updated_at")),
        "mapping": out_mapping,
        "current_node": current_node,
        "conversation_id": conv_id,
        "moderation_results": [],
        "is_archived": False,
        "safe_urls": [],
        "plugin_ids": [],
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _preparse_lang(argv: list[str]) -> str:
    """在 argparse 之前手动提取 --lang，以便帮助文案也能本地化。"""
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return "zh"


def main(argv: Optional[list[str]] = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    set_lang(_preparse_lang(raw_argv))

    ap = argparse.ArgumentParser(
        prog="deepseek-ucs",
        description=t("prog_desc"),
    )
    ap.add_argument("input", help=t("arg_input"))
    ap.add_argument("output", help=t("arg_output"))
    ap.add_argument("--verbose", action="store_true", help=t("arg_verbose"))
    ap.add_argument("--dry-run", action="store_true", help=t("arg_dry_run"))
    ap.add_argument("--limit", type=int, default=None, help=t("arg_limit"))
    ap.add_argument(
        "--merge-think", action=argparse.BooleanOptionalAction, default=True,
        help=t("arg_merge_think"),
    )
    ap.add_argument("--lang", choices=["zh", "en"], default="zh", help=t("arg_lang"))
    ap.add_argument("--keep-tool-fragments", action="store_true", help=t("arg_keep_tools"))
    ap.add_argument("--legacy-tags", action="store_true", help=t("arg_legacy_tags"))
    ap.add_argument("--flatten-empty", action="store_true", help=t("arg_flatten_empty"))
    args = ap.parse_args(raw_argv)

    # 以 argparse 解析结果为准
    set_lang(args.lang)

    # 读取输入
    try:
        with open(args.input, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(t("err_read_input", e=e), file=sys.stderr)
        return 2

    # 顶层必须是数组；单个会话对象可以被包装，其余结构直接判为输入错误
    if not isinstance(data, list):
        if not isinstance(data, dict) or "mapping" not in data:
            print(t("err_not_conversation"), file=sys.stderr)
            return 2
        print(t("warn_top_not_array", t=type(data).__name__), file=sys.stderr)
        data = [data]

    source_total = len(data)
    if args.limit is not None:
        if args.limit <= 0:
            print(t("err_bad_limit", n=args.limit), file=sys.stderr)
            return 2
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
        "frag_tool": 0,
        "frag_unknown": 0,
    }
    warnings: list[str] = []
    current_conv = ""

    def warn(msg: str) -> None:
        prefix = f"[{current_conv}] " if current_conv else ""
        warnings.append(prefix + msg)

    out: list[dict] = []
    for i, conv in enumerate(data, start=1):
        if not isinstance(conv, dict):
            current_conv = f"#{i}"
            warn(t("warn_conv_not_dict", i=i))
            stats["skipped_conversations"] += 1
            continue

        current_conv = str(conv.get("id") or conv.get("title") or f"#{i}")
        result = convert_conversation(
            conv, args.merge_think, warn, stats,
            args.keep_tool_fragments, args.legacy_tags, args.flatten_empty,
        )
        if result is not None:
            out.append(result)

        if args.verbose:
            print(t("verbose_conv", i=i, total=total, title=conv.get("title"),
                    n=len(result["mapping"]) if result else 0))

        if total >= 50 and (i % 50 == 0 or i == total):
            print(t("progress", i=i, total=total))

    # ---- 输出统计 ----
    print("\n" + "=" * 60)
    print(t("stats_title"))
    print("=" * 60)
    rows = [(t("stats_total"), str(source_total))]
    if total != source_total:
        rows.append((t("stats_processed"), str(total)))
    rows.extend([
        (t("stats_success"), str(len(out))),
        (t("stats_skipped_conv"), str(stats["skipped_conversations"])),
        (t("stats_msg_nodes"), str(stats["message_nodes"])),
        (t("stats_skipped_nodes"), str(stats["skipped_nodes"])),
        (t("stats_skipped_frags"), str(stats["skipped_fragments"])),
    ])
    width = max(display_width(label) for label, _ in rows) + 2
    for label, value in rows:
        pad = " " * (width - display_width(label))
        print(f"  {label}{pad}: {value}")
    print("  " + t("stats_frag_title") + ":")
    frag_rows = [
        (t("stats_frag_request"), stats["frag_REQUEST"]),
        (t("stats_frag_response"), stats["frag_RESPONSE"]),
        (t("stats_frag_think_kept"), stats["frag_THINK"]),
        (t("stats_frag_think_dropped"), stats["frag_THINK_dropped"]),
        (t("stats_frag_file"), stats["frag_FILE"]),
        (t("stats_frag_tool"), stats["frag_tool"]),
        (t("stats_frag_unknown"), stats["frag_unknown"]),
    ]
    fwidth = max(display_width(label) for label, _ in frag_rows) + 2
    for label, value in frag_rows:
        pad = " " * (fwidth - display_width(label))
        print(f"    {label}{pad}: {value}")
    print(f"  {t('stats_warnings')}: {len(warnings)}")

    # ---- 写日志 ----
    log_path = args.output + ".log"
    if not args.dry_run:
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(t("log_header") + "\n")
                f.write(t("log_input", path=args.input) + "\n")
                f.write(t("log_output", path=args.output) + "\n")
                f.write(t("log_warn_count", n=len(warnings)) + "\n")
                f.write("-" * 60 + "\n")
                for w in warnings:
                    f.write(w + "\n")
        except OSError as e:
            print(t("err_write_log", e=e), file=sys.stderr)
            return 2
        if warnings:
            print(t("log_written", path=log_path))
    else:
        if warnings:
            print(t("dryrun_no_log", n=len(warnings)))
            for w in warnings[:20]:
                print("  " + w)
            if len(warnings) > 20:
                print("  " + t("warn_more", n=len(warnings) - 20))

    # ---- 写输出 ----
    if args.dry_run:
        print("\n" + t("dryrun_no_output"))
        return 0

    tmp_path = args.output + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, args.output)
    except OSError as e:
        Path(tmp_path).unlink(missing_ok=True)
        print(t("err_write_output", e=e), file=sys.stderr)
        return 2

    print("\n" + t("output_written", path=args.output))
    if not out and total > 0:
        print(t("err_no_output"), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
