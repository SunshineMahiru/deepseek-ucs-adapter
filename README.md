# deepseek-ucs-adapter

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)
[![Status](https://img.shields.io/badge/Status-stable-brightgreen)]()

[English](./README.md) | [中文](./README.zh-CN.md)

> Convert DeepSeek web-export conversations.json into a portable UCS (Universal Cognitive Schema) user profile. Rewrites DeepSeek's `fragments[]` into the ChatGPT `content.parts[]` mapping ucs-parser understands. Privacy-first: raw conversations stay out of the repo. Includes a style card and injection templates for Hermes / Claude Code / DSH.

## What problem does this solve?

DeepSeek's web export (`conversations.json`) is a `mapping` tree, but message content lives in `message.fragments[]` (8 fragment types observed in practice: REQUEST / RESPONSE / THINK / TOOL_OPEN / TOOL_SEARCH / TOOL_FIND / SEARCH / FILE).

[ucs-parser](https://github.com/XwhyZ-WHYLD/universal-cognitive-schema) (the Universal Cognitive Schema reference implementation) only reads three ChatGPT-format fields — `message.author.role`, `message.content.parts[]`, `message.create_time`. Feed it a DeepSeek export directly and you get Persona 0.30, Interaction 0.10 — an almost-empty profile.

This project does two things:

1. **Format conversion**: losslessly rewrite DeepSeek `fragments[]` into ChatGPT `content.parts[]` so ucs-parser can extract a real profile.
2. **Style distillation**: based on a linguistic study of 2,856 real user messages, ship a hand-crafted style card plus three Agent injection templates that turn the profile into concrete Agent behaviour.

## Features

- ✅ Lossless DeepSeek fragments → ChatGPT mapping conversion (all 8 fragment types handled)
- ✅ Deterministic UUID v4 (diff-able, reproducible, idempotent)
- ✅ Privacy-first: raw conversation data is physically excluded from the repo
- ✅ Chinese-corpus empirical grounding: repair-behaviour study over 2,856 user messages
- ✅ 3 Agent injection templates (Hermes / Claude Code / DSH)
- ✅ Bilingual CLI: `--lang zh|en`
- ⚠️ Interaction patterns has a structural ceiling for Chinese data (see Limitations)

## Quick start

### Installation

```bash
# Requires Python 3.10+
git clone https://github.com/SunshineMahiru/deepseek-ucs-adapter.git
cd deepseek-ucs-adapter

# Install the UCS parser (to generate the profile)
pip install ucs-parser
```

### Usage

```bash
# 1. Export conversations.json from the DeepSeek web app (see "Getting your data")
# 2. Convert to ChatGPT mapping format (positional args: input output)
python deepseek_to_ucs.py \
  /path/to/conversations.json \
  /path/to/conversations_chatgpt.json

# 3. Generate the UCS profile
python -c "
from ucs_parser import Parser
r = Parser(source='chatgpt').from_file('conversations_chatgpt.json')
r.to_json('my_profile.ucs.json')
print(r.fidelity_report())
"
```

Extra flags: `--verbose`, `--dry-run`, `--limit N`, `--merge-think/--no-merge-think`, `--lang zh|en` (English UI: `--lang en`).

### Sample output

See `examples/sample_profile.json` (sanitized sample: domain names replaced with `Domain A–J`, dates fuzzed). The real profile is not committed because it contains personal domains and timestamps.

## Getting your data

DeepSeek web app → **Settings** → **Data management** → **Export all conversations** → unzip to get `conversations.json`.

> Privacy note: the exported `conversations.json` contains your full conversation text — never commit it to a public repo. This repo's `.gitignore` excludes `conversations*.json` by default.

## Architecture

```
DeepSeek conversations.json
        ↓
[deepseek_to_ucs.py]   # fragments[] → content.parts[]
        ↓
ChatGPT mapping format
        ↓
[ucs-parser]                          # 7-dimension profile distillation
        ↓
UCS profile (my_profile.ucs.json)
        ↓
[风格卡片.md + injection_snippets.md] # hand-crafted rules + injection templates
        ↓
Agent adaptation
```

## Project structure

```
.
├── deepseek_to_ucs.py   # core converter (stdlib only)
├── 风格卡片.md                          # hand-crafted interaction style card
├── injection_snippets.md               # 3 Agent injection templates
├── 调研与方案设计.md                     # research report + design
├── examples/
│   └── sample_profile.json             # sanitized sample output
├── analyze_deepseek.py                 # DeepSeek structure analysis script
├── research_fix_analysis.py            # repair-behaviour research script
├── README.md                           # this file (English)
├── README.zh-CN.md                     # Chinese version
├── .gitattributes                      # line-ending normalization
├── .gitignore                          # exclude private data & caches
└── LICENSE
```

## Research findings

Empirical results over 2,856 user messages (see `调研与方案设计.md`):

- Structure-bound explicit correction markers ("you misunderstood me", etc.): **0–2 occurrences**
- Explicit redo ("重新回答" / "answer again"): **6–7 occurrences**
- 90% of "turn-taking" candidates are brand-new requests, **0% are explicit corrections**

**Conclusion**: ucs-parser's `corrections` concept is structurally scarce in a Chinese analytical-collaboration corpus, so further regex tuning has marginal value. This project ships a hand-crafted style card instead.

## Limitations

- ucs-parser v0.1.0 only accepts ChatGPT format — conversion is required first
- Interaction patterns has a structural ceiling for Chinese data (measured 0.41), due to scarce explicit correction signals
- `language_preferences` is hardcoded to `"en"` by ucs-parser, with no Chinese detection
- The converter covers the 8 observed DeepSeek fragment types; new types will need extension

## License

[MIT](./LICENSE)

## Acknowledgements

- [ucs-parser](https://github.com/XwhyZ-WHYLD/universal-cognitive-schema) — Universal Cognitive Schema reference implementation
- [ChatInsights](https://github.com/CouriusDeveloper/ChatInsights) — DeepSeek fragments parsing reference
- [ai-conversation-exporter](https://github.com/toke648/chatgpt_to_md) — fragments handling reference
