<h1 align="center">deepseek-ucs-adapter</h1>

<p align="center">把 DeepSeek 对话导出转换为可移植的 UCS 用户画像，隐私优先、中英双语。</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square" alt="Python 3.10+">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-22C55E?style=flat-square" alt="License: MIT"></a>
</p>

## 解决什么问题

DeepSeek 网页版导出的 `conversations.json` 虽然也是 `mapping` 树结构，但消息内容存放在 `message.fragments[]` 中——实测共八种 fragment 类型：`REQUEST`、`RESPONSE`、`THINK`、`TOOL_OPEN`、`TOOL_SEARCH`、`TOOL_FIND`、`SEARCH`、`FILE`。

而 [ucs-parser](https://github.com/XwhyZ-WHYLD/universal-cognitive-schema)（Universal Cognitive Schema 参考实现）只读取 ChatGPT 格式的三个字段：`message.author.role`、`message.content.parts[]`、`message.create_time`。直接把 DeepSeek 导出喂进去，会得到 Persona 0.30、Interaction 0.10 的极低分，画像几乎为空。

本项目做两件事：

1. **格式转换** —— 把 DeepSeek 的 `fragments[]` 无损转换为 ChatGPT 的 `content.parts[]`，让 ucs-parser 能正确提取画像。
2. **风格蒸馏** —— 基于 2856 条真实 user 消息的语言学调研，产出手工风格卡片 + 三种 Agent 注入模板，让画像真正落地到 Agent 行为。

## 亮点

| 亮点 | 价值 |
|---|---|
| fragments → parts 无损转换 | 覆盖全部八种 DeepSeek fragment 类型，让 ucs-parser 提取出真实画像 |
| 确定性 UUID v4 | 输出可复现、可 diff |
| 隐私优先设计 | 原始对话数据物理隔离，不进入仓库 |
| 双语 CLI | `--lang zh|en` 切换界面文案、错误与统计 |
| 语料实证 | 风格卡片基于 2856 条真实消息的语言学调研蒸馏 |

## 架构

```text
┌──────────────────────────────┐
│  DeepSeek conversations.json  │
│  （网页导出）                   │
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  deepseek_to_ucs.py           │
│  fragments[] → content.parts[]│
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  ChatGPT mapping JSON         │
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  ucs-parser                   │
│  7 维度画像蒸馏                │
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  UCS profile                  │
│  （my_profile.ucs.json）       │
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  风格卡片.md +                 │
│  injection_snippets.md        │
│  （手工 Agent 规则）            │
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  Agent 适配                    │
└──────────────────────────────┘
```

## 快速安装

需要 Python 3.10+。

```bash
git clone https://github.com/SunshineMahiru/deepseek-ucs-adapter.git
cd deepseek-ucs-adapter

# 安装用于生成画像的 UCS 解析器
pip install ucs-parser
```

## 快速开始

```bash
# 1. 从 DeepSeek 网页版导出 conversations.json（系统设置 -> 数据管理 -> 导出全部）
# 2. 转换为 ChatGPT mapping 格式（位置参数：输入 输出）
python deepseek_to_ucs.py conversations.json conversations_chatgpt.json

# 3. 生成 UCS 画像
python -c "from ucs_parser import Parser; r = Parser(source='chatgpt').from_file('conversations_chatgpt.json'); r.to_json('my_profile.ucs.json'); print(r.fidelity_report())"
```

Fidelity 报告会打印各维度分数（Persona、Interaction patterns 等）。

## 用法

`deepseek_to_ucs.py` 支持以下参数：

| 参数 | 说明 |
|---|---|
| `--verbose` | 打印每个会话的转换统计 |
| `--dry-run` | 只解析和统计，不写文件 |
| `--limit N` | 只转换前 N 个会话 |
| `--merge-think` / `--no-merge-think` | 保留或丢弃 THINK 推理片段（默认保留） |
| `--lang zh|en` | 切换界面语言（默认 zh） |

脱敏输出示例见 `examples/sample_profile.json`（领域名替换为 `Domain A–J`，日期模糊化）。真实画像因含个人领域与时间信息，不入库。

## 数据获取

DeepSeek 网页版 → **系统设置** → **数据管理** → **导出所有历史对话** → 解压得到 `conversations.json`。

导出的 `conversations.json` 含你的全部对话原文，切勿提交到公开仓库。本项目 `.gitignore` 已默认排除 `conversations*.json`。

## 调研发现

基于 2856 条 user 消息的实证（详见 `调研与方案设计.md`）：

- 结构绑定显式纠正标记（"你理解错了"之类）：0–2 次
- 显式重答（"重新回答"）：6–7 次
- 90% 的"含转折词"候选消息是全新需求，0% 是显式纠正

结论：ucs-parser 的 `corrections` 概念对"中文分析型协作"场景结构性稀缺，继续优化正则边际收益极低，故转向手工风格卡片规则。

## 局限性

- ucs-parser v0.1.0 仅支持 ChatGPT 格式，需先经本脚本转换
- Interaction patterns 对中文数据有结构性上限（实测 0.41），因显式纠正信号稀缺
- `language_preferences` 被 ucs-parser 硬编码为 `"en"`，不支持中文检测
- 转换脚本覆盖八种实测 DeepSeek fragment 类型；若 DeepSeek 未来新增类型，需相应扩展

## 许可证

[MIT](./LICENSE)

## 致谢

- [ucs-parser](https://github.com/XwhyZ-WHYLD/universal-cognitive-schema) — Universal Cognitive Schema 参考实现
- [ChatInsights](https://github.com/CouriusDeveloper/ChatInsights) — DeepSeek fragments 解析参考
- [ai-conversation-exporter](https://github.com/toke648/chatgpt_to_md) — fragments 处理参考
