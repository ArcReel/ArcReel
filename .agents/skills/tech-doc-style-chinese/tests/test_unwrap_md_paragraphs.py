from __future__ import annotations

import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "unwrap_md_paragraphs.py"
UNWRAPPER = runpy.run_path(str(SCRIPT), run_name="unwrap_md_paragraphs_test")
unwrap_text = UNWRAPPER["unwrap_text"]
joiner = UNWRAPPER["joiner"]
scan_structure = UNWRAPPER["scan_structure"]
has_file_ignore_marker = UNWRAPPER["has_file_ignore_marker"]
process_file = UNWRAPPER["process_file"]
KIND_VERBATIM = UNWRAPPER["KIND_VERBATIM"]


class JoinSpacingTest(unittest.TestCase):
    def test_chinese_to_chinese_has_no_space(self):
        self.assertEqual(joiner("模型把它写成", "多行文本"), "")

    def test_chinese_to_latin_keeps_one_space(self):
        self.assertEqual(joiner("其中还提到了作为", "ASR 输入"), " ")
        self.assertEqual(joiner("输入的", "stdio 模式"), " ")

    def test_latin_to_chinese_keeps_one_space(self):
        self.assertEqual(joiner("HTTP", "请求超时"), " ")

    def test_full_width_punctuation_takes_no_space(self):
        self.assertEqual(joiner("补充说明，", "API 返回值"), "")
        self.assertEqual(joiner("使用 stdio", "。结束"), "")

    def test_latin_to_latin_keeps_one_space(self):
        self.assertEqual(joiner("controlled", "language"), " ")

    def test_ascii_punctuation_does_not_gain_space(self):
        self.assertEqual(joiner("stdio", ", grpc"), "")

    def test_inline_markup_uses_visible_characters(self):
        self.assertEqual(joiner("详见 **接口说明**", "文档目录"), "")
        self.assertEqual(joiner("详见 [接口说明](api.md)", "文档目录"), "")
        self.assertEqual(joiner("参数", "`--check` 可用"), " ")


class UnwrapParagraphTest(unittest.TestCase):
    def test_preserves_entire_multiline_setext_heading(self):
        for underline in ('---', '==='):
            title = '标题第一行\n标题第二行\n' + underline + '\n'
            text = title + '\n正文第一行\n正文第二行\n'
            result, joins = unwrap_text(text)
            self.assertEqual(result, title + '\n正文第一行正文第二行\n')
            self.assertEqual(len(joins), 1)
            self.assertEqual(unwrap_text(result)[0], result)

    def test_preserves_multiline_code_spans(self):
        for span in (
            "说明 `中文\n内容`。\n",
            "说明 ``中文 `\n内容``。\n",
            "- 说明 `中文\n  内容`。\n",
            "说明 `中文  \n内容`。\n",
        ):
            with self.subTest(span=span):
                text = span + "\n正文第一行\n正文第二行\n"
                result, joins = unwrap_text(text)
                self.assertEqual(result, span + "\n正文第一行正文第二行\n")
                self.assertEqual(len(joins), 1)
                self.assertEqual(unwrap_text(span), (span, []))

    def test_unmatched_or_escaped_backticks_do_not_hide_paragraph(self):
        for text in ("说明 `未闭合\n正文。\n", "说明 \\`中文\n内容`。\n"):
            self.assertEqual(len(unwrap_text(text)[1]), 1)

    def test_closing_fence_indent_is_relative_to_container(self):
        for fence in ("```", "~~~"):
            for opening_indent in range(4):
                for closing_indent in range(4):
                    code = " " * opening_indent + fence + "text\ncode\n" + " " * closing_indent + fence + "\n"
                    self.assertEqual(
                        self.unwrap(code + "\n正文一\n正文二\n"),
                        code + "\n正文一正文二\n",
                    )
        code = "- 示例\n\n     ```text\n  code\n  ```\n"
        self.assertEqual(self.unwrap(code + "\n正文一\n正文二\n"), code + "\n正文一正文二\n")

    def test_overindented_fence_does_not_close(self):
        text = "```text\n    ```\n正文一\n正文二\n"
        self.assertEqual(self.unwrap(text), text)

    def unwrap(self, text: str) -> str:
        return unwrap_text(text)[0]

    def test_paragraph_becomes_one_line(self):
        text = "模型把它写成\n多行，其中提到了作为\nASR 输入的\nstdio 模式。\n"
        self.assertEqual(
            self.unwrap(text),
            "模型把它写成多行，其中提到了作为 ASR 输入的 stdio 模式。\n",
        )

    def test_reports_start_line_and_merged_count(self):
        text = "# 标题\n\n第一行\n第二行\n第三行\n"
        _, joins = unwrap_text(text)
        self.assertEqual(joins[0][0], 3)
        self.assertEqual(joins[0][1], 2)

    def test_keeps_front_matter(self):
        text = "---\nname: demo\ndescription: 第一行\n  第二行\n---\n\n正文。\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_fenced_code_block(self):
        text = "```bash\necho 一\necho 二\n```\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_tables_headings_and_rules(self):
        text = (
            "## 参数\n"
            "\n"
            "| 参数 | 说明 |\n"
            "| --- | --- |\n"
            "| `mode` | 运行模式 |\n"
            "\n"
            "---\n"
            "\n"
            "副标题\n"
            "---\n"
        )
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_blockquote_and_html_block(self):
        text = "> 引用第一行\n> 引用第二行\n\n<div>\nHTML 内容\n</div>\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_indented_code_block(self):
        text = "说明如下。\n\n    indented code\n    stays as is\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_reference_definition(self):
        text = "[ref]: https://example.com\n[note]: https://example.org\n"
        self.assertEqual(self.unwrap(text), text)

    def test_unwraps_list_items_and_keeps_structure(self):
        text = (
            "- 第一项为了行宽断开，\n"
            "  第二行继续说明 API 的\n"
            "  返回值。\n"
            "- 第二项\n"
            "\n"
            "1. 步骤第一行\n"
            "   步骤续行\n"
        )
        self.assertEqual(
            self.unwrap(text),
            "- 第一项为了行宽断开，第二行继续说明 API 的返回值。\n"
            "- 第二项\n"
            "\n"
            "1. 步骤第一行步骤续行\n",
        )

    def test_keeps_nested_list_structure(self):
        text = "- 父项说明\n  - 子项说明\n    继续说明子项\n"
        self.assertEqual(
            self.unwrap(text),
            "- 父项说明\n  - 子项说明继续说明子项\n",
        )

    def test_unwraps_second_paragraph_inside_list_item(self):
        text = (
            "- 第一项说明。\n"
            "\n"
            "    列表项内的第二段，\n"
            "    应当合并为一行。\n"
        )
        self.assertEqual(
            self.unwrap(text),
            "- 第一项说明。\n\n    列表项内的第二段，应当合并为一行。\n",
        )

    def test_keeps_indented_code_inside_list_item(self):
        text = "- 步骤说明。\n\n      indented code\n      stays as is\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_fence_opened_on_list_marker_line(self):
        text = "- ```sh\n  echo first\n  echo second\n  ```\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_tilde_fence_opened_on_list_marker_line(self):
        text = "- ~~~sh\n  echo first\n  echo second\n  ~~~\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_fence_opened_on_ordered_list_marker_line(self):
        text = "1. ```py\n   print(1)\n   print(2)\n   ```\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_fence_indented_at_item_content_column(self):
        text = "- 说明\n\n  ```sh\n  echo first\n  echo second\n  ```\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_closing_fence_indented_within_slack(self):
        text = "- ```sh\n  echo first\n    ```\n"
        self.assertEqual(self.unwrap(text), text)

    def test_unwraps_paragraph_after_list_fence_block(self):
        text = "- ```sh\n  echo first\n  ```\n\n段落第一行\n段落第二行\n"
        unwrapped, joins = unwrap_text(text)
        self.assertEqual(
            unwrapped,
            "- ```sh\n  echo first\n  ```\n\n段落第一行段落第二行\n",
        )
        self.assertEqual([(item[0], item[1]) for item in joins], [(5, 1)])

    def test_closing_fence_requires_only_fence_characters(self):
        text = "```\ncode\n```python\n文本一\n文本二\n"
        self.assertEqual(self.unwrap(text), text)

    def test_inline_code_span_does_not_open_fence(self):
        text = "```code``` 表示行内\n代码写法。\n"
        self.assertEqual(self.unwrap(text), "```code``` 表示行内代码写法。\n")

    def test_keeps_indented_code_that_looks_like_list(self):
        text = "示例：\n\n    - first\n    second\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_indented_code_after_list_ends(self):
        text = "- 项目\n\n结束。\n\n    echo first\n    echo second\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_indented_code_after_heading_ends_list(self):
        text = "- 项目说明\n# 标题\n\n    echo first\n    echo second\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_indented_code_across_blank_lines(self):
        text = "说明：\n\n    echo first\n\n    echo second\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_tab_indented_code_block(self):
        text = "说明：\n\n\t制表符代码\n\t第二行\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_multiline_pre_block(self):
        text = "<pre>\nfirst\nsecond\n</pre>\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_pre_block_containing_blank_line(self):
        text = "<pre>\nfirst\n\nsecond\n</pre>\n\n段落一\n段落二\n"
        self.assertEqual(
            self.unwrap(text),
            "<pre>\nfirst\n\nsecond\n</pre>\n\n段落一段落二\n",
        )

    def test_keeps_pre_block_opened_on_list_marker_line(self):
        text = "- <pre>\n  first\n  second\n  </pre>\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_multiline_html_block(self):
        text = "<div>\n第一行\n第二行\n</div>\n"
        self.assertEqual(self.unwrap(text), text)

    def test_html_block_ends_at_blank_line(self):
        text = "<div>\n第一行\n</div>\n\n段落一\n段落二\n"
        self.assertEqual(
            self.unwrap(text), "<div>\n第一行\n</div>\n\n段落一段落二\n"
        )

    def test_keeps_multiline_html_comment(self):
        text = "<!-- 注释第一行\n注释第二行 -->\n\n段落一\n段落二\n"
        self.assertEqual(
            self.unwrap(text), "<!-- 注释第一行\n注释第二行 -->\n\n段落一段落二\n"
        )

    def test_autolink_line_does_not_open_html_block(self):
        text = "<https://example.com>\n后续段落一\n后续段落二\n"
        self.assertEqual(
            self.unwrap(text), "<https://example.com>\n后续段落一后续段落二\n"
        )

    def test_scan_marks_list_fence_lines_verbatim(self):
        scan = scan_structure("- ```sh\n  echo first\n  ```\n")
        self.assertEqual(
            [item.kind for item in scan.lines],
            [KIND_VERBATIM, KIND_VERBATIM, KIND_VERBATIM],
        )

    def test_keeps_explicit_hard_break(self):
        text = "本项目采用 MIT License。  \n详见 LICENSE。\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_trailing_backslash_break(self):
        text = "第一行\\\n第二行\n"
        self.assertEqual(self.unwrap(text), text)

    def test_keeps_blank_line_structure_and_final_newline(self):
        text = "第一段。\n\n第二段。"
        self.assertEqual(self.unwrap(text), text)

    def test_is_idempotent(self):
        text = "模型把它写成\n多行，提到了作为\nASR 输入。\n"
        once = self.unwrap(text)
        self.assertEqual(self.unwrap(once), once)

    def test_file_ignore_marker_skips_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            text = "<!-- unwrap-disable-file -->\n\n第一行\n第二行\n"
            path.write_text(text, encoding="utf-8")
            unwrapped, joins = UNWRAPPER["process_file"](path)
            self.assertEqual(unwrapped, text)
            self.assertEqual(joins, [])


class IgnoreMarkerScopeTest(unittest.TestCase):
    """跳过标记只在代码块和 HTML 块之外独立成行时生效。"""

    def test_standalone_marker_returns_text_unchanged(self):
        text = "<!-- unwrap-disable-file -->\n\n第一行\n第二行\n"
        self.assertTrue(has_file_ignore_marker(text))
        self.assertEqual(unwrap_text(text), (text, []))

    def test_marker_inside_fenced_example_does_not_skip_file(self):
        text = "````markdown\n<!-- unwrap-disable-file -->\n````\n\n第一行\n第二行\n"
        self.assertFalse(has_file_ignore_marker(text))
        unwrapped, joins = unwrap_text(text)
        self.assertEqual(
            unwrapped, "````markdown\n<!-- unwrap-disable-file -->\n````\n\n第一行第二行\n"
        )
        self.assertEqual(len(joins), 1)

    def test_marker_appended_to_text_does_not_skip_file(self):
        self.assertFalse(
            has_file_ignore_marker("正文 <!-- unwrap-disable-file -->\n第二行\n")
        )

    def test_marker_inside_indented_code_does_not_skip_file(self):
        text = "说明：\n\n    <!-- unwrap-disable-file -->\n\n第一行\n第二行\n"
        self.assertFalse(has_file_ignore_marker(text))

    def test_marker_inside_html_block_does_not_skip_file(self):
        text = "<pre>\n<!-- unwrap-disable-file -->\n</pre>\n\n第一行\n第二行\n"
        self.assertFalse(has_file_ignore_marker(text))

    def test_process_file_agrees_with_unwrap_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text(
                "```markdown\n<!-- unwrap-disable-file -->\n```\n\n第一行\n第二行\n",
                encoding="utf-8",
            )
            _, joins = process_file(path)
            self.assertEqual(len(joins), 1)

    def test_readme_is_not_exempt_from_check(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("<!-- unwrap-disable-file -->", readme)
        self.assertFalse(has_file_ignore_marker(readme))


class UnwrapCliTest(unittest.TestCase):
    def run_cli(self, path: Path, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *flags, str(path)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_check_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            text = "模型把它写成\n多行。\n"
            path.write_text(text, encoding="utf-8")
            result = self.run_cli(path, "--check")
            self.assertEqual(result.returncode, 2)
            self.assertIn("FAIL", result.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_default_run_writes_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text("模型把它写成\n多行。\n", encoding="utf-8")
            result = self.run_cli(path)
            self.assertEqual(result.returncode, 0)
            self.assertIn("DONE", result.stdout)
            self.assertEqual(
                path.read_text(encoding="utf-8"), "模型把它写成多行。\n"
            )

    def test_clean_file_passes_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.md"
            path.write_text("模型把它写成一行。\n", encoding="utf-8")
            result = self.run_cli(path, "--check")
            self.assertEqual(result.returncode, 0)
            self.assertIn("PASS", result.stdout)

    def test_repository_markdown_has_no_hard_wraps(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", str(ROOT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout)


if __name__ == "__main__":
    unittest.main()
