/** Compare changed frontend TypeScript with the fixed source, excluding comments. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const ts = require('../../../../frontend/node_modules/typescript');

const root = path.resolve(__dirname, '../../../..');
const reference = process.argv[2] || '7b4f781ab2c57d3d4dd1d0a15bdc5c427262f250';
const output = process.argv[3];
if (!output) throw new Error('Usage: node capture_frontend_passive.cjs BASE OUTPUT.json');
const diagnosticReview = process.argv[4] && JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const exceptionPath = 'frontend/src/lint-class-guards.test.ts';
const git = args => execFileSync('git', args, { cwd: root, encoding: 'utf8' });
const base = git(['rev-parse', '--verify', '--end-of-options', `${reference}^{commit}`]).trim();
const files = git(['diff', '--name-only', '-z', base, '--', 'frontend/']).split('\0')
  .filter(file => /\.tsx?$/.test(file));
const printer = ts.createPrinter({ removeComments: true, newLine: ts.NewLineKind.LineFeed });
const canonical = (file, text) => printer.printFile(ts.createSourceFile(
  file, text, ts.ScriptTarget.Latest, true, file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
));
const sha256 = text => crypto.createHash('sha256').update(text).digest('hex');
const records = files.map(file => {
  const source = git(['show', `${base}:${file}`]);
  const candidate = fs.readFileSync(path.join(root, file), 'utf8');
  let expected = canonical(file, source);
  let reviewedLiteralChanges = [];
  if (file === exceptionPath && diagnosticReview) {
    const record = diagnosticReview.files.find(entry => entry.path === file);
    if (!record || sha256(candidate) !== record.after_sha256 || record.pairs.length !== 17) {
      throw new Error('Diagnostic expectation review does not match the exact candidate');
    }
    const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    const pairs = new Map(record.pairs.map(pair => [pair.literal_ordinal, pair]));
    if (pairs.size !== record.pairs.length) throw new Error('Duplicate reviewed literal ordinal');
    let ordinal = 0;
    const seen = new Set();
    const transform = context => node => {
      function visit(current) {
        if (ts.isStringLiteralLike(current)) {
          const index = ordinal++;
          const pair = pairs.get(index);
          if (pair) {
            if (current.text !== pair.before || ts.SyntaxKind[current.parent.kind] !== pair.parent_kind) {
              throw new Error(`Reviewed original literal context differs: ${file}:${index}`);
            }
            seen.add(index);
            return ts.factory.createStringLiteral(pair.after);
          }
        }
        return ts.visitEachChild(current, visit, context);
      }
      return ts.visitNode(node, visit);
    };
    const transformed = ts.transform(ast, [transform]);
    expected = printer.printFile(transformed.transformed[0]);
    transformed.dispose();
    if (seen.size !== pairs.size) throw new Error('Not all reviewed expectation literals were found');
    reviewedLiteralChanges = record.pairs.map(({literal_ordinal, before, after, parent_kind}) =>
      ({literal_ordinal, before, after, parent_kind}));
  }
  if (expected !== canonical(file, candidate)) {
    throw new Error(`Executable TypeScript differs: ${file}`);
  }
  return { path: file, baseline_sha256: sha256(source), candidate_sha256: sha256(candidate),
    code_without_comments_equal: reviewedLiteralChanges.length === 0,
    ...(reviewedLiteralChanges.length ? {code_equal_after_exact_reviewed_diagnostic_expectations: true,
      reviewed_literal_changes: reviewedLiteralChanges} : {}) };
});
fs.writeFileSync(output, JSON.stringify({ baseline: base,
  scope: 'Every changed tracked frontend TypeScript file. Excludes comments. Optional diagnostic review permits only 17 exact ordinal/context-bound expectation literal changes in lint-class-guards.test.ts. Semantic fidelity and functional directives require separate review.',
  files: records }, null, 2) + '\n');
process.stdout.write(`${records.length} changed frontend files match original code excluding comments and explicitly recorded diagnostic expectation literals\n`);
