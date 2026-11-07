# Chinese Tech Doc Style

This project is a writing Skill for Chinese technical documentation, product copy, and UI copy, supporting writing, rewriting, proofreading, and review.

Its goal is explicit: make Chinese technical writing more restrained, accurate, and readable. It does not pursue a promotional tone or try to fit everything into one template, instead addressing recurring problems:

- Chinese technical copy becoming vague, repetitive, or promotional
- Poor readability when mixing Chinese with English and numbers
- Mechanical literal translations of common English status and error terms
- Unbalanced information density and structure in documentation homepages, solution pages, interface descriptions, and FAQs
- Unclear terms, conditions, and actions in operating instructions, causing misunderstanding or execution errors

Use this Skill directly, or as a reference, when you need foundational Chinese technical-writing guidelines.

## Use cases

This Skill suits:

- Documentation homepages, landing pages, and first-screen copy
- Interface documentation, parameter descriptions, error-code explanations, and changelogs
- Operating manuals, installation and deployment steps, and multistep API call workflows
- Troubleshooting, operations runbooks, and security guidance
- Product capability introductions, solution pages, and capability descriptions
- UI copy, button copy, navigation labels, and notices

Keep these document elements unchanged; do not apply copy rewriting or typographic replacements:

- Code literals
- JSON keys
- URLs
- API paths
- Database field names
- Other machine-readable identifiers

## Core rules overview

This Skill mainly covers:

- Preserving facts, constraints, conditions, and certainty when rewriting
- Consistent corner brackets `「」` for Chinese quotations
- Avoiding unnecessary direct address by default, with project tone overrides allowed
- Spacing between Chinese, English, and numbers in visible body text
- One source line per Chinese paragraph, without hard wraps
- Avoiding mechanical literal translations of English statuses such as `Success`, `Invalid`, and `Bad Request`
- Avoiding frequent internet jargon such as `赋能`, `抓手`, `闭环`, and `打通`
- Applying controlled Chinese technical-writing methods to operations, troubleshooting, and maintenance documentation

Read the complete guidelines:

- [SKILL.md](./SKILL.md)
- [Public overview](./NoCode-Skill.md)

## Technical-writing methods inspired by STE

This project's [controlled Chinese technical-writing module](./references/controlled-technical-chinese.md) draws on the controlled-language approach of ASD-STE100 (Simplified Technical English, or STE), reducing ambiguity in technical instructions through consistent terminology, explicit actions, and clear logical order.

The module adapts transferable methods to Chinese writing:

- Use one preferred term per concept rather than rotating synonyms to avoid repetition.
- Give each step one main action; split multiple sequential actions into numbered steps.
- Put conditions and risk warnings that must be known before execution ahead of actions.
- Make actors, operation targets, and expected results explicit; distinguish manual operations from automatic system behavior.
- Preserve original facts, constraints, exceptions, and certainty; do not invent information to simplify wording.

These methods mainly serve operating manuals, installation and deployment, troubleshooting, operations documentation, security guidance, and multistep API workflows. Conceptual descriptions and product introductions may selectively apply terminology consistency and explicit references; do not mechanically turn brand copy, narratives, fixed quotations, or short button copy into operating steps.

STE addresses English technical documentation; its controlled vocabulary, part-of-speech, tense, and sentence-length rules cannot be applied directly to Chinese. This project is not a Chinese version of that standard and does not promise compliant output; automated checks do not indicate STE compliance. See the [official ASD-STE100 introduction](https://www.asd-ste100.org/about_STE.html) for background.

## Repository structure

```text
tech-doc-style-chinese/
├── SKILL.md
├── NoCode-Skill.md
├── README.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── api-status-copy.md
│   ├── controlled-technical-chinese.md
│   ├── project-overrides-example.md
│   └── terminology-and-typography.md
├── scripts/
│   ├── lint_copy_rules.py
│   └── unwrap_md_paragraphs.py
└── tests/
    ├── test_lint_copy_rules.py
    ├── test_skill_structure.py
    └── test_unwrap_md_paragraphs.py
```

File roles:

- `SKILL.md`: formal skill entry point for agents such as Codex and Claude Code
- `NoCode-Skill.md`: public overview suitable for reading and sharing
- `README.md`: GitHub repository homepage
- `agents/openai.yaml`: skill display metadata
- `references/`: detailed rules and project override templates read as needed
- `scripts/lint_copy_rules.py`: lightweight checker
- `scripts/unwrap_md_paragraphs.py`: paragraph hard-wrap removal tool
- `tests/`: regression tests for the checker, skill structure, and unwrapping tool

## Using in Codex

### Install with npx (recommended)

With Node.js installed locally, install directly with `npx skills`:

```bash
# Install directly
npx skills add https://github.com/Fenng/tech-doc-style-chinese
```

For noninteractive installation explicitly targeting global Codex, use:

```bash
npx -y skills add https://github.com/Fenng/tech-doc-style-chinese -a codex -g
```

Arguments:

- `-a codex` installs for the Codex agent
- `-g` installs globally (user level); without it, installation is scoped to the current project
- `-y` skips interactive confirmation for automation

Restart Codex after installation to ensure the new Skill loads.

### Install a Release (recommended)

Pin a version for team reproducibility:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$CODEX_HOME/skills"

git clone --depth 1 --branch <release-tag> \
  https://github.com/Fenng/tech-doc-style-chinese.git \
  "$CODEX_HOME/skills/tech-doc-style-chinese"
```

Replace `<release-tag>` with a published version, for example `v0.1.0.2.4`.

### Install from a local directory (development)

When modifying or debugging locally, copy the directory directly:

```bash
mkdir -p "$CODEX_HOME/skills/tech-doc-style-chinese"
cp -R ./* "$CODEX_HOME/skills/tech-doc-style-chinese/"
```

Quickly verify installation:

```bash
test -f "$CODEX_HOME/skills/tech-doc-style-chinese/SKILL.md" && echo "installed"
```

After installation, invoke explicitly in a task:

```text
Use $tech-doc-style-chinese to rewrite this Chinese technical copy.
```

Or trigger it through relevant tasks, for example:

- Rewrite Chinese technical copy
- Organize FAQs
- Improve API documentation wording
- Improve Chinese landing-page copy

## Using in Claude Code

### Ask Claude Code to install (simplest)

If the current Claude Code environment supports Skill installation, ask it to read and install this repository:

```text
请安装这份 Skill：https://github.com/Fenng/tech-doc-style-chinese
```

This is convenient, but project-level versus global installation depends on Claude Code's current capabilities and judgment. For team collaboration or instructions embedded in documentation or CI, prefer the npx commands below.

### Install with npx (recommended)

With Node.js installed locally, install directly with `npx skills`:

```bash
# Install in the current project
npx skills add https://github.com/Fenng/tech-doc-style-chinese -a claude-code
```

For noninteractive installation explicitly targeting global Claude Code, use:

```bash
npx -y skills add https://github.com/Fenng/tech-doc-style-chinese -a claude-code -g
```

Arguments:

- `-a claude-code` installs for Claude Code
- `-g` installs globally (user level, into `~/.claude/skills/`); without it, installation is scoped to the current project (into `./.claude/skills/`)
- `-y` skips interactive confirmation for automation

Restart Claude Code after installation to ensure the new Skill loads.

### Install from a local directory (development)

When modifying or debugging locally, copy the directory directly:

```bash
mkdir -p ~/.claude/skills/tech-doc-style-chinese
cp SKILL.md ~/.claude/skills/tech-doc-style-chinese/
cp -R references ~/.claude/skills/tech-doc-style-chinese/
```

Quickly verify installation:

```bash
test -f ~/.claude/skills/tech-doc-style-chinese/SKILL.md && echo "installed"
```

Claude Code automatically decides when to invoke the Skill based on the `description` in `SKILL.md`; no manual trigger is needed, for example:

- Rewrite Chinese technical copy
- Organize FAQs
- Improve API documentation wording
- Improve Chinese landing-page copy

## Project-level overrides

This Skill contains only general rules; it does not hardcode one project's version display, brand voice, glossary, or information architecture into core guidelines.

If a project has its own conventions, create a separate override file in the target project. Start from this template:

- `references/project-overrides-example.md`

Override files suit:

- Version display conventions
- Brand or terminology preferences
- Documentation structure preferences
- Examples specific to the current project

The template itself contains no business terminology effective by default. Do not treat the sample file as target-project conventions.

## Lightweight validation and CI

The repository includes a zero-dependency validation script for frequent rules. Results are categorized as:

- `error`: highly certain errors; causes nonzero exit by default
- `warning`: context-dependent suspicious expressions requiring human judgment
- `style`: project style and terminology preferences

The checker protects code blocks, inline code, URLs, Markdown link targets, and single- or multi-segment API paths. Contextual items such as `截止日期`, `登陆月球`, `配制溶液`, and `H5` are no longer treated as definite errors.

Run locally:

```bash
python scripts/lint_copy_rules.py
```

Check only specified files or directories:

```bash
python scripts/lint_copy_rules.py SKILL.md NoCode-Skill.md references/
```

Treat warnings and style hints as failures too:

```bash
python scripts/lint_copy_rules.py --strict SKILL.md references/
```

Skip checks for one line:

```markdown
需要保留的原文 <!-- copy-lint-disable-line -->
```

Run regression tests:

```bash
python -m unittest discover -s tests -v
```

The GitHub Actions configuration is `.github/workflows/skill-lint.yml`, running automatically on `pull_request` and `push` to `main`.

## Unwrap paragraph hard breaks

Manually breaking paragraphs to control source line width fragments source text; breaks at Chinese/English boundaries may also add or swallow spaces in some renderers. A zero-dependency script restores paragraphs and list items to one paragraph per line, relying on editor soft wrapping for reading.

Check for hard wraps without changing files:

```bash
python scripts/unwrap_md_paragraphs.py --check .
```

Unwrap specified files or directories and write back directly:

```bash
python scripts/unwrap_md_paragraphs.py docs/ README.md
```

Preview results without writing files:

```bash
python scripts/unwrap_md_paragraphs.py --stdout docs/guide.md
```

Join boundaries follow Chinese/Western spacing rules: no spaces between Chinese characters, one space between Chinese and half-width English or numbers, and no spaces around full-width punctuation.

The script scans structure before joining paragraphs, preserving these as complete blocks rather than protecting only their first lines: front matter, fenced code blocks (including `- ```sh` following list markers), indented code blocks (including indented code in list items), table rows, headings, separators, multiline HTML blocks (`<pre>`, `<script>`, `<style>`, `<textarea>`, and comments), link-reference definitions, blockquotes, and explicit breaks indicated by two trailing spaces or a backslash.

To skip an entire file, place this marker alone on a line outside code blocks:

```markdown
<!-- unwrap-disable-file -->
```

The marker only applies when standing alone outside code and HTML blocks, so the code example above does not exempt this file.

The script handles joins only, without modifying existing interior spacing, punctuation, or wording. After unwrapping, manually recheck boundary spacing and list and table structures.

## Publishing recommendations

For publicly sharing guideline content only:

- Retain `NoCode-Skill.md`
- Use `README.md` as the repository homepage

For directly installable use:

- Retain `SKILL.md`
- Retain `agents/openai.yaml`
- Clearly document directory structure and installation in the repository

<!-- 作者：Fenng（GitHub：@Fenng） -->

## License

This project uses the MIT License.\
See [LICENSE](./LICENSE).
