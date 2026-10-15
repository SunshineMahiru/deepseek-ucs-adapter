# DeepSeek → UCS 转换器

把 DeepSeek 网页版导出的 `conversations.json` 转换为 `ucs-parser` 可解析的 ChatGPT mapping 格式。

## 背景

DeepSeek 导出虽然是 mapping 树结构，但消息内容存放在 `message.fragments[]` 中（实测共 8 种 fragment 类型），
而 ucs-parser v0.1.0 只读三个字段：`message.author.role`、`message.content.parts[]`、`message.create_time`。
直接喂 DeepSeek 导出会得到 Persona 0.30、Interaction 0.10 的极低分。

## 交付物

| 文件 | 说明 |
|---|---|
| `sunshinemahiru_deepseek_to_ucs.py` | 转换脚本（仅标准库，确定性 UUID v4 + current_node + 旁路重挂 + FILE 文件名 + THINK 可开关） |
| `风格卡片.md` | 手工交互模式卡片（供 Agent 阅读） |
| `injection_snippets.md` | 风格卡片注入模板（Hermes SOUL.md / Claude Code / DSH Profile） |
| `调研与方案设计.md` | 调研报告 + 方案设计 |
| `examples/sample_profile.json` | 脱敏示例画像（见下方「示例输出」） |
| `analyze_deepseek.py` | DeepSeek fragment 结构分析脚本（只读） |
| `research_fix_analysis.py` | 中文修复行为语言学调研脚本（只读） |

## 用法

```bash
# 1. 转换
python sunshinemahiru_deepseek_to_ucs.py conversations.json conversations_chatgpt.json

# 2. 解析 + 看 fidelity（用 editable install 的官方 ucs-parser）
D:/appInstall/Python312/python.exe -c "from ucs_parser import Parser; r=Parser(source='chatgpt').from_file('conversations_chatgpt.json'); print(r.fidelity_report())"
```

转换脚本额外参数：`--verbose`、`--dry-run`、`--limit N`、`--merge-think/--no-merge-think`。

## 示例输出

`examples/sample_profile.json` 是从真实画像 `my_profile.ucs.json` 派生的**脱敏示例**，仅用于展示 UCS profile 的输出结构：
- `expertise_map.domains` 的领域名已替换为 `Domain A`、`Domain B`…（保持数量 10）
- `temporal_context` 的日期与消息量已模糊化（`2026-XX`）
- `persona.communication_style` 等泛化数值保留

真实画像（含个人领域与时间信息）不进入仓库，存放在 `G:\Tenshisama Code\_my_deepseek_data\my_profile.ucs.json`。

## 转换后 Fidelity（clean baseline，官方 ucs-parser）

| 维度 | 分数 |
|---|---|
| Persona | 0.99 |
| Interaction patterns | 0.41 |
| Overall | 0.49 |

## 调研结论：为什么不继续优化中文正则

基于 2856 条 user 消息的实测：

- 结构绑定真纠正标记（并不 / 又不是 / 我的意思是 / 你理解错了）：**0–2 次**
- 显式纠正（重新回答）：**6–7 次**
- 90% 的"含转折词"候选消息是全新需求，**0% 是显式纠正**
- 结论：ucs-parser 的 corrections 概念在该语料中**结构性稀缺**，继续优化正则的
  边际收益极低，转向风格卡片手工规则。

**保留的 parser 改动：无**（fork 已回退到 clean baseline，保持官方行为，上游更新无冲突）。

## 附：6 条中文正则实验记录（已回退）

实验曾把 Interaction patterns 推到 0.60，但样本审查证明是虚高（③⑥ 靠引文/裸词误匹配堆上去）。

| # | 正则 | 匹配数 | 真纠正 | 误匹配 |
|---|---|---|---|---|
| ① | `太(长\|短\|啰嗦\|简\|复杂)了?` | 4 | 0 | 4（引文里的"太复杂/太简单"） |
| ② | `(精简\|缩短\|简洁\|详细\|展开\|具体)(一)?(点\|些\|下)` | 0 | 0 | 0（语料用裸词，无"点/些/下"后缀，一条未中） |
| ③ | `不对\|错了\|不是(这\|那)样` | 70 | 少数（"答案都不对""选D也不对"） | 大量（引文、"为什么不对香烟"、"不是…样"随笔） |
| ④ | `重新(来\|做\|写\|回答)` | 9 | ~6（"请你重新回答"） | ~3（"重新来潮""简历重新做""重新写"创作） |
| ⑤ | `不要(用\|写\|加\|说\|再)` | 5 | ~1（"不要再提及"） | 4（"不要用markdown"是首次请求非纠正、引文） |
| ⑥ | `(去掉\|删掉\|删除\|移除)` | 43 | 极少数（"去掉不必要的文字"） | 绝大多数（代码/数据库/考试语境的"删除"） |

唯一可靠的是 ④「重新回答」，已并入 `风格卡片.md` 第 5 条作为最高优先级规则。
