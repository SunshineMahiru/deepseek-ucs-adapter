# deepseek-ucs-adapter

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)
[![Status](https://img.shields.io/badge/Status-stable-brightgreen)]()

> Convert DeepSeek web-export conversations.json into a portable UCS (Universal Cognitive Schema) user profile. Rewrites DeepSeek's `fragments[]` into the ChatGPT `content.parts[]` mapping ucs-parser understands. Privacy-first: raw conversations stay out of the repo. Includes a style card and injection templates for Hermes / Claude Code / DSH.

## 解决什么问题

DeepSeek 网页版导出的 `conversations.json` 虽然也是 `mapping` 树结构，但消息内容存放在 `message.fragments[]` 中（实测共 8 种 fragment 类型：REQUEST / RESPONSE / THINK / TOOL_OPEN / TOOL_SEARCH / TOOL_FIND / SEARCH / FILE）。

而 [ucs-parser](https://github.com/XwhyZ-WHYLD/universal-cognitive-schema)（Universal Cognitive Schema 参考实现）只读取 ChatGPT 格式的三个字段——`message.author.role`、`message.content.parts[]`、`message.create_time`。直接把 DeepSeek 导出喂进去，会得到 Persona 0.30、Interaction 0.10 的极低分，画像几乎为空。

本项目做两件事：

1. **格式转换**：把 DeepSeek 的 `fragments[]` 无损转换为 ChatGPT 的 `content.parts[]`，让 ucs-parser 能正确提取画像。
2. **风格蒸馏**：基于 2856 条真实 user 消息的语言学调研，产出手工风格卡片 + 三种 Agent 注入模板，让画像真正落地到 Agent 行为。

## 特性

- ✅ DeepSeek fragments → ChatGPT mapping 无损转换（8 种 fragment 类型全处理）
- ✅ 确定性 UUID v4（可 diff、可复现、幂等）
- ✅ 隐私保护：原始对话数据物理隔离，仓库不含任何真实对话内容
- ✅ 中文语料实证：基于 2856 条 user 消息的修复行为调研
- ✅ 提供 3 种 Agent 注入模板（Hermes / Claude Code / DSH）
- ⚠️ Interaction patterns 维度对中文数据有结构性上限（见「局限性」）

## 快速开始

### 安装

```bash
# 只需 Python 3.10+
git clone https://github.com/sunshinemahiru/deepseek-ucs-adapter.git
cd deepseek-ucs-adapter

# 安装 UCS 解析器（用于生成画像）
pip install ucs-parser
```

### 使用

```bash
# 1. 从 DeepSeek 网页版导出 conversations.json（见下方「数据获取」）
# 2. 转换为 ChatGPT mapping 格式（位置参数：输入 输出）
python sunshinemahiru_deepseek_to_ucs.py \
  /path/to/conversations.json \
  /path/to/conversations_chatgpt.json

# 3. 生成 UCS 画像
python -c "
from ucs_parser import Parser
r = Parser(source='chatgpt').from_file('conversations_chatgpt.json')
r.to_json('my_profile.ucs.json')
print(r.fidelity_report())
"
```

转换脚本额外参数：`--verbose`、`--dry-run`、`--limit N`、`--merge-think/--no-merge-think`。

### 输出示例

见 `examples/sample_profile.json`（脱敏示例：领域名替换为 `Domain A–J`，日期模糊化）。真实画像因含个人领域与时间信息，不入库。

## 数据获取

DeepSeek 网页版 → **系统设置** → **数据管理** → **导出所有历史对话** → 解压得到 `conversations.json`。

> 隐私提示：导出的 `conversations.json` 含你的全部对话原文，切勿提交到公开仓库。本项目 `.gitignore` 已默认排除 `conversations*.json`。

## 架构

```
DeepSeek conversations.json
        ↓
[sunshinemahiru_deepseek_to_ucs.py]   # fragments[] → content.parts[]
        ↓
ChatGPT mapping format
        ↓
[ucs-parser]                          # 7 维度画像蒸馏
        ↓
UCS profile (my_profile.ucs.json)
        ↓
[风格卡片.md + injection_snippets.md] # 手工规则 + 注入模板
        ↓
Agent 适配
```

## 项目结构

```
.
├── sunshinemahiru_deepseek_to_ucs.py   # 核心转换脚本（仅标准库）
├── 风格卡片.md                          # 手工交互模式卡片
├── injection_snippets.md               # 3 种 Agent 注入模板
├── 调研与方案设计.md                     # 调研报告 + 方案设计
├── examples/
│   └── sample_profile.json             # 脱敏示例输出
├── analyze_deepseek.py                 # DeepSeek 结构分析脚本
├── research_fix_analysis.py            # 中文修复行为调研脚本
└── README.md
```

## 调研发现

基于 2856 条 user 消息的实证（详见 `调研与方案设计.md`）：

- 结构绑定真纠正标记（并不 / 又不是 / 我的意思是 / 你理解错了）：**0–2 次**
- 显式重答（"重新回答"）：**6–7 次**
- 90% 的"含转折词"候选消息是全新需求，**0% 是显式纠正**

**结论**：ucs-parser 的 `corrections` 概念对"中文分析型协作"场景结构性稀缺，继续优化正则边际收益极低，故转向手工风格卡片规则。

## 局限性

- ucs-parser v0.1.0 仅支持 ChatGPT 格式，需先经本脚本转换
- Interaction patterns 对中文数据有结构性上限（实测 0.41），因显式纠正信号稀缺
- `language_preferences` 被 ucs-parser 硬编码为 `"en"`，不支持中文检测
- 转换脚本覆盖 DeepSeek fragments 的 8 种实测类型；若 DeepSeek 未来新增类型，需相应扩展

## 许可证

[MIT](./LICENSE)

## 致谢

- [ucs-parser](https://github.com/XwhyZ-WHYLD/universal-cognitive-schema) — Universal Cognitive Schema 参考实现
- [ChatInsights](https://github.com/CouriusDeveloper/ChatInsights) — DeepSeek fragments 解析参考
- [ai-conversation-exporter](https://github.com/toke648/chatgpt_to_md) — fragments 处理参考
