/** Inventory parsed frontend comments, including JSX/template tails and test/config files. */
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../../../..');
const ts = require(path.join(root, 'frontend/node_modules/typescript'));

function commentRanges(file, text) {
  const kind = /\.[jt]sx$/.test(file) ? ts.ScriptKind.TSX
    : /\.[cm]?js$/.test(file) ? ts.ScriptKind.JS : ts.ScriptKind.TS;
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
  const ranges = new Map();
  function visit(node) {
    for (const position of [node.pos, node.end, node.getStart(source)]) {
      for (const range of [
        ...(ts.getLeadingCommentRanges(text, position) || []),
        ...(ts.getTrailingCommentRanges(text, position) || []),
      ]) ranges.set(`${range.pos}:${range.end}`, range);
    }
    for (const child of node.getChildren(source)) visit(child);
  }
  visit(source);
  const sorted = [...ranges.values()].sort((a, b) => a.pos - b.pos || b.end - a.end);
  return sorted.filter((range, index) => !sorted.slice(0, index).some(
    parent => parent.pos <= range.pos && parent.end >= range.end,
  )).map(range => ({ start: range.pos, end: range.end,
    line: text.slice(0, range.pos).split('\n').length, text: text.slice(range.pos, range.end) }));
}

function inventory(files) {
  return files.map(file => {
    const text = fs.readFileSync(path.join(root, file), 'utf8');
    return { path: file, comments: commentRanges(file, text).filter(
      comment => /[\u3400-\u9fff]/.test(comment.text),
    ) };
  });
}

if (require.main === module) {
  const output = process.argv[2];
  if (!output) throw new Error('Usage: node frontend_comment_inventory.cjs OUTPUT.json');
  const files = execFileSync('git', ['ls-files', '-z', 'frontend'], { cwd: root, encoding: 'utf8' })
    .split('\0').filter(file => /\.(?:tsx?|[cm]?jsx?)$/.test(file));
  const records = inventory(files);
  fs.writeFileSync(output, JSON.stringify({
    scope: 'All tracked frontend TypeScript/JavaScript source, tests, locales, types, configuration and e2e files; no directory exclusions. Han is a classification signal, never a completion criterion.',
    files: records,
  }, null, 2) + '\n');
  process.stdout.write(`${records.length} files inventoried; ${records.filter(file => file.comments.length).length} with Han comments\n`);
}
module.exports = { commentRanges, inventory };
