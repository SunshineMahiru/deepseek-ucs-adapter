import contextlib
import io
import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import deepseek_to_ucs as d2u

TMP = ROOT / ".test-tmp"


def empty_stats() -> dict:
    return {
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


def make_node(nid, parent, children, fragments=None, when="2026-10-15T10:00:01+08:00"):
    message = None if fragments is None else {"inserted_at": when, "fragments": fragments}
    return {"id": nid, "parent": parent, "children": children, "message": message}


def make_conv(mapping, cid="c1", title="sample", when="2026-10-15T10:00:00+08:00"):
    return {
        "id": cid,
        "title": title,
        "inserted_at": when,
        "updated_at": when,
        "mapping": mapping,
    }


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = d2u.main(argv)
    return code, out.getvalue(), err.getvalue()


class ConvertTest(unittest.TestCase):
    def setUp(self):
        d2u.set_lang("zh")

    def test_basic_round(self):
        mapping = {
            "root": make_node("root", None, ["u"]),
            "u": make_node("u", "root", ["a"], [{"type": "REQUEST", "content": "问题"}]),
            "a": make_node("a", "u", [], [
                {"type": "THINK", "content": "推理"},
                {"type": "RESPONSE", "content": "回答"},
            ], when="2026-10-15T10:00:02+08:00"),
        }
        stats = empty_stats()
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats)

        self.assertIsNotNone(result)
        self.assertEqual(len(result["mapping"]), 3)
        roles = {
            n["message"]["author"]["role"]: n["message"]["content"]["parts"]
            for n in result["mapping"].values() if n["message"]
        }
        self.assertEqual(roles["user"], ["问题"])
        self.assertEqual(roles["assistant"], ["[REASONING] 推理", "回答"])
        self.assertEqual(stats["message_nodes"], 2)
        self.assertEqual(stats["skipped_nodes"], 0)

    def test_current_node_prefers_the_newest_leaf(self):
        mapping = {
            "root": make_node("root", None, ["u"]),
            "u": make_node("u", "root", ["late", "early"], [{"type": "REQUEST", "content": "q"}]),
            "late": make_node("late", "u", [], [{"type": "RESPONSE", "content": "更晚"}],
                              when="2026-10-15T10:09:00+08:00"),
            "early": make_node("early", "u", [], [{"type": "RESPONSE", "content": "更早"}],
                               when="2026-10-15T10:02:00+08:00"),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        current = result["mapping"][result["current_node"]]
        self.assertEqual(current["message"]["content"]["parts"], ["更晚"])

    def test_current_node_falls_back_to_the_last_leaf_without_timestamps(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": {"id": "a", "parent": "root", "children": ["b"],
                  "message": {"fragments": [{"type": "RESPONSE", "content": "第一"}]}},
            "b": {"id": "b", "parent": "a", "children": [],
                  "message": {"fragments": [{"type": "RESPONSE", "content": "第二"}]}},
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        current = result["mapping"][result["current_node"]]
        self.assertEqual(current["message"]["content"]["parts"], ["第二"])

    def test_think_can_be_dropped(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "THINK", "content": "推理"},
                {"type": "RESPONSE", "content": "回答"},
            ]),
        }
        stats = empty_stats()
        result = d2u.convert_conversation(make_conv(mapping), False, lambda m: None, stats)
        parts = [n["message"]["content"]["parts"] for n in result["mapping"].values() if n["message"]]
        self.assertEqual(parts, [["回答"]])
        self.assertEqual(stats["frag_THINK_dropped"], 1)

    def test_lowercase_fragment_type_is_user(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [{"type": "request", "content": "hi"}]),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        msg = [n["message"] for n in result["mapping"].values() if n["message"]][0]
        self.assertEqual(msg["author"]["role"], "user")

    def test_file_only_node_is_user(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "FILE", "files": [{"file_name": "screenshot.webp"}]},
            ]),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        msg = [n["message"] for n in result["mapping"].values() if n["message"]][0]
        self.assertEqual(msg["author"]["role"], "user")

    def test_file_with_response_stays_assistant(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "FILE", "files": [{"file_name": "out.pdf"}]},
                {"type": "RESPONSE", "content": "生成好了"},
            ]),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        msg = [n["message"] for n in result["mapping"].values() if n["message"]][0]
        self.assertEqual(msg["author"]["role"], "assistant")

    def test_missing_parent_becomes_root(self):
        mapping = {
            "root": make_node("root", None, []),
            "a": make_node("a", "ghost", [], [{"type": "REQUEST", "content": "hi"}]),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        self.assertEqual(len(result["mapping"]), 2)
        for node in result["mapping"].values():
            if node["message"]:
                self.assertIsNone(node["parent"])

    def test_cyclic_parent_terminates(self):
        mapping = {
            "root": make_node("root", None, ["x"]),
            "x": make_node("x", "y", ["a"], []),
            "y": make_node("y", "x", [], []),
            "a": make_node("a", "x", [], [{"type": "REQUEST", "content": "hi"}]),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        self.assertIsNotNone(result)

    def test_non_dict_root_skips_conversation(self):
        mapping = {"root": "broken", "a": make_node("a", "root", [], [{"type": "REQUEST", "content": "hi"}])}
        stats = empty_stats()
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats)
        self.assertIsNone(result)
        self.assertEqual(stats["skipped_conversations"], 1)

    def test_non_dict_message_skips_node(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": {"id": "a", "parent": "root", "children": [], "message": "broken"},
        }
        stats = empty_stats()
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats)
        self.assertEqual(len(result["mapping"]), 1)
        self.assertEqual(stats["skipped_nodes"], 1)

    def test_root_with_non_dict_message_stays_root(self):
        mapping = {
            "root": {"id": "root", "parent": None, "children": ["a"], "message": "broken"},
            "a": make_node("a", "root", [], [{"type": "REQUEST", "content": "hi"}]),
        }
        stats = empty_stats()
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats)
        self.assertIsNotNone(result)
        self.assertEqual(len(result["mapping"]), 2)
        roots = [n for n in result["mapping"].values() if n["message"] is None]
        self.assertEqual(len(roots), 1)
        self.assertEqual(stats["skipped_nodes"], 0)

    def test_null_placeholder_is_bypassed(self):
        mapping = {
            "root": make_node("root", None, ["p"]),
            "p": make_node("p", "root", ["a"]),
            "a": make_node("a", "p", [], [{"type": "REQUEST", "content": "hi"}]),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        roots = [n for n in result["mapping"].values() if n["parent"] is None]
        self.assertEqual(len(roots), 1)
        self.assertEqual(len(result["mapping"]), 2)

    def test_file_fragment_keeps_names(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "FILE", "files": [{"file_name": "报告.pdf"}]},
                {"type": "REQUEST", "content": "看这个"},
            ]),
        }
        stats = empty_stats()
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats)
        parts = [n["message"]["content"]["parts"] for n in result["mapping"].values() if n["message"]][0]
        self.assertIn("[文件] 报告.pdf", parts)
        self.assertEqual(stats["frag_FILE"], 1)

    def test_non_list_files_counts_as_skipped(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "FILE", "files": {"file_name": "x.pdf"}},
                {"type": "REQUEST", "content": "看这个"},
            ]),
        }
        stats = empty_stats()
        d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats)
        self.assertEqual(stats["frag_FILE"], 0)
        self.assertEqual(stats["skipped_fragments"], 1)

    def test_tool_and_unknown_fragments_are_counted_apart(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "TOOL_OPEN"},
                {"type": "FUTURE_TYPE", "content": "x"},
                {"type": "RESPONSE", "content": "回答"},
            ]),
        }
        stats = empty_stats()
        d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats)
        self.assertEqual(stats["frag_tool"], 1)
        self.assertEqual(stats["frag_unknown"], 1)

    def test_tool_results_are_dropped_by_default(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "TOOL_SEARCH", "results": "[{'title': '示例', 'url': 'https://example.com'}]"},
                {"type": "RESPONSE", "content": "回答"},
            ]),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats())
        parts = [n["message"]["content"]["parts"] for n in result["mapping"].values() if n["message"]][0]
        self.assertEqual(parts, ["回答"])

    def test_tool_results_can_be_kept(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "TOOL_SEARCH", "results": "[{'title': '示例', 'url': 'https://example.com'}]"},
                {"type": "RESPONSE", "content": "回答"},
            ]),
        }
        stats = empty_stats()
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats, True)
        parts = [n["message"]["content"]["parts"] for n in result["mapping"].values() if n["message"]][0]
        self.assertIn("[工具] 示例 — https://example.com", parts)
        self.assertEqual(stats["frag_tool"], 1)

    def test_unparsable_tool_results_are_skipped(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "SEARCH", "results": "{not a literal"},
                {"type": "RESPONSE", "content": "回答"},
            ]),
        }
        stats = empty_stats()
        warnings = []
        result = d2u.convert_conversation(make_conv(mapping), True, warnings.append, stats, True)
        parts = [n["message"]["content"]["parts"] for n in result["mapping"].values() if n["message"]][0]
        self.assertEqual(parts, ["回答"])
        self.assertEqual(stats["skipped_fragments"], 1)
        self.assertEqual(len(warnings), 1)

    def test_tool_results_accept_a_list(self):
        mapping = {
            "root": make_node("root", None, ["a"]),
            "a": make_node("a", "root", [], [
                {"type": "TOOL_SEARCH", "results": [{"title": "只有标题"}]},
                {"type": "RESPONSE", "content": "回答"},
            ]),
        }
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, empty_stats(), True)
        parts = [n["message"]["content"]["parts"] for n in result["mapping"].values() if n["message"]][0]
        self.assertIn("[工具] 只有标题", parts)

    def test_root_with_empty_parts_is_counted_once(self):
        mapping = {
            "root": make_node("root", None, [], [{"type": "TOOL_OPEN"}]),
        }
        stats = empty_stats()
        result = d2u.convert_conversation(make_conv(mapping), True, lambda m: None, stats)
        self.assertEqual(len(result["mapping"]), 1)
        self.assertEqual(stats["skipped_nodes"], 0)
        self.assertEqual(stats["message_nodes"], 1)


class TimeTest(unittest.TestCase):
    def test_offset_timestamp(self):
        self.assertEqual(d2u.to_unix("2026-10-15T10:00:00+08:00"), 1792029600.0)

    def test_z_suffix(self):
        self.assertEqual(d2u.to_unix("2026-10-15T02:00:00Z"), 1792029600.0)

    def test_naive_is_utc(self):
        self.assertEqual(d2u.to_unix("2026-10-15T02:00:00"), 1792029600.0)

    def test_missing_returns_none(self):
        self.assertIsNone(d2u.to_unix(None))
        self.assertIsNone(d2u.to_unix(""))
        self.assertIsNone(d2u.to_unix("not-a-date"))


class UuidTest(unittest.TestCase):
    def test_deterministic(self):
        first = d2u.deterministic_uuid4("conv:node")
        second = d2u.deterministic_uuid4("conv:node")
        self.assertEqual(first, second)
        self.assertEqual(first[14], "4")


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)
        d2u.set_lang("zh")

    def write_json(self, name, payload, encoding="utf-8"):
        path = TMP / name
        with open(path, "w", encoding=encoding) as f:
            json.dump(payload, f, ensure_ascii=False)
        return path

    def sample_conversations(self, count=3):
        items = []
        for i in range(count):
            mapping = {
                "root": make_node("root", None, ["a"]),
                "a": make_node("a", "root", [], [{"type": "REQUEST", "content": f"msg{i}"}]),
            }
            items.append(make_conv(mapping, cid=f"c{i}"))
        return items

    def test_round_trip_writes_output(self):
        src = self.write_json("in.json", self.sample_conversations())
        dst = TMP / "out.json"
        code, out, _ = run_main([str(src), str(dst), "--lang", "en"])
        self.assertEqual(code, 0)
        with open(dst, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data), 3)
        self.assertEqual(data[0]["title"], "sample")
        self.assertTrue((TMP / "out.json.log").exists())

    def test_bom_input_is_accepted(self):
        src = self.write_json("bom.json", self.sample_conversations(1), encoding="utf-8-sig")
        code, _, _ = run_main([str(src), str(TMP / "bom_out.json")])
        self.assertEqual(code, 0)

    def test_negative_limit_is_rejected(self):
        src = self.write_json("lim.json", self.sample_conversations())
        code, _, err = run_main([str(src), str(TMP / "lim_out.json"), "--limit", "-1", "--lang", "en"])
        self.assertEqual(code, 2)
        self.assertIn("--limit", err)

    def test_unrelated_json_is_rejected(self):
        src = self.write_json("junk.json", {"hello": "world"})
        code, _, err = run_main([str(src), str(TMP / "junk_out.json"), "--lang", "en"])
        self.assertEqual(code, 2)
        self.assertIn("neither a conversation array", err)

    def test_all_skipped_returns_three(self):
        src = self.write_json("skip.json", [{"foo": "bar"}])
        code, _, err = run_main([str(src), str(TMP / "skip_out.json"), "--lang", "en"])
        self.assertEqual(code, 3)
        self.assertIn("empty array", err)

    def test_dry_run_writes_nothing(self):
        src = self.write_json("dry.json", self.sample_conversations(1))
        dst = TMP / "dry_out.json"
        code, _, _ = run_main([str(src), str(dst), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertFalse(dst.exists())


if __name__ == "__main__":
    unittest.main()
