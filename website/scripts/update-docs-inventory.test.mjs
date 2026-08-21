import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

const script = resolve(import.meta.dirname, "update-docs-inventory.mjs");

function withRepo(files, run) {
  const root = mkdtempSync(join(tmpdir(), "arcreel-update-docs-"));
  try {
    for (const [path, content] of Object.entries(files)) {
      const absolutePath = join(root, path);
      mkdirSync(resolve(absolutePath, ".."), { recursive: true });
      writeFileSync(absolutePath, content, "utf8");
    }
    run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function frontmatter(value) {
  return `---\nid: fixture\nupdate_docs: ${value}\n---\n\n# Fixture {#fixture}\n`;
}

const responsibilities = (paths) => `
### 各页职责

| 页面 | 应该包含 | 不应该包含 |
|---|---|---|
${paths.map((path) => `| \`${path}\` | yes | no |`).join("\n")}

### 写作约定
`;

test("CLI derives coverage tiers from page frontmatter and reports explicit none", () => {
  withRepo(
    {
      "website/docs/a.md": frontmatter("full"),
      "website/docs/nested/b.mdx": frontmatter("fact-check"),
      "website/docs/ignored.md": frontmatter("none"),
      "CONTRIBUTING.md": responsibilities([
        "website/docs/a.md",
        "website/docs/nested/b.mdx",
        "website/docs/ignored.md",
      ]),
    },
    (root) => {
      const output = execFileSync(process.execPath, [script, "--root", root, "--format", "tsv"], {
        encoding: "utf8",
      });
      assert.equal(
        output,
        ["full\twebsite/docs/a.md", "none\twebsite/docs/ignored.md", "fact-check\twebsite/docs/nested/b.mdx", ""].join(
          "\n",
        ),
      );
    },
  );
});

test("CLI fails loud on an invalid update-docs value, including retired engine names", () => {
  withRepo(
    {
      "website/docs/typo.md": frontmatter("engine-a"),
      "CONTRIBUTING.md": responsibilities(["website/docs/typo.md"]),
    },
    (root) => {
      assert.throws(
        () => execFileSync(process.execPath, [script, "--root", root], { encoding: "utf8", stdio: "pipe" }),
        (error) =>
          error.stderr.includes(
            "website/docs/typo.md 的 frontmatter update_docs 值「engine-a」无效（可选 full / fact-check / none）",
          ),
      );
    },
  );
});

test("CLI fails loud when a published page has no update-docs declaration", () => {
  withRepo(
    {
      "website/docs/declared.md": frontmatter("fact-check"),
      "website/docs/undeclared.md": "---\nid: missing\n---\n",
      "CONTRIBUTING.md": responsibilities(["website/docs/declared.md", "website/docs/undeclared.md"]),
    },
    (root) => {
      assert.throws(
        () => execFileSync(process.execPath, [script, "--root", root], { encoding: "utf8", stdio: "pipe" }),
        (error) => error.stderr.includes("website/docs/undeclared.md 未声明 frontmatter 的 update_docs"),
      );
    },
  );
});

test("CLI reports pages missing from and stale in the responsibilities table", () => {
  withRepo(
    {
      "website/docs/actual.md": frontmatter("fact-check"),
      "CONTRIBUTING.md": responsibilities(["website/docs/stale.md"]),
    },
    (root) => {
      assert.throws(
        () => execFileSync(process.execPath, [script, "--root", root], { encoding: "utf8", stdio: "pipe" }),
        (error) => {
          assert.match(error.stderr, /各页职责缺少：website\/docs\/actual\.md/);
          assert.match(error.stderr, /各页职责多出：website\/docs\/stale\.md/);
          return true;
        },
      );
    },
  );
});

test("CLI ignores table-like responsibility rows inside fenced examples", () => {
  withRepo(
    {
      "website/docs/actual.md": frontmatter("fact-check"),
      "CONTRIBUTING.md": responsibilities(["website/docs/actual.md"]).replace(
        "\n### 写作约定",
        "\n```md\n| `website/docs/example-only.md` | yes | no |\n```\n\n### 写作约定",
      ),
    },
    (root) => {
      assert.doesNotThrow(() =>
        execFileSync(process.execPath, [script, "--root", root], { encoding: "utf8", stdio: "pipe" }),
      );
    },
  );
});
