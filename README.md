<h1 align="center">deepseek-ucs-adapter</h1>

<p align="center">Convert DeepSeek chat exports into portable UCS user profiles, privacy-first and bilingual.</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square" alt="Python 3.10+">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-22C55E?style=flat-square" alt="License: MIT"></a>
</p>

## What problem does this solve?

DeepSeek's web export (`conversations.json`) is a `mapping` tree, but message content lives in `message.fragments[]` — eight fragment types observed in practice: `REQUEST`, `RESPONSE`, `THINK`, `TOOL_OPEN`, `TOOL_SEARCH`, `TOOL_FIND`, `SEARCH`, `FILE`.

[ucs-parser](https://github.com/XwhyZ-WHYLD/universal-cognitive-schema), the Universal Cognitive Schema reference implementation, reads only three ChatGPT-format fields: `message.author.role`, `message.content.parts[]`, and `message.create_time`. Feed it a DeepSeek export directly and you get Persona 0.30 and Interaction 0.10 — an almost-empty profile.

This project does two things:

1. **Format conversion** — losslessly rewrite DeepSeek `fragments[]` into ChatGPT `content.parts[]` so ucs-parser can extract a real profile.
2. **Style distillation** — from a linguistic study of 2,856 real user messages, ship a hand-crafted style card plus three Agent injection templates that turn the profile into concrete Agent behaviour.

## Highlights

| Highlight | Why it matters |
|---|---|
| Lossless fragments → parts conversion | Handles all eight observed DeepSeek fragment types so ucs-parser extracts a real profile |
| Deterministic UUID v4 | Reproducible, diff-able output across runs |
| Privacy-first design | Raw conversation data is physically excluded from the repository |
| Bilingual CLI | `--lang zh|en` switches UI text, errors, and statistics |
| Empirical grounding | Style card distilled from a linguistic study of 2,856 real messages |

## Architecture

```text
┌──────────────────────────────┐
│  DeepSeek conversations.json  │
│  (web export)                 │
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
│  7-dimension distillation     │
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  UCS profile                  │
│  (my_profile.ucs.json)        │
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  风格卡片.md +                 │
│  injection_snippets.md        │
│  (hand-crafted Agent rules)   │
└───────────────┬──────────────┘
                ▼
┌──────────────────────────────┐
│  Agent adaptation             │
└──────────────────────────────┘
```

## Quick Install

Requires Python 3.10+.

```bash
git clone https://github.com/SunshineMahiru/deepseek-ucs-adapter.git
cd deepseek-ucs-adapter

# Install the UCS parser used to generate the profile
pip install ucs-parser
```

## Quick Start

```bash
# 1. Export conversations.json from the DeepSeek web app (Settings -> Data management -> Export all)
# 2. Convert to ChatGPT mapping format (positional arguments: input, output)
python deepseek_to_ucs.py conversations.json conversations_chatgpt.json

# 3. Generate the UCS profile
python -c "from ucs_parser import Parser; r = Parser(source='chatgpt').from_file('conversations_chatgpt.json'); r.to_json('my_profile.ucs.json'); print(r.fidelity_report())"
```

The Fidelity report prints per-dimension scores (Persona, Interaction patterns, and others).

## Usage

`deepseek_to_ucs.py` accepts the following flags:

| Flag | Description |
|---|---|
| `--verbose` | Print per-conversation statistics |
| `--dry-run` | Parse and report without writing files |
| `--limit N` | Convert only the first N conversations |
| `--merge-think` / `--no-merge-think` | Keep or drop THINK reasoning fragments (default: keep) |
| `--lang zh|en` | Switch UI language (default: zh) |

See `examples/sample_profile.json` for a sanitized output sample (domain names replaced with `Domain A–J`, dates fuzzed). The real profile is not committed because it contains personal domains and timestamps.

## Getting your data

DeepSeek web app → **Settings** → **Data management** → **Export all conversations** → unzip to get `conversations.json`.

The exported file contains your full conversation text; never commit it to a public repository. This repo's `.gitignore` excludes `conversations*.json` by default.

## Research findings

Empirical results over 2,856 user messages (details in `调研与方案设计.md`):

- Structure-bound explicit correction markers ("you misunderstood me" and similar): 0–2 occurrences
- Explicit redo ("重新回答"): 6–7 occurrences
- 90% of turn-taking candidates are brand-new requests; 0% are explicit corrections

Conclusion: ucs-parser's `corrections` concept is structurally scarce in a Chinese analytical-collaboration corpus, so further regex tuning has marginal value. This project ships a hand-crafted style card instead.

## Limitations

- ucs-parser v0.1.0 accepts only ChatGPT format, so conversion is required first
- Interaction patterns has a structural ceiling for Chinese data (measured 0.41), due to scarce explicit correction signals
- `language_preferences` is hardcoded to `"en"` by ucs-parser, with no Chinese detection
- The converter covers the eight observed DeepSeek fragment types; new types will need extension

## License

[MIT](./LICENSE)

## Acknowledgements

- [ucs-parser](https://github.com/XwhyZ-WHYLD/universal-cognitive-schema) — Universal Cognitive Schema reference implementation
- [ChatInsights](https://github.com/CouriusDeveloper/ChatInsights) — DeepSeek fragments parsing reference
- [ai-conversation-exporter](https://github.com/toke648/chatgpt_to_md) — fragments handling reference
