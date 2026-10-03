// token 语义化改名 codemod（#2994）：把旧的颜色、圆角 token 改写为 shadcn 语义命名。
//
// 用法（在 frontend/ 下运行，Node 直接执行 TS）：
//   node scripts/token-codemod.ts             改写 src/ 下的 .ts、.tsx、.css
//   node scripts/token-codemod.ts --check     只列出会改动的文件，有改动时退出码为 1
//   node scripts/token-codemod.ts <路径…>     只处理指定的文件或目录
//
// 改写规则：
// - 颜色工具类（bg-、text-、border-、ring- 等）按 COLOR_MAP 改名。被删除的变体色改为基色加
//   透明度修饰，与原有修饰相乘：bg-accent-dim/50 → bg-primary/6。
// - ring-accent、outline-accent 带 focus 类变体时改为 ring 色，其余改为 primary。
// - 只引用一个颜色的任意值类（text-[var(--color-text-4)]）改为对应工具类。
// - 其余 var(--color-*) 改为 index.css :root 中的原始变量（var(--primary)），带透明度的改用
//   color-mix；在任意值类内部使用下划线写法。首尾同色的 linear-gradient 收为单色。
// - 任意值圆角 rounded-[Npx] 改为 Nova 刻度中最接近的具名档，距离相同时取较大档；
//   不带档位的 rounded（4px）按同一规则改为 rounded-sm，只改字符串里的类名。
// 运行结束时列出改写后仍残留的旧颜色变量、旧工具类与任意值圆角，以及因颜色合并而两支同值的
// 三元表达式，这些需要人工处理。
//
// 跳过 src/components/ui/（shadcn 生成文件，其中的 accent 是菜单悬停色）与 src/i18n/。
// 重跑幂等：改写结果不再命中任何规则。accent → primary 无法区分旧紫色与 shadcn 的 accent，
// 业务代码开始使用 shadcn accent 之后，不要再对这些文件运行本脚本。

import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";

interface ColorTarget {
  /** index.css 中的语义名，同时是工具类名与 :root 变量名 */
  name: string;
  /** 透明度修饰（百分比），被删除的变体色用它表达 */
  alpha?: number;
}

// 旧 token → 语义 token。透明度按原色在页面底色上的观感取整：原值已是基色加透明度的保留原比例；
// 深色加透明度的浅底（soft、tint）换成亮基色后取更低的透明度。
const COLOR_MAP: Record<string, ColorTarget> = {
  accent: { name: "primary" },
  "accent-2": { name: "primary" },
  "accent-soft": { name: "primary", alpha: 22 },
  "accent-dim": { name: "primary", alpha: 12 },
  "accent-glow": { name: "primary", alpha: 35 },
  text: { name: "foreground" },
  "text-2": { name: "subtle-foreground" },
  "text-3": { name: "muted-foreground" },
  "text-4": { name: "muted-foreground" },
  hairline: { name: "border" },
  "hairline-strong": { name: "input" },
  "hairline-soft": { name: "border", alpha: 50 },
  bg: { name: "background" },
  "bg-grad-a": { name: "card" },
  "bg-grad-b": { name: "sidebar" },
  "surface-2": { name: "muted" },
  "ink-800": { name: "muted" },
  danger: { name: "destructive" },
  "danger-2": { name: "destructive" },
  "danger-bright": { name: "destructive" },
  "danger-soft": { name: "destructive", alpha: 10 },
  "danger-ring": { name: "destructive", alpha: 30 },
  "danger-glow": { name: "destructive", alpha: 35 },
  warm: { name: "warn" },
  "warm-bright": { name: "warn" },
  "warm-fade": { name: "warn", alpha: 50 },
  "warm-soft": { name: "warn", alpha: 10 },
  "warm-tint": { name: "warn", alpha: 15 },
  "warm-tint-faint": { name: "warn", alpha: 5 },
  "warm-ring": { name: "warn", alpha: 30 },
  "warm-glow": { name: "warn", alpha: 35 },
};

// 名字不变、但变量移到 :root 的 token：var(--color-good) → var(--good)
const VAR_ONLY_MAP: Record<string, ColorTarget> = {
  good: { name: "good" },
  warn: { name: "warn" },
};

const VAR_MAP: Record<string, ColorTarget> = { ...COLOR_MAP, ...VAR_ONLY_MAP };

// Nova 刻度（--radius: 0.625rem）；xs 沿用 Tailwind 默认的 2px
const RADIUS_TIERS: ReadonlyArray<readonly [string, number]> = [
  ["xs", 2],
  ["sm", 6],
  ["md", 8],
  ["lg", 10],
  ["xl", 14],
  ["2xl", 18],
  ["3xl", 22],
  ["4xl", 26],
];

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const alternation = (names: string[]) =>
  [...names]
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp)
    .join("|");

const OLD_TOKENS = alternation(Object.keys(COLOR_MAP));
const VAR_TOKENS = alternation(Object.keys(VAR_MAP));
const UTILITY =
  "bg|text|border(?:-[xytrblse])?|ring-offset|ring|outline|divide|from|via|to|fill|stroke|caret|accent|decoration|shadow|inset-ring|inset-shadow";
const MODIFIER = String.raw`(?:\/(\d+|\[(?:0?\.\d+|\d+%)\]))?`;
const SIDES = "tl|tr|br|bl|ss|se|es|ee|t|r|b|l|s|e";

const UTILITY_RE = new RegExp(
  String.raw`(?<![\w-])(${UTILITY})-(${OLD_TOKENS})${MODIFIER}(?![\w\-[])`,
  "g",
);
const ARBITRARY_COLOR_RE = new RegExp(
  String.raw`(?<![\w-])(${UTILITY})-(?:\[var\(--color-(${VAR_TOKENS})\)\]|\(--color-(${VAR_TOKENS})\))${MODIFIER}(?![\w\-[])`,
  "g",
);
const VAR_RE = new RegExp(String.raw`var\(--color-(${VAR_TOKENS})(\s*,[^()]*)?\)`, "g");
const SOLID_GRADIENT_RE = /linear-gradient\(\s*\d+deg\s*,\s*(var\(--[\w-]+\))\s*,\s*\1\s*\)/g;
const BACKGROUND_PROPERTY_RE = /(?<![\w-])\[background:var\(--([\w-]+)\)\]/g;
const ARBITRARY_RADIUS_RE = new RegExp(
  String.raw`(?<![\w-])rounded(-(?:${SIDES}))?-\[(\d+(?:\.\d+)?)px\]`,
  "g",
);
const BARE_RADIUS_RE = new RegExp(String.raw`(?<![\w\-[/.])rounded(-(?:${SIDES}))?(?![\w\-[/])`, "g");

const TOKEN_BOUNDARY = /[\s"'`]/;

function tokenStart(text: string, offset: number): number {
  let i = offset;
  while (i > 0 && !TOKEN_BOUNDARY.test(text[i - 1])) i -= 1;
  return i;
}

function tokenEnd(text: string, offset: number): number {
  let i = offset;
  while (i < text.length && !TOKEN_BOUNDARY.test(text[i])) i += 1;
  return i;
}

function parseModifier(raw: string | undefined): number | undefined {
  if (raw === undefined) return undefined;
  if (raw.startsWith("[")) {
    const inner = raw.slice(1, -1);
    return inner.endsWith("%") ? Number(inner.slice(0, -1)) : Math.round(Number(inner) * 100);
  }
  return Number(raw);
}

function combineAlpha(base: number | undefined, modifier: number | undefined): number | undefined {
  if (base === undefined) return modifier;
  if (modifier === undefined) return base;
  return Math.max(1, Math.round((base * modifier) / 100));
}

function colorClass(
  utility: string,
  target: ColorTarget,
  oldToken: string,
  modifier: string | undefined,
  variantPrefix: string,
): string {
  const focusRing =
    oldToken === "accent" && (utility === "ring" || utility === "outline") && variantPrefix.includes("focus");
  const name = focusRing ? "ring" : target.name;
  const alpha = combineAlpha(target.alpha, parseModifier(modifier));
  return `${utility}-${name}${alpha === undefined ? "" : `/${alpha}`}`;
}

function colorValue(target: ColorTarget, inArbitraryClass: boolean, fallback = ""): string {
  if (target.alpha === undefined) return `var(--${target.name}${fallback})`;
  const ref = `var(--${target.name})`;
  return inArbitraryClass
    ? `color-mix(in_oklab,${ref}_${target.alpha}%,transparent)`
    : `color-mix(in oklab, ${ref} ${target.alpha}%, transparent)`;
}

function radiusTier(px: number): string {
  if (px === 0) return "none";
  if (px >= 100) return "full";
  let best = RADIUS_TIERS[0];
  for (const tier of RADIUS_TIERS) {
    // 距离相同时取较大档：数组按升序遍历，用 <= 让后者覆盖
    if (Math.abs(tier[1] - px) <= Math.abs(best[1] - px)) best = tier;
  }
  return best[0];
}

function rewriteColors(text: string): string {
  let out = text.replace(
    ARBITRARY_COLOR_RE,
    (match, utility: string, bracketToken: string | undefined, shortToken: string | undefined, modifier: string | undefined, offset: number, whole: string) => {
      const token = bracketToken ?? shortToken ?? "";
      const target = VAR_MAP[token];
      if (!target) return match;
      return colorClass(utility, target, token, modifier, whole.slice(tokenStart(whole, offset), offset));
    },
  );
  out = out.replace(
    UTILITY_RE,
    (match, utility: string, token: string, modifier: string | undefined, offset: number, whole: string) => {
      const target = COLOR_MAP[token];
      if (!target) return match;
      return colorClass(utility, target, token, modifier, whole.slice(tokenStart(whole, offset), offset));
    },
  );
  out = out.replace(VAR_RE, (match, token: string, fallback: string | undefined, offset: number, whole: string) => {
    const target = VAR_MAP[token];
    if (!target) return match;
    const start = tokenStart(whole, offset);
    const end = tokenEnd(whole, offset + match.length);
    const inArbitraryClass =
      whole.slice(start, offset).includes("[") && whole.slice(offset + match.length, end).includes("]");
    return colorValue(target, inArbitraryClass, fallback);
  });
  out = out.replace(SOLID_GRADIENT_RE, "$1");
  return out.replace(BACKGROUND_PROPERTY_RE, "bg-$1");
}

function rewriteArbitraryRadius(text: string): string {
  return text.replace(
    ARBITRARY_RADIUS_RE,
    (_match, side: string | undefined, px: string) => `rounded${side ?? ""}-${radiusTier(Number(px))}`,
  );
}

function parseSource(text: string, fileName: string): ts.SourceFile {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, kind);
}

/** 只在字符串字面量里改不带档位的 rounded，避开同名变量与注释里的英文单词 */
function rewriteBareRadius(text: string, fileName: string): string {
  const source = parseSource(text, fileName);
  const ranges: Array<[number, number]> = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      ranges.push([node.getStart(source), node.end]);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  let out = text;
  for (const [start, end] of ranges.sort((a, b) => b[0] - a[0])) {
    const segment = out.slice(start, end);
    const rewritten = segment.replace(BARE_RADIUS_RE, (_match, side: string | undefined) => `rounded${side ?? ""}-sm`);
    if (rewritten !== segment) out = out.slice(0, start) + rewritten + out.slice(end);
  }
  return out;
}

function transformSource(text: string, fileName: string): string {
  const isScript = /\.tsx?$/.test(fileName);
  let out = isScript ? rewriteBareRadius(text, fileName) : text;
  out = rewriteColors(out);
  return rewriteArbitraryRadius(out);
}

const LEFTOVER_RES: ReadonlyArray<readonly [string, RegExp]> = [
  ["旧颜色变量", new RegExp(String.raw`--color-(?:${OLD_TOKENS})(?![\w:-])|var\(--color-(?:good|warn)\)`, "g")],
  ["旧颜色工具类", UTILITY_RE],
  ["任意值圆角", /rounded(?:-[a-z]+)?-\[[^\]]+\]/g],
];

function leftovers(text: string): string[] {
  const found: string[] = [];
  const lines = text.split("\n");
  lines.forEach((line, index) => {
    for (const [label, re] of LEFTOVER_RES) {
      for (const match of line.matchAll(new RegExp(re.source, "g"))) {
        found.push(`${index + 1}: ${label} ${match[0]}`);
      }
    }
  });
  return found;
}

/** 多个旧色合并为同一语义色后，原本按状态取色的三元表达式两支可能变得相同（a ? "x" : "x"） */
function sameBranchTernaries(text: string, fileName: string): string[] {
  const source = parseSource(text, fileName);
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isConditionalExpression(node) &&
      ts.isStringLiteralLike(node.whenTrue) &&
      ts.isStringLiteralLike(node.whenFalse) &&
      node.whenTrue.text === node.whenFalse.text
    ) {
      const { line } = source.getLineAndCharacterOfPosition(node.getStart(source));
      found.push(`${line + 1}: 两支同值的三元表达式 ${JSON.stringify(node.whenTrue.text)}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

const FRONTEND_ROOT = path.resolve(import.meta.dirname, "..");
const SKIPPED_DIRS = ["src/components/ui", "src/i18n", "node_modules"].map((dir) => path.join(FRONTEND_ROOT, dir));

function collectFiles(entry: string, files: string[]): void {
  if (SKIPPED_DIRS.some((dir) => entry === dir || entry.startsWith(`${dir}${path.sep}`))) return;
  if (statSync(entry).isDirectory()) {
    for (const child of readdirSync(entry)) collectFiles(path.join(entry, child), files);
  } else if (/\.(tsx?|css)$/.test(entry)) {
    files.push(entry);
  }
}

function main(): void {
  const args = process.argv.slice(2);
  const check = args.includes("--check");
  const targets = args.filter((arg) => arg !== "--check");
  const files: string[] = [];
  for (const target of targets.length > 0 ? targets : ["src"]) collectFiles(path.resolve(target), files);

  const changed: string[] = [];
  const warnings: string[] = [];
  for (const file of files) {
    const before = readFileSync(file, "utf8");
    const after = transformSource(before, file);
    const relative = path.relative(FRONTEND_ROOT, file);
    if (after !== before) {
      changed.push(relative);
      if (!check) writeFileSync(file, after);
    }
    const pending = leftovers(after);
    if (/\.tsx?$/.test(file)) pending.push(...sameBranchTernaries(after, file));
    for (const line of pending) warnings.push(`${relative}:${line}`);
  }

  for (const file of changed) console.log(`${check ? "将改写" : "已改写"} ${file}`);
  for (const warning of warnings) console.warn(`未处理 ${warning}`);
  console.log(`${files.length} 个文件，${changed.length} 个${check ? "需要" : "已"}改写，${warnings.length} 处需人工处理。`);
  if (check && changed.length > 0) process.exitCode = 1;
}

main();
