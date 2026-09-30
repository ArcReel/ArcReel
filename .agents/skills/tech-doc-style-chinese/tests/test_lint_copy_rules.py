from __future__ import annotations

import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINTER = runpy.run_path(
    str(ROOT / "scripts" / "lint_copy_rules.py"),
    run_name="lint_copy_rules_test",
)
scan_markdown = LINTER["scan_markdown"]


class CopyLintRulesTest(unittest.TestCase):
    def test_multiline_inline_code_keeps_outside_diagnostics(self):
        for code in ('`中文\n阀值`', '``中文 `\n阀值``'):
            with self.subTest(code=code):
                text = '说明 ' + code + '。阀值需要调整。'
                findings = self.scan(text)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].line, 2)
                self.assertEqual(findings[0].col, text.splitlines()[1].rindex('阀值') + 1)

    def test_unclosed_inline_code_does_not_hide_next_paragraph(self):
        findings = self.scan('说明 `未闭合\n\n阀值`。')
        self.assertEqual([(v.line, v.kind) for v in findings], [(3, 'typo')])

    def test_indented_code_after_heading(self):
        for heading in ('# 示例', '## 示例', '示例\n==='):
            with self.subTest(heading=heading):
                text = heading + '\n    阀值 = 1\n\n阀值需要调整。'
                findings = self.scan(text)
                self.assertEqual([(v.line, v.kind) for v in findings],
                                 [(len(text.splitlines()), 'typo')])

    def scan(self, text: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text(text, encoding="utf-8")
            return scan_markdown(path)

    def test_detects_multiword_ai_terms(self):
        findings = self.scan("调用 openai api，并使用 fine tune。")
        messages = [item.message for item in findings]
        self.assertTrue(any("OpenAI API" in message for message in messages))
        self.assertTrue(any("fine-tuning" in message for message in messages))

    def test_masks_single_segment_api_path(self):
        findings = self.scan("调用 /api 返回 json。")
        messages = [item.message for item in findings]
        self.assertFalse(any("「api」" in message for message in messages))
        self.assertTrue(any("JSON" in message for message in messages))

    def test_context_dependent_words_are_warnings(self):
        findings = self.scan("截止日期。登陆月球。按比例配制溶液。制作 H5 页面。")
        self.assertTrue(findings)
        self.assertFalse(any(item.severity == "error" for item in findings))
        self.assertTrue(any(item.kind == "context" for item in findings))
        self.assertTrue(any(item.kind == "abbreviation" for item in findings))

    def test_high_confidence_typo_is_error(self):
        findings = self.scan("请调整阀值，不要布署旧版本。")
        self.assertEqual({item.severity for item in findings}, {"error"})

    def test_ignores_code_urls_paths_and_link_targets(self):
        text = "\n".join(
            [
                "`openai api`",
                "```text",
                "openai api",
                "```",
                "https://example.com/openai/api",
                "[说明](https://example.com/openai/api)",
                "/api",
            ]
        )
        self.assertEqual(self.scan(text), [])

    def test_inline_ignore_marker(self):
        findings = self.scan("阀值 <!-- copy-lint-disable-line -->")
        self.assertEqual(findings, [])

    def test_fence_content_does_not_change_containers(self):
        for prefix in ("", "> "):
            for delimiter in ("```", "~~~"):
                with self.subTest(prefix=prefix, delimiter=delimiter):
                    text = "\n".join(prefix + line for line in (
                        delimiter + "text", "> 阀值", "- 阀值",
                        delimiter, "阀值需要调整。",
                    ))
                    findings = self.scan(text)
                    self.assertEqual([(v.line, v.kind) for v in findings], [(5, "typo")])

    def test_list_fences_preserve_following_list_prose(self):
        for marker, indent in (("- ", "    "), ("1. ", "    "), ("-   ", "    ")):
            for prefix in ("", "> "):
                with self.subTest(marker=marker, prefix=prefix):
                    text = "\n".join(prefix + line for line in (
                        marker + "示例：", "", indent + "```text",
                        indent + "> 阀值", indent + "```", "",
                        indent + "阀值需要调整。",
                    ))
                    findings = self.scan(text)
                    self.assertEqual([(v.line, v.kind) for v in findings], [(7, "typo")])
                    self.assertEqual(findings[0].col, len(prefix + indent) + 1)

    def test_nested_list_and_indented_code(self):
        text = "\n".join((
            "- 外层", "  - 内层", "", "        阀值 = 1", "",
            "    阀值需要调整。", "", "阀值需要调整。",
        ))
        findings = self.scan(text)
        self.assertEqual([(v.line, v.kind) for v in findings], [(6, "typo"), (8, "typo")])

    def test_list_fence_on_marker_line_and_container_exit(self):
        for text, expected in (
            ("- ```text\n  > 阀值\n  ```\n\n阀值", 5),
            ("- ```text\n  阀值\n\n阀值", 4),
        ):
            findings = self.scan(text)
            self.assertEqual([(v.line, v.kind) for v in findings], [(expected, "typo")])

    def test_url_followed_by_chinese_punctuation_keeps_prose(self):
        for punctuation in "。；，！？）」":
            with self.subTest(punctuation=punctuation):
                text = f"参见 https://example.com{punctuation}阀值需要调整。"
                findings = self.scan(text)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].kind, "typo")
                self.assertEqual(findings[0].col, text.index("阀值") + 1)

    def test_explicit_links_keep_punctuation_inside_destination(self):
        for text in (
            "[说明](https://example.com/中文，阀值)",
            "<https://example.com/中文，阀值>",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.scan(text), [])

    def test_quoted_fences_protect_code_but_not_following_prose(self):
        for prefix in ("> ", "> > "):
            text = "\n".join(prefix + line for line in (
                "```text", '阀值 = "api"', "```", "阀值需要调整。"
            ))
            findings = self.scan(text)
            self.assertEqual([(v.line, v.kind) for v in findings], [(4, "typo")])

    def test_unclosed_quote_fence_does_not_hide_outside_prose(self):
        findings = self.scan("> ```text\n> 阀值\n\n阀值需要调整。")
        self.assertEqual([(v.line, v.kind) for v in findings], [(4, "typo")])

    def test_indented_code_and_paragraph_continuation(self):
        for indent in ("    ", "\t", ">     "):
            with self.subTest(indent=indent):
                findings = self.scan(f'{indent}阀值 = "api"\n\n阀值需要调整。')
                self.assertEqual([(v.line, v.kind) for v in findings], [(3, "typo")])
        findings = self.scan("普通段落\n    阀值需要调整。")
        self.assertEqual([(v.line, v.kind) for v in findings], [(2, "typo")])

    def test_fence_like_code_line_does_not_close_fence(self):
        findings = self.scan("```text\n```not-a-close\n阀值\n```\n阀值")
        self.assertEqual([(v.line, v.kind) for v in findings], [(5, "typo")])

    def test_html_attributes_are_hidden_but_text_is_checked(self):
        for text in (
            '<a id="api" title="阀值 > 0">阀值</a>',
            '<a\n id="api"\n title="阀值 > 0">阀值</a>',
        ):
            with self.subTest(text=text):
                findings = self.scan(text)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].kind, "typo")
                last_line = text.splitlines()[-1]
                self.assertEqual(findings[0].col, last_line.rindex("阀值") + 1)

    def test_context_warning_does_not_fail_default_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text("截止日期。登陆月球。", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "lint_copy_rules.py"), str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("PASS WITH ADVICE", result.stdout)

    def test_high_confidence_error_fails_default_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text("阀值。", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "lint_copy_rules.py"), str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("FAIL", result.stdout)


if __name__ == "__main__":
    unittest.main()
