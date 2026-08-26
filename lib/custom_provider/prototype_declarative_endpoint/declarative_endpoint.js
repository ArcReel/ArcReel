// PROTOTYPE — 自定义调用端点声明式定义的纯逻辑模块（#2123）。
// 不碰 DOM。页面（demo_shell.html）只调用这里暴露的函数。
// 覆盖：JSONPath 子集解析/求值、优先级数组提取、{{ }} 模板渲染、$each 铺字段、
// 枚举字典、状态映射、定义语义校验、以及一个「提交 → 轮询」的纯 reducer。
/* eslint-disable */
const DeclarativeEndpoint = (() => {
  // ───────────────────────── JSONPath 子集 ─────────────────────────
  class PathError extends Error {
    constructor(message, pos) { super(message); this.name = "PathError"; this.pos = pos; }
  }
  const NOTHING = Symbol("nothing");

  function parsePath(src) {
    if (typeof src !== "string") throw new PathError("路径必须是字符串");
    let i = 0;
    const s = src;
    const peek = (n = 0) => s[i + n];
    const eof = () => i >= s.length;
    const fail = (m) => { throw new PathError(`${m}（位置 ${i}：${JSON.stringify(s)}）`, i); };
    if (s !== s.trim()) fail("路径首尾不得有空白");
    if (peek() !== "$") fail("路径必须以 $ 开头");
    i++;
    const segments = [];
    const isShorthandStart = (c) => /[A-Za-z_]/.test(c) || c.charCodeAt(0) > 127;
    const isShorthandChar = (c) => /[A-Za-z0-9_]/.test(c) || c.charCodeAt(0) > 127;
    const skipWs = () => { while (!eof() && /\s/.test(peek())) i++; };
    const readShorthand = () => {
      if (eof() || !isShorthandStart(peek())) fail("点号后须跟成员名（含 - 或数字开头的键请用 ['…'] 记法）");
      let start = i;
      while (!eof() && isShorthandChar(peek())) i++;
      return s.slice(start, i);
    };
    const readString = () => {
      const quote = peek(); i++;
      let out = "";
      while (!eof() && peek() !== quote) {
        if (peek() === "\\") { i++; const c = peek(); i++;
          const map = { n: "\n", t: "\t", r: "\r", b: "\b", f: "\f", "/": "/", "\\": "\\", "'": "'", '"': '"' };
          if (c === "u") { out += String.fromCharCode(parseInt(s.slice(i, i + 4), 16)); i += 4; }
          else if (c in map) out += map[c]; else fail("非法转义");
        } else { out += peek(); i++; }
      }
      if (eof()) fail("字符串未闭合");
      i++;
      return out;
    };
    const readInt = () => {
      let start = i;
      if (peek() === "-") i++;
      if (eof() || !/[0-9]/.test(peek())) fail("期望整数");
      while (!eof() && /[0-9]/.test(peek())) i++;
      const txt = s.slice(start, i);
      if (/^-?0[0-9]/.test(txt) || txt === "-0") fail("整数不得有前导 0 或写成 -0");
      return parseInt(txt, 10);
    };
    const readNumber = () => {
      let start = i;
      if (peek() === "-") i++;
      while (!eof() && /[0-9.eE+-]/.test(peek())) i++;
      const n = Number(s.slice(start, i));
      if (Number.isNaN(n)) fail("非法数字");
      return n;
    };
    // 过滤器表达式：or → and → basic
    const parseSingular = () => {
      if (peek() !== "@") fail("过滤器只允许以 @ 起头的单值查询");
      i++;
      const segs = [];
      for (;;) {
        if (peek() === "." ) {
          if (peek(1) === ".") fail("过滤器内禁用 .. 递归");
          i++;
          if (peek() === "*") fail("过滤器内禁用通配（非单值查询）");
          segs.push({ kind: "name", name: readShorthand() });
        } else if (peek() === "[") {
          i++; skipWs();
          if (peek() === "'" || peek() === '"') segs.push({ kind: "name", name: readString() });
          else if (peek() === "-" || /[0-9]/.test(peek() || "")) segs.push({ kind: "index", index: readInt() });
          else fail("过滤器内的 @ 查询只允许名字 / 下标段");
          skipWs();
          if (peek() !== "]") fail("期望 ]");
          i++;
        } else break;
      }
      return { kind: "singular", segs };
    };
    const parseLiteral = () => {
      const c = peek();
      if (c === "'" || c === '"') return { kind: "lit", value: readString() };
      if (c === "-" || /[0-9]/.test(c)) return { kind: "lit", value: readNumber() };
      if (s.startsWith("true", i)) { i += 4; return { kind: "lit", value: true }; }
      if (s.startsWith("false", i)) { i += 5; return { kind: "lit", value: false }; }
      if (s.startsWith("null", i)) { i += 4; return { kind: "lit", value: null }; }
      if (c === "$") fail("过滤器内禁止引用根 $");
      if (/[A-Za-z_]/.test(c || "")) fail("过滤器内禁用函数扩展与非 RFC 关键字（length/count/match/search/value/and/or）");
      fail("非法过滤器操作数");
    };
    const parseOperand = () => (peek() === "@" ? parseSingular() : parseLiteral());
    const parseBasic = () => {
      skipWs();
      if (peek() === "!") { i++; return { kind: "not", expr: parseBasic() }; }
      if (peek() === "(") { i++; const e = parseOr(); skipWs(); if (peek() !== ")") fail("期望 )"); i++; return e; }
      const left = parseOperand();
      skipWs();
      const ops = ["==", "!=", "<=", ">=", "<", ">"];
      const op = ops.find((o) => s.startsWith(o, i));
      if (!op) {
        if (left.kind !== "singular") fail("字面量不能单独作为存在性测试");
        return { kind: "test", query: left };
      }
      if (s.startsWith("=~", i)) fail("禁用 =~ 正则扩展");
      i += op.length; skipWs();
      const right = parseOperand();
      return { kind: "cmp", op, left, right };
    };
    const parseAnd = () => {
      let left = parseBasic(); skipWs();
      while (s.startsWith("&&", i)) { i += 2; const right = parseBasic(); left = { kind: "and", left, right }; skipWs(); }
      return left;
    };
    const parseOr = () => {
      let left = parseAnd(); skipWs();
      while (s.startsWith("||", i)) { i += 2; const right = parseAnd(); left = { kind: "or", left, right }; skipWs(); }
      return left;
    };

    while (!eof()) {
      if (peek() === ".") {
        if (peek(1) === ".") fail("禁用 .. 递归下降");
        i++;
        if (peek() === "*") { i++; segments.push({ kind: "wildcard" }); continue; }
        segments.push({ kind: "name", name: readShorthand() });
        continue;
      }
      if (peek() !== "[") fail("段必须以 . 或 [ 开头");
      i++; skipWs();
      const c = peek();
      if (c === "'" || c === '"') {
        segments.push({ kind: "name", name: readString() });
      } else if (c === "*") {
        i++; segments.push({ kind: "wildcard" });
      } else if (c === "?") {
        i++; const expr = parseOr(); segments.push({ kind: "filter", expr });
      } else if (c === ":" || c === "-" || /[0-9]/.test(c || "")) {
        let start = null, end = null;
        if (c !== ":") start = readInt();
        skipWs();
        if (peek() === ":") {
          i++; skipWs();
          if (peek() === "-" || /[0-9]/.test(peek() || "")) end = readInt();
          skipWs();
          if (peek() === ":") fail("禁用切片 step");
          segments.push({ kind: "slice", start, end });
        } else {
          segments.push({ kind: "index", index: start });
        }
      } else {
        fail("括号内只允许 'name' / 整数 / * / a:b / ?expr");
      }
      skipWs();
      if (peek() === ",") fail("禁用联合选择器 [a, b]");
      if (peek() !== "]") fail("期望 ]");
      i++;
    }
    return { src, segments };
  }

  const canon = (path) => "$" + path.map((p) => (typeof p === "number" ? `[${p}]` : `['${String(p).replace(/'/g, "\\'")}']`)).join("");

  function evalSingular(q, node) {
    let cur = node;
    for (const seg of q.segs) {
      if (seg.kind === "name") {
        if (cur && typeof cur === "object" && !Array.isArray(cur) && Object.prototype.hasOwnProperty.call(cur, seg.name)) cur = cur[seg.name];
        else return NOTHING;
      } else {
        if (!Array.isArray(cur)) return NOTHING;
        const idx = seg.index < 0 ? cur.length + seg.index : seg.index;
        if (idx < 0 || idx >= cur.length) return NOTHING;
        cur = cur[idx];
      }
    }
    return cur;
  }
  const deepEq = (a, b) => {
    if (a === NOTHING || b === NOTHING) return a === b;
    if (a === b) return true;
    if (typeof a !== typeof b || a === null || b === null) return false;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    if (typeof a === "object") {
      const ka = Object.keys(a), kb = Object.keys(b);
      if (ka.length !== kb.length) return false;
      return ka.every((k) => deepEq(a[k], b[k]));
    }
    return false;
  };
  function evalExpr(e, node) {
    switch (e.kind) {
      case "or": return evalExpr(e.left, node) || evalExpr(e.right, node);
      case "and": return evalExpr(e.left, node) && evalExpr(e.right, node);
      case "not": return !evalExpr(e.expr, node);
      case "test": return evalSingular(e.query, node) !== NOTHING;
      case "cmp": {
        const l = e.left.kind === "lit" ? e.left.value : evalSingular(e.left, node);
        const r = e.right.kind === "lit" ? e.right.value : evalSingular(e.right, node);
        switch (e.op) {
          case "==": return deepEq(l, r);
          case "!=": return !deepEq(l, r);
          default: {
            const both = (t) => typeof l === t && typeof r === t;
            if (!(both("number") || both("string"))) return false;
            if (e.op === "<") return l < r;
            if (e.op === "<=") return l <= r;
            if (e.op === ">") return l > r;
            return l >= r;
          }
        }
      }
    }
    return false;
  }

  function evalPath(ast, root) {
    let nodes = [{ value: root, path: [] }];
    const warnings = [];
    for (const seg of ast.segments) {
      const next = [];
      for (const n of nodes) {
        const v = n.value;
        const isObj = v && typeof v === "object" && !Array.isArray(v);
        const isArr = Array.isArray(v);
        if (seg.kind === "name") {
          if (isObj && Object.prototype.hasOwnProperty.call(v, seg.name)) next.push({ value: v[seg.name], path: [...n.path, seg.name] });
        } else if (seg.kind === "index") {
          if (isArr) { const idx = seg.index < 0 ? v.length + seg.index : seg.index; if (idx >= 0 && idx < v.length) next.push({ value: v[idx], path: [...n.path, idx] }); }
        } else if (seg.kind === "wildcard") {
          if (isArr) v.forEach((x, k) => next.push({ value: x, path: [...n.path, k] }));
          else if (isObj) {
            const keys = Object.keys(v);
            if (keys.length > 1) warnings.push(`对象通配命中 ${keys.length} 个成员：JS 会把整数样键升序重排，前端预览与后端顺序可能不同`);
            keys.forEach((k) => next.push({ value: v[k], path: [...n.path, k] }));
          }
        } else if (seg.kind === "slice") {
          if (isArr) {
            const len = v.length;
            const norm = (x, d) => (x === null ? d : x < 0 ? Math.max(len + x, 0) : Math.min(x, len));
            const st = norm(seg.start, 0), en = norm(seg.end, len);
            for (let k = st; k < en; k++) next.push({ value: v[k], path: [...n.path, k] });
          }
        } else if (seg.kind === "filter") {
          const children = isArr ? v.map((x, k) => [k, x]) : isObj ? Object.entries(v) : [];
          if (isObj && children.length > 1) warnings.push("过滤器作用于对象成员：命中多个时顺序在前后端可能不同");
          for (const [k, x] of children) if (evalExpr(seg.expr, x)) next.push({ value: x, path: [...n.path, k] });
        }
      }
      nodes = next;
    }
    return { nodes: nodes.map((n) => ({ value: n.value, canonical: canon(n.path) })), warnings };
  }

  // ───────────────────────── 优先级数组提取 ─────────────────────────
  function normalizeExtractSpec(spec) {
    if (Array.isArray(spec)) return { paths: spec, accept: "string", json_decode: false, then: null };
    return { paths: spec.paths, accept: spec.accept || "string", json_decode: !!spec.json_decode, then: spec.then || null };
  }
  const acceptable = (v, accept) => {
    if (accept === "scalar") return (typeof v === "string" && v.trim() !== "") || typeof v === "number" || typeof v === "boolean";
    return typeof v === "string" && v.trim() !== "";
  };
  const typeName = (v) => (v === null ? "null" : Array.isArray(v) ? "array" : typeof v);

  function extract(spec, response) {
    const n = normalizeExtractSpec(spec);
    const root = response.body; // $ 一律指向响应体
    const trace = [];
    const runPaths = (paths, target, accept) => {
      for (const p of paths) {
        let ast;
        try { ast = parsePath(p); } catch (e) { trace.push({ path: p, error: e.message }); continue; }
        const { nodes, warnings } = evalPath(ast, target);
        const first = nodes.find((x) => acceptable(x.value, accept));
        const row = { path: p, count: nodes.length, warnings, accepted: !!first };
        if (nodes.length && !first) row.skipped = `命中 ${nodes.length} 个但类型不可接受（${nodes.map((x) => typeName(x.value)).join(", ")}）`;
        if (first && nodes.length > 1) row.note = `命中 ${nodes.length} 个，取第一个 ${first.canonical}`;
        trace.push(row);
        if (first) return { value: first.value, canonical: first.canonical, path: p };
      }
      return null;
    };
    // json_decode：第一跳的命中必须是字符串，解析后按 then 继续
    let hit = runPaths(n.paths, root, n.json_decode ? "string" : n.accept);
    if (hit && n.json_decode) {
      let parsed;
      try { parsed = JSON.parse(hit.value); } catch (e) { trace.push({ path: "(json_decode)", error: `命中值不是合法 JSON：${e.message}` }); return { value: null, hit: null, trace }; }
      trace.push({ path: "(json_decode)", note: `已把 ${hit.canonical} 的字符串解析为 ${typeName(parsed)}` });
      hit = runPaths(n.then || ["$"], parsed, n.accept);
    }
    return { value: hit ? hit.value : null, hit, trace };
  }
  const lowerKeys = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k.toLowerCase(), v]));

  // ───────────────────────── 模板渲染 ─────────────────────────
  class RenderError extends Error { constructor(m) { super(m); this.name = "RenderError"; } }
  const DROP = Symbol("drop");
  const PLACEHOLDER = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}/g;
  const WHOLE = /^\s*\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}\s*$/;

  function lookup(ctx, name) {
    const parts = name.split(".");
    let cur = ctx;
    for (const p of parts) {
      if (cur && typeof cur === "object" && Object.prototype.hasOwnProperty.call(cur, p)) cur = cur[p];
      else return { found: false, value: undefined };
    }
    return { found: true, value: cur };
  }
  function resolveVar(name, ctx, enumMaps, trace) {
    const { found, value } = lookup(ctx, name);
    if (!found) throw new RenderError(`占位符 {{ ${name} }} 引用了未声明的变量`);
    if (enumMaps && Object.prototype.hasOwnProperty.call(enumMaps, name)) {
      if (value === null || value === undefined) return null;
      const key = String(value);
      if (!Object.prototype.hasOwnProperty.call(enumMaps[name], key)) {
        throw new RenderError(`枚举映射缺项：${name} = ${JSON.stringify(value)} 不在 enum_maps.${name} 中（已登记：${Object.keys(enumMaps[name]).join(", ")}）——不发请求`);
      }
      trace && trace.push(`enum_maps.${name}: ${JSON.stringify(value)} → ${JSON.stringify(enumMaps[name][key])}`);
      return enumMaps[name][key];
    }
    return value === undefined ? null : value;
  }
  const stringify = (v) => (typeof v === "string" ? v : typeof v === "object" ? JSON.stringify(v) : String(v));

  function renderString(tpl, ctx, enumMaps, trace) {
    const whole = tpl.match(WHOLE);
    if (whole) {
      const v = resolveVar(whole[1], ctx, enumMaps, trace);
      if (v === null) { trace && trace.push(`{{ ${whole[1]} }} 为空 → 删除所在键/元素`); return DROP; }
      return v; // 原生类型保留
    }
    return tpl.replace(PLACEHOLDER, (_, name) => {
      const v = resolveVar(name, ctx, enumMaps, trace);
      if (v === null) throw new RenderError(`混合文本 ${JSON.stringify(tpl)} 中的 {{ ${name} }} 为空`);
      return stringify(v);
    });
  }
  function renderNode(node, ctx, enumMaps, trace) {
    if (typeof node === "string") return renderString(node, ctx, enumMaps, trace);
    if (node === null || typeof node !== "object") return node;
    if (Array.isArray(node)) {
      const out = [];
      for (const el of node) {
        if (el && typeof el === "object" && !Array.isArray(el) && Object.keys(el).length === 1 && el.$each) {
          const d = el.$each;
          if (!("item" in d)) throw new RenderError("数组位置的 $each 必须提供 item");
          const list = resolveVar(d.in, ctx, null, trace) || [];
          if (!Array.isArray(list)) throw new RenderError(`$each.in = ${d.in} 不是列表`);
          trace && trace.push(`$each ${d.in} 展开 ${list.length} 项（数组元素）`);
          list.forEach((item, index) => {
            const v = renderNode(d.item, { ...ctx, [d.as]: item, index }, enumMaps, trace);
            if (v !== DROP) out.push(v);
          });
          continue;
        }
        const v = renderNode(el, ctx, enumMaps, trace);
        if (v !== DROP) out.push(v);
      }
      return out;
    }
    const out = {};
    for (const [k, v] of Object.entries(node)) {
      if (k === "$each") {
        const d = v;
        if (!("key" in d && "value" in d)) throw new RenderError("对象位置的 $each 必须提供 key 与 value");
        const list = resolveVar(d.in, ctx, null, trace) || [];
        if (!Array.isArray(list)) throw new RenderError(`$each.in = ${d.in} 不是列表`);
        trace && trace.push(`$each ${d.in} 展开 ${list.length} 项（键值对）`);
        list.forEach((item, index) => {
          const scope = { ...ctx, [d.as]: item, index };
          const key = renderString(d.key, scope, null, trace);
          const val = renderNode(d.value, scope, enumMaps, trace);
          if (key !== DROP && val !== DROP) out[stringify(key)] = val;
        });
        continue;
      }
      const r = renderNode(v, ctx, enumMaps, trace);
      if (r !== DROP) out[k] = r;
    }
    return out;
  }
  function renderRequest(req, ctx, enumMaps, auth) {
    const trace = [];
    const url = renderString(req.url, ctx, null, trace);
    if (url === DROP || typeof url !== "string") throw new RenderError("URL 模板渲染结果不是字符串");
    // auth 节优先：请求模板里的同名 header（不区分大小写）/ query 被 auth 覆盖，并记入 trace
    const headers = {};
    const authHeaderKeys = new Set(Object.keys(auth && auth.headers || {}).map((k) => k.toLowerCase()));
    for (const [k, v] of Object.entries(req.headers || {})) {
      if (authHeaderKeys.has(k.toLowerCase())) { trace.push(`header ${k}：与 auth 同名，以 auth 为准`); continue; }
      const r = renderString(v, ctx, null, trace); if (r !== DROP) headers[k] = stringify(r);
    }
    for (const [k, v] of Object.entries(auth && auth.headers || {})) {
      const r = renderString(v, ctx, null, trace); if (r !== DROP) headers[k] = stringify(r);
    }
    // 请求级 query 已移除：静态 / 动态 query 写进 url 模板；只有 auth.query 单独渲染（percent-encoding）
    const query = {};
    for (const [k, v] of Object.entries(auth && auth.query || {})) {
      const r = renderString(v, ctx, null, trace); if (r !== DROP) query[k] = stringify(r);
    }
    const qs = Object.entries(query).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
    const body = req.body === undefined ? undefined : renderNode(req.body, ctx, enumMaps, trace);
    return { method: req.method, url: qs ? `${url}${url.includes("?") ? "&" : "?"}${qs}` : url, headers, body: body === DROP ? undefined : body, trace };
  }

  // ───────────────────────── 素材编码 ─────────────────────────
  function encodeInputs(inputsDecl, assets) {
    const out = {}; const notes = [];
    for (const [name, decl] of Object.entries(inputsDecl || {})) {
      const raw = assets[decl.source];
      const enc = (a) => {
        return decl.encoding === "data_uri" ? `data:${a.mime};base64,${a.base64}` : a.base64;
      };
      if (raw === undefined || raw === null) { out[name] = null; notes.push(`inputs.${name} ← ${decl.source}：未提供 → null`); continue; }
      if (Array.isArray(raw)) { out[name] = raw.map(enc); notes.push(`inputs.${name} ← ${decl.source}：${raw.length} 项，${decl.encoding}`); }
      else { out[name] = enc(raw); notes.push(`inputs.${name} ← ${decl.source}：${decl.encoding}`); }
    }
    return { inputs: out, notes };
  }

  // ───────────────────────── 状态映射 ─────────────────────────
  const BUILTIN_STATUS_SYNONYMS = {
    completed: "succeeded", succeeded: "succeeded", succeed: "succeeded", success: "succeeded",
    failed: "failed", fail: "failed", error: "failed", canceled: "failed", cancelled: "failed",
    expired: "failed", not_found: "failed", timeout: "failed", timed_out: "failed",
    generating: "running", in_progress: "running", running: "running", processing: "running",
    queued: "queued", queueing: "queued", preparing: "queued", submitted: "queued", pending: "queued", created: "queued",
  };
  // 与 ProviderJobStatus 同名的四个字面量；运行时只区分 继续轮询(queued|running) / succeeded / failed。
  // expired 不由声明式产生（手写模板的 404 多半是 URL 写错），backend 按 v2 先例折进 failed。
  const CANONICAL_STATUSES = ["queued", "running", "succeeded", "failed"];
  function mapStatus(raw, statusMap) {
    if (raw === null || raw === undefined) return { raw, key: null, source: "missing", canonical: "running" };
    const key = String(raw).trim().toLowerCase();
    let hit = null;
    if (statusMap && Object.prototype.hasOwnProperty.call(statusMap, key)) hit = { raw, key, source: "status_map", canonical: statusMap[key] };
    else if (Object.prototype.hasOwnProperty.call(BUILTIN_STATUS_SYNONYMS, key)) hit = { raw, key, source: "builtin", canonical: BUILTIN_STATUS_SYNONYMS[key] };
    else return { raw, key, source: "fallback", canonical: "running" };
    // 四档之外的目标一律折为 failed，不让未知终态漏进轮询循环
    if (!CANONICAL_STATUSES.includes(hit.canonical)) hit = { ...hit, source: hit.source + "→coerced", canonical: "failed" };
    return hit;
  }

  // ───────────────────────── 定义语义校验 ─────────────────────────
  const BASE_VARS = ["base_url", "model", "prompt", "duration", "aspect_ratio", "resolution", "generate_audio", "seed"];
  const LIST_SOURCES = new Set(["reference_images", "reference_audio_files"]);
  const REQUEST_KEYS = new Set(["method", "url", "headers", "body", "extract"]);
  const REMOVED_REQUEST_KEYS = { query: "写进 url 模板（auth.query 仍可用）", success_status_codes: "任何 2xx 即成功", retry_status_codes: "运行时策略（429 / 5xx 重试）", expired_status_codes: "expired 不由声明式产生", interval_seconds: "运行时策略", timeout_seconds: "运行时策略" };
  // 能力推导：first_frame / last_frame 由 inputs 来源得出，其余取声明值或默认
  function deriveCapabilities(def) {
    const inputs = Object.values(def.inputs || {});
    return {
      first_frame: inputs.some((d) => d.source === "start_image"),
      last_frame: inputs.some((d) => d.source === "end_image"),
      max_reference_images: 0, reference_audio_mode: "none", max_reference_audio_count: 0, max_reference_audio_total_seconds: null,
      reference_audio_per_image: false, max_prompt_chars: null, first_frame_ratio_adaptive_only: false, audio_track: "controllable", reference_route_audio_track: null,
      ...(def.capabilities || {}),
    };
  }
  function validateDefinition(def) {
    const issues = [];
    const err = (where, message) => issues.push({ level: "error", where, message });
    const warn = (where, message) => issues.push({ level: "warning", where, message });
    if (!def || typeof def !== "object") { err("$", "定义不是对象"); return issues; }
    if (def.kind !== "declarative") err("kind", `未知 kind ${JSON.stringify(def.kind)}，导入拒绝`);
    const inputs = def.inputs || {};
    const inputVars = Object.keys(inputs).map((k) => `inputs.${k}`);
    const listInputs = new Set(Object.entries(inputs).filter(([, d]) => LIST_SOURCES.has(d.source)).map(([k]) => `inputs.${k}`));
    // 占位符扫描
    const scan = (node, where, scope) => {
      if (typeof node === "string") {
        for (const m of node.matchAll(PLACEHOLDER)) {
          const name = m[1];
          if (name === "api_key") { if (!scope.authSection) err(where, "{{ api_key }} 只允许出现在 auth 节（分享文件不含凭证，请求体/URL 不得携带）"); continue; }
          if (name === "task_id") { if (!scope.poll) err(where, "{{ task_id }} 只在 poll 节可用"); continue; }
          if (scope.locals.includes(name)) continue;
          if (BASE_VARS.includes(name)) continue;
          if (inputVars.includes(name)) {
            if (listInputs.has(name) && !scope.locals.length && !scope.eachIn) err(where, `${name} 是列表型素材，只能经 $each 展开，不能直接内插`);
            continue;
          }
          err(where, `占位符 {{ ${name} }} 引用了未声明的变量`);
        }
        return;
      }
      if (Array.isArray(node)) { node.forEach((el, k) => scan(el, `${where}[${k}]`, scope)); return; }
      if (node && typeof node === "object") {
        for (const [k, v] of Object.entries(node)) {
          if (k === "$each") {
            if (!v || typeof v !== "object") { err(`${where}.$each`, "$each 必须是对象"); continue; }
            if (!listInputs.has(v.in)) err(`${where}.$each.in`, `${JSON.stringify(v.in)} 不是已声明的列表型素材（reference_images / reference_audio_files）`);
            const inner = { ...scope, locals: [...scope.locals, v.as, "index"], eachIn: v.in };
            if ("item" in v) scan(v.item, `${where}.$each.item`, inner);
            if ("key" in v) scan(v.key, `${where}.$each.key`, inner);
            if ("value" in v) scan(v.value, `${where}.$each.value`, inner);
            continue;
          }
          scan(v, `${where}.${k}`, scope);
        }
      }
    };
    const baseScope = { locals: [], authSection: false, poll: false };
    scan(def.auth || {}, "auth", { ...baseScope, authSection: true });
    for (const sec of ["submit", "poll"]) {
      const r = def[sec]; if (!r) { err(sec, "缺少该节"); continue; }
      for (const k of Object.keys(r)) if (!REQUEST_KEYS.has(k)) err(`${sec}.${k}`, k in REMOVED_REQUEST_KEYS ? `字段已移除：${REMOVED_REQUEST_KEYS[k]}` : "未知字段");
      const scope = { ...baseScope, poll: sec === "poll" };
      for (const f of ["url", "headers", "body"]) if (r[f] !== undefined) scan(r[f], `${sec}.${f}`, scope);
      if (sec === "poll" && !JSON.stringify([r.url, r.body]).includes("task_id")) warn("poll", "轮询请求没有引用 {{ task_id }}——确认这是有意的");
      // 与 auth 节同名的 header / query：凭证只能由 auth 节写入，请求模板不得覆盖
      const authHeaderKeys = new Set(Object.keys((def.auth || {}).headers || {}).map((k) => k.toLowerCase()));
      for (const k of Object.keys(r.headers || {})) if (authHeaderKeys.has(k.toLowerCase())) err(`${sec}.headers.${k}`, `与 auth.headers 同名（不区分大小写）——凭证 header 只能由 auth 节写入`);
      // 路径子集
      const walkExtract = (spec, where) => {
        if (spec && !Array.isArray(spec) && typeof spec === "object" && "source" in spec) err(`${where}.source`, "字段已移除：$ 一律指向响应体，HTTP 状态码不走 JSONPath");
        const n = normalizeExtractSpec(spec);
        for (const p of [...n.paths, ...(n.then || [])]) {
          try { const ast = parsePath(p); if (ast.segments.some((s) => s.kind === "wildcard")) warn(where, `${p} 含通配：命中多个时顺序在前端预览与后端可能不同`); }
          catch (e) { err(where, `${p}：${e.message}`); }
        }
        if (n.then && !n.json_decode) err(where, "then 只在 json_decode=true 时有意义");
      };
      for (const [k, v] of Object.entries(r.extract || {})) {
        if (k === "usage") for (const [uk, uv] of Object.entries(v)) walkExtract(uv, `${sec}.extract.usage.${uk}`);
        else walkExtract(v, `${sec}.extract.${k}`);
      }
    }
    for (const [k, v] of Object.entries(def.status_map || {})) if (!CANONICAL_STATUSES.includes(v)) err(`status_map.${k}`, `目标档位 ${JSON.stringify(v)} 不在 queued / running / succeeded / failed 内（expired 不由声明式产生，请映射到 failed）`);
    for (const k of ["first_frame", "last_frame"]) if (def.capabilities && k in def.capabilities) err(`capabilities.${k}`, "字段已移除：由 inputs 是否含 start_image / end_image 来源推导");
    for (const k of ["mime_types"]) for (const [name, d] of Object.entries(inputs)) if (d && k in d) err(`inputs.${name}.${k}`, "字段已移除：素材格式不做白名单，由供应商在提交时拒绝");
    if (def.meta && "media_type" in def.meta) err("meta.media_type", "字段已移除：首期只有 video，零信息");
    for (const [k, v] of Object.entries(def.enum_maps || {})) if (!["duration", "aspect_ratio", "resolution", "generate_audio"].includes(k)) err(`enum_maps.${k}`, "只允许对 duration / aspect_ratio / resolution / generate_audio 做枚举映射");
    const caps = def.capabilities || {};
    if ((caps.max_reference_images || 0) > 0 && !Object.values(inputs).some((d) => d.source === "reference_images")) warn("capabilities.max_reference_images", "声明接受参考图，但 inputs 没有 reference_images 来源");
    return issues;
  }

  // ───────────────────────── 提交 → 轮询 reducer ─────────────────────────
  const RUNTIME_RETRY_STATUS_CODES = new Set([408, 425, 429, 500, 502, 503, 504]);
  function buildContext(def, params, assets) {
    const { inputs, notes } = encodeInputs(def.inputs, assets);
    const ctx = { base_url: params.base_url, model: params.model, prompt: params.prompt, duration: params.duration, aspect_ratio: params.aspect_ratio,
      resolution: params.resolution ?? null, generate_audio: params.generate_audio, seed: params.seed ?? null, inputs };
    return { ctx, notes };
  }
  function initialState(def, params, assets) {
    return { def, params, assets, phase: "idle", log: [], submit: null, poll: null, taskId: null, status: null, videoUrl: null, error: null, usage: {}, pollCount: 0 };
  }
  function reduce(state, action) {
    const s = { ...state, log: [...state.log] };
    const say = (m) => s.log.push(m);
    switch (action.type) {
      case "RESET": return initialState(state.def, state.params, state.assets);
      case "SET_DEFINITION": return initialState(action.def, state.params, state.assets);
      case "SET_PARAMS": return { ...initialState(state.def, { ...state.params, ...action.params }, state.assets), log: [...state.log, "请求参数已更新（会话重置）"] };
      case "SET_ASSETS": return { ...initialState(state.def, state.params, { ...state.assets, ...action.assets }), log: [...state.log, "素材已更新（会话重置）"] };
      case "SUBMIT": {
        if (s.phase !== "idle") { say("已提交过，请先重置"); return s; }
        const { def, params, assets } = s;
        let ctx, notes;
        try { ({ ctx, notes } = buildContext(def, params, assets)); } catch (e) { s.phase = "failed"; s.error = e.message; say(`✗ 素材编码失败：${e.message}`); return s; }
        notes.forEach(say);
        let req;
        try { req = renderRequest(def.submit, { ...ctx, api_key: params.api_key }, def.enum_maps, def.auth); }
        catch (e) { s.phase = "failed"; s.error = e.message; say(`✗ 提交请求渲染失败（未发请求）：${e.message}`); return s; }
        req.trace.forEach(say);
        s.submit = { request: req, response: action.response, extract: {} };
        const ok = action.response.status_code >= 200 && action.response.status_code < 300;
        if (!ok) {
          const er = def.submit.extract.error ? extract(def.submit.extract.error, action.response) : { value: null, trace: [] };
          s.submit.extract.error = er;
          s.phase = "failed"; s.error = `HTTP ${action.response.status_code}：${er.value || "（错误路径无命中）"}`; say(`✗ 提交失败 ${s.error}`); return s;
        }
        const tid = extract(def.submit.extract.task_id, action.response);
        s.submit.extract.task_id = tid;
        if (!tid.hit) {
          const er = def.submit.extract.error ? extract(def.submit.extract.error, action.response) : { value: null, trace: [] };
          s.submit.extract.error = er;
          s.phase = "failed"; s.error = `提交成功码之下 task_id 无命中 ⇒ 按提交失败处理：${er.value || "（错误路径无命中）"}`; say(`✗ ${s.error}`); return s;
        }
        s.taskId = tid.value; s.phase = "submitted"; say(`✓ 提交成功，task_id = ${tid.value}（${tid.hit.path}）`); return s;
      }
      case "POLL": {
        if (!(s.phase === "submitted" || s.phase === "polling")) { say("还没有可轮询的任务"); return s; }
        const { def, params, assets } = s;
        const { ctx } = buildContext(def, params, assets);
        let req;
        try { req = renderRequest(def.poll, { ...ctx, api_key: params.api_key, task_id: s.taskId }, def.enum_maps, def.auth); }
        catch (e) { s.phase = "failed"; s.error = e.message; say(`✗ 轮询请求渲染失败：${e.message}`); return s; }
        s.pollCount += 1;
        const resp = action.response;
        // HTTP 层（运行时策略，不在定义文件里）：2xx 才看 body 状态；429 / 5xx 保持上一状态继续轮询；其余码 ⇒ 任务失败
        const code = resp.status_code;
        if (!(code >= 200 && code < 300)) {
          const prev = s.status ? s.status.canonical : "queued";
          if (RUNTIME_RETRY_STATUS_CODES.has(code)) {
            s.poll = { request: req, response: resp, extract: {}, usage: {}, mapped: { raw: `HTTP ${code}`, key: null, source: "runtime_retry", canonical: prev } };
            s.phase = "polling"; say(`… HTTP ${code} 是瞬时错误（运行时策略），保持 ${prev}，稍后再轮询`); return s;
          }
          const er = def.poll.extract.error ? extract(def.poll.extract.error, resp) : { value: null, trace: [] };
          s.poll = { request: req, response: resp, extract: { error: er }, usage: {}, mapped: { raw: `HTTP ${code}`, key: null, source: "http_non_2xx", canonical: "failed" } };
          s.status = s.poll.mapped;
          s.phase = "failed"; s.error = `HTTP ${code}：${er.value || "（错误路径无命中）"}`; say(`✗ 轮询响应非 2xx 且非瞬时错误 ⇒ 任务失败 ${s.error}`); return s;
        }
        const st = extract(def.poll.extract.status, resp);
        const mapped = mapStatus(st.hit ? st.value : null, def.status_map);
        const usage = {};
        for (const [k, spec] of Object.entries(def.poll.extract.usage || {})) usage[k] = extract(spec, resp);
        s.poll = { request: req, response: resp, extract: { status: st }, usage, mapped };
        s.usage = Object.fromEntries(Object.entries(usage).map(([k, v]) => [k, v.value]));
        s.status = mapped;
        say(`轮询 #${s.pollCount}：status 原值 ${JSON.stringify(mapped.raw)} → 查表键 ${JSON.stringify(mapped.key)} → ${mapped.canonical}（来源：${mapped.source}）`);
        if (mapped.canonical === "succeeded") {
          const vu = extract(def.poll.extract.video_url, resp);
          s.poll.extract.video_url = vu;
          if (!vu.hit) { s.phase = "failed"; s.error = "状态已成功但 video_url 优先级数组无命中 ⇒ 判失败（不下载）"; say(`✗ ${s.error}`); return s; }
          s.videoUrl = vu.value; s.phase = "done"; say(`✓ 完成，video_url = ${vu.value}（${vu.hit.path}）`); return s;
        }
        if (mapped.canonical === "failed") {
          const er = def.poll.extract.error ? extract(def.poll.extract.error, resp) : { value: null, trace: [] };
          s.poll.extract.error = er;
          s.phase = "failed"; s.error = er.value || "（错误路径无命中）"; say(`✗ 供应商任务失败：${s.error}`); return s;
        }
        s.phase = "polling"; say(`… ${mapped.canonical}，稍后再轮询`); return s;
      }
      default: return s;
    }
  }

  return { parsePath, evalPath, extract, normalizeExtractSpec, renderRequest, renderNode, encodeInputs, mapStatus, BUILTIN_STATUS_SYNONYMS, validateDefinition, deriveCapabilities, initialState, reduce, buildContext, PathError, RenderError, DROP };
})();
if (typeof module !== "undefined") module.exports = DeclarativeEndpoint;
