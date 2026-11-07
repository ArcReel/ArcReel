const assert = require('node:assert/strict');
const { test } = require('node:test');
const { commentRanges } = require('./frontend_comment_inventory.cjs');

test('finds JSX comments and comments after nested template expressions', () => {
  const source = [
    'const ignored = "// 字符串";',
    'const view = <div title={`name-${`${value}/`}`}>{/* JSX 注释 */}</div>;',
    '// 模板之后',
    'const endpoint = `${base}/${item ? `nested-${id}` : ""}`; // 行尾注释',
    '/** 后续文档 */',
    'function run() {}',
  ].join('\n');
  assert.deepEqual(commentRanges('view.tsx', source).map(range => range.text), [
    '/* JSX 注释 */', '// 模板之后', '// 行尾注释', '/** 后续文档 */',
  ]);
});

test('returns exact UTF-16 offsets without treating strings, regexes or templates as comments', () => {
  const source = 'const emoji = "😀"; const pattern = /\\/\\/不是注释/; const text = `/* 非注释 */`; // 实际注释\n';
  const [range] = commentRanges('fixture.ts', source);
  assert.equal(range.text, '// 实际注释');
  assert.equal(range.start, source.indexOf('// 实际注释'));
  assert.equal(source.slice(range.start, range.end), range.text);
  assert.equal(range.line, 1);
});

test('includes leading directives, trailing comments, JSDoc and end-of-file comments once', () => {
  const source = '// eslint-disable-next-line no-alert -- 原因\n/** 文档 */\nexport const x = 1; /* 尾注 */\n// 文件末尾';
  assert.deepEqual(commentRanges('config.mjs', source).map(range => range.text), [
    '// eslint-disable-next-line no-alert -- 原因', '/** 文档 */', '/* 尾注 */', '// 文件末尾',
  ]);
});
