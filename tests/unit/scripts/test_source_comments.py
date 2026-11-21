from __future__ import annotations

import pytest

from scripts.audit_comments import comment_additions
from scripts.source_comments import extract_comments


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        ("lib/example.py", '"""# Module docs"""\nvalue = "# literal"\n# prose\n', ["# prose"]),
        (
            "frontend/example.ts",
            "const value = `// literal ${1 /* inline */}`;\nconst regex = /\\/\\//; // trailing\n",
            ["/* inline */", "// trailing"],
        ),
        (
            "frontend/example.tsx",
            'const node = <div data-label="/* literal */">// body{/* JSX comment */}</div>;\n',
            ["/* JSX comment */"],
        ),
        ("frontend/example.js", 'const url = "https://example.test"; // prose\n', ["// prose"]),
        ("frontend/example.css", 'a::before { content: "/* literal */"; } /* prose */\n', ["/* prose */"]),
        (
            "public/example.html",
            '<div title="<!-- literal -->"><!-- HTML comment --></div>\n'
            '<script type="application/json">{"data": "// literal"}</script>\n'
            '<script>const data = "// literal"; // JS comment\n</script>\n'
            '<style>a::before {content: "/* literal */";} /* CSS comment */</style>\n',
            ["<!-- HTML comment -->", "// JS comment", "/* CSS comment */"],
        ),
        ("public/example.svg", "<svg><![CDATA[<!-- literal -->]]><!-- prose --></svg>", ["<!-- prose -->"]),
        (
            "scripts/example.sh",
            'printf "%s" "# literal" # shell prose\n'
            "cat <<'DATA'\n# data\nDATA\n"
            "python - <<'PY'\nprint('# literal')  # embedded Python\nPY\n",
            ["# shell prose", "# embedded Python"],
        ),
        ("Dockerfile.test", '# image prose\nRUN printf "%s" "# literal"\n', ["# image prose"]),
        (
            "Dockerfile",
            'FROM alpine\nRUN printf "%s" "# literal" # shell prose\n'
            'RUN ["echo", "# argument"]\n'
            "ENV MESSAGE=hello # argument\n",
            ["# shell prose"],
        ),
        ("Dockerfile", "FROM alpine\nRUN echo hello \\\n  && echo world # continuation\n", ["# continuation"]),
        (".github/example.yaml", 'key: "# literal" # prose\ntext: |\n  # data\n', ["# prose"]),
        (
            ".github/workflows/example.yml",
            "jobs:\n"
            "  sample:\n"
            "    steps:\n"
            "      - run: |\n"
            "          echo '# shell data' # shell prose\n"
            "      - uses: actions/github-script@v7\n"
            "        with:\n"
            "          script: |\n"
            "            const data = '// JS data'; // JS prose\n",
            ["# shell prose", "// JS prose"],
        ),
        (
            ".github/workflows/example.yml",
            "steps:\n  - run: |\n      echo ${{ '}} # it''s a quote: ''' }} ${{ '' }} # shell prose\n",
            ["# shell prose"],
        ),
        (
            ".github/workflows/example.yml",
            "jobs:\n"
            "  sample:\n"
            "    steps:\n"
            "      - run: >-\n"
            "          printf '%s' '# literal'\n"
            "          # folded shell prose\n"
            "      - run: echo hello;# plain shell prose\n"
            "        env:\n"
            "          run: |\n"
            "            # environment data\n",
            ["# folded shell prose", "# plain shell prose"],
        ),
        (
            ".github/actions/example/action.yml",
            'runs:\n  using: composite\n  steps: [{run: "echo hi # shell prose", shell: bash}]\n',
            ["# shell prose"],
        ),
        (
            ".github/workflows/example.yml",
            "steps:\n  - run: >-\n      echo hello # comment continues\n      # on the next physical line\n",
            ["# comment continues\n      # on the next physical line"],
        ),
        ("pyproject.toml", 'key = "# literal" # prose\ntext = """\n# data\n"""\n', ["# prose"]),
        ("settings.jsonc", '{"key": "// literal", /* prose */ "other": 2}\n', ["/* prose */"]),
        ("website/tsconfig.json", '{"compilerOptions": { /* prose */ "paths": {"#*": ["./*"]} }}\n', ["/* prose */"]),
        ("settings.ini", "[section]\n; prose\nkey = value\n", ["; prose"]),
        (".env.example", 'URL="https://example.test/#literal" # prose\n', ["# prose"]),
        (".gitignore", "\\#literal\n  #leading-space-pattern\n# prose\nignored # part of the path\n", ["# prose"]),
        ("scripts/example.jq", '"# literal" # prose\n', ["# prose"]),
        (
            "alembic/example.py.mako",
            "## Template comment\nvalue = '# literal'  # Python comment\n",
            ["## Template comment", "# Python comment"],
        ),
        ("docs/example.md", "<!-- Documentation -->\n```python\n# example\n```\n", []),
        ("data.json", '{"value": "/* literal */"}\n', []),
    ],
    ids=[
        "python",
        "typescript",
        "tsx",
        "javascript",
        "css",
        "html",
        "svg",
        "shell",
        "dockerfile",
        "dockerfile-shell-and-data",
        "dockerfile-continuation",
        "yaml",
        "workflow",
        "workflow-escaped-expression-quotes",
        "workflow-shell-and-data",
        "composite-flow-step",
        "workflow-folded-comment-span",
        "toml",
        "jsonc",
        "tsconfig",
        "ini",
        "dotenv",
        "ignore",
        "jq",
        "mako",
        "markdown",
        "json",
    ],
)
def test_scans_source_comments_but_preserves_data_literals(path: str, source: str, expected: list[str]) -> None:
    assert [comment.raw for comment in extract_comments(path, source)] == expected


def test_embedded_multiline_comment_has_physical_positions_with_unicode_and_crlf() -> None:
    source = (
        "steps:\r\n"
        "  - uses: actions/github-script@v7\r\n"
        "    with:\r\n"
        "      script: |\r\n"
        "        const 字 = 1; /* 多行\r\n"
        "         * 结束 */\r\n"
    )

    (comment,) = extract_comments(".github/workflows/例.yml", source)

    assert comment.path == ".github/workflows/例.yml"
    assert (comment.start_line, comment.start_col, comment.end_line, comment.end_col) == (5, 21, 6, 16)
    assert comment.raw == "/* 多行\r\n         * 结束 */"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r'"value \("#tag")"', []),
        (r'"outer \("inner \("#tag")") \\( # literal" # trailing comment', ["# trailing comment"]),
        (
            '"value \\(\n  # interpolation comment with )\n  (1 + 2)) # literal" # trailing comment\n',
            ["# interpolation comment with )", "# trailing comment"],
        ),
    ],
    ids=["quoted-hash", "nested-interpolation-and-escape", "comment-in-interpolation"],
)
def test_audits_jq_comments_in_code_and_interpolated_expressions(source: str, expected: list[str]) -> None:
    assert [comment.raw for comment in comment_additions("scripts/example.jq", "", source)] == expected


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        (
            "scripts/example.py",
            "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"
            "# SPDX-License-Identifier: Apache-2.0\n"
            "from api import run  # noqa: F401 -- API consumers import run from here\n"
            "value: int = ''  # type: ignore[assignment]  # Dependency omits this type\n"
            "#! ordinary prose\n",
            [
                (True, ""),
                (True, ""),
                (True, ""),
                (True, ""),
                (True, ""),
                (False, "! ordinary prose"),
            ],
        ),
        (
            "frontend/example.ts",
            '/// <reference types="vite/client" />\n'
            "// eslint-disable-next-line no-console -- This is the CLI output\n"
            "/** @public Consumed by a plugin */\n"
            "// @ts-expect-error External SDK omits this option\n"
            "const data = /* @__PURE__ */ createData();\n",
            [
                (True, ""),
                (True, ""),
                (True, ""),
                (True, ""),
                (True, ""),
            ],
        ),
        ("Dockerfile", "# syntax=docker/dockerfile:1\nFROM scratch\n", [(True, "")]),
        (
            ".github/workflows/example.yml",
            "steps:\n"
            "  - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567 # v6\n"
            "  - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567 # v6 -- Checkout for tests\n"
            "jobs: {} # zizmor: ignore[excessive-permissions] -- Required by deploy\n",
            [(True, ""), (True, "Checkout for tests"), (True, "")],
        ),
    ],
    ids=["python", "typescript", "dockerfile", "workflow"],
)
def test_retains_the_prose_portion_of_machine_directives(
    path: str, source: str, expected: list[tuple[bool, str]]
) -> None:
    assert [(comment.directive, comment.prose) for comment in extract_comments(path, source)] == expected


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("# Existing comment\n", "# Different comment\n", ["# Different comment"]),
        ("# Existing comment\n", "# Existing\n", ["# Existing"]),
        ("# Existing comment\n", "", []),
        ("# Existing comment\n# Existing\n", "# Existing\n", []),
        ("# One\n# Two\n", "# One\n# One\n", ["# One"]),
        ("# One\n# One\n", "# One\n# One\n# One\n", ["# One"]),
        ("# One\nvalue = 1\n", "value = 2  # One\n", []),
    ],
    ids=[
        "replacement",
        "shortening",
        "deletion",
        "retained-shorter-comment",
        "duplicate-replacement",
        "duplicate",
        "move",
    ],
)
def test_matches_each_unchanged_comment_only_once(before: str, after: str, expected: list[str]) -> None:
    assert [comment.raw for comment in comment_additions("lib/example.py", before, after)] == expected


@pytest.mark.parametrize(
    ("path", "source"),
    [
        (
            "lib/example.py",
            "#!/usr/bin/env python3\n"
            "# SPDX-License-Identifier: Apache-2.0\n"
            "from api import run  # noqa: F401 -- API consumers import run from here\n"
            "value: int = ''  # type: ignore[assignment]  # Dependency omits this type\n"
            "# This ordinary comment is audited\n",
        ),
        (
            ".github/workflows/example.yml",
            "jobs: {} # zizmor: ignore[excessive-permissions] -- Required by deploy\n"
            "# This ordinary comment is audited\n",
        ),
    ],
    ids=["python", "workflow"],
)
def test_allows_machine_directives_with_required_suppression_reasons(path: str, source: str) -> None:
    assert [comment.raw for comment in comment_additions(path, "", source)] == ["# This ordinary comment is audited"]


def test_public_marker_does_not_exempt_other_lines_of_its_doc_comment() -> None:
    doc = "/**\n * Duplicates the function name\n * @public Imported dynamically\n */"
    source = f"{doc}\nexport const plugin = 1;\n"

    assert [comment.raw for comment in comment_additions("frontend/src/plugin.ts", "", source)] == [doc]


def test_workflow_allows_tool_reasons_and_pinned_versions_but_audits_extra_prose() -> None:
    source = (
        "steps:\n"
        "  - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567 # v6\n"
        "  - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567 # v6 -- Checkout for tests\n"
        "  - uses: actions/github-script@v7\n"
        "    with:\n"
        "      script: |\n"
        "        // eslint-disable-next-line no-console -- Print the workflow result\n"
        "        console.log('result');\n"
    )

    additions = comment_additions(".github/workflows/example.yml", "", source)

    assert [comment.raw for comment in additions] == ["# v6 -- Checkout for tests"]


@pytest.mark.parametrize(
    ("path", "exempt"),
    [
        ("lib/example.py", True),
        ("server/example.py", True),
        ("packages/sample/src/sample/example.py", True),
        ("tests/unit/example.py", False),
    ],
)
def test_hard_delete_annotation_is_exempt_in_product_modules(path: str, exempt: bool) -> None:
    annotation = "# hard-delete: temp -- Created by this function"
    source = f"path.unlink()  {annotation}\n"

    assert [comment.raw for comment in comment_additions(path, "", source)] == ([] if exempt else [annotation])
