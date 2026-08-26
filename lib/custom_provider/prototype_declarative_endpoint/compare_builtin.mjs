// PROTOTYPE — 把 templates/builtin 下的声明式定义与 builtin_actual.json（内置 backend 实际发出的请求 /
// 解析结果，由 builtin_probe.py 生成）逐 case 对照（#2142）。
// 用法：node lib/custom_provider/prototype_declarative_endpoint/compare_builtin.mjs
// 每个 case：① 定义过 validateDefinition；② 用同一组参数 / 素材渲染 submit，与内置请求比 URL / 鉴权头 / body；
// ③ 渲染 poll URL 与内置轮询 URL 比；④ 对同一组响应夹具跑 reducer，与内置的 video_uri / 异常比结局。
// 结果打印为报告并写入 builtin_compare.json。
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const D = createRequire(import.meta.url)(join(here, "declarative_endpoint.js"));
const actual = JSON.parse(readFileSync(join(here, "builtin_actual.json"), "utf8"));

const short = (v) => {
  if (typeof v === "string" && v.length > 48) return v.slice(0, 32) + `…<${v.length} chars>`;
  return v;
};
const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
function deepDiff(builtin, declarative, path = "$", out = []) {
  if (isObj(builtin) && isObj(declarative)) {
    for (const k of new Set([...Object.keys(builtin), ...Object.keys(declarative)])) {
      if (!(k in builtin)) out.push({ path: `${path}.${k}`, builtin: "<缺>", declarative: short(declarative[k]) });
      else if (!(k in declarative)) out.push({ path: `${path}.${k}`, builtin: short(builtin[k]), declarative: "<缺>" });
      else deepDiff(builtin[k], declarative[k], `${path}.${k}`, out);
    }
    return out;
  }
  if (Array.isArray(builtin) && Array.isArray(declarative)) {
    const n = Math.max(builtin.length, declarative.length);
    for (let i = 0; i < n; i++) {
      if (i >= builtin.length) out.push({ path: `${path}[${i}]`, builtin: "<缺>", declarative: short(JSON.stringify(declarative[i])) });
      else if (i >= declarative.length) out.push({ path: `${path}[${i}]`, builtin: short(JSON.stringify(builtin[i])), declarative: "<缺>" });
      else deepDiff(builtin[i], declarative[i], `${path}[${i}]`, out);
    }
    return out;
  }
  if (JSON.stringify(builtin) !== JSON.stringify(declarative)) out.push({ path, builtin: short(builtin), declarative: short(declarative) });
  return out;
}

// builtin_probe 记录的素材（mime + 原始字节 base64）→ encodeInputs 需要的 {mime, base64}
function assetsFor(rec) {
  const out = {};
  for (const [source, v] of Object.entries(rec.assets || {})) {
    const conv = (a) => ({ mime: a.mime, base64: a.bytes_b64 });
    out[source] = Array.isArray(v) ? v.map(conv) : conv(v);
  }
  return out;
}

const report = [];
let pass = 0, gap = 0;
for (const rec of actual) {
  const row = { id: rec.id, template: rec.template, note: rec.note, builtin: rec.result ? { video_uri: rec.result.video_uri, duration_seconds: rec.result.duration_seconds, seed: rec.result.seed, usage_tokens: rec.result.usage_tokens } : { error: rec.error } };
  if (!rec.template) {
    row.verdict = "N/A";
    row.detail = rec.jwt_payload ? `JWT header=${JSON.stringify(rec.jwt_header)} payload keys=${Object.keys(rec.jwt_payload).join(",")}` : "无对应定义";
    report.push(row);
    continue;
  }
  const def = JSON.parse(readFileSync(join(here, "templates", "builtin", rec.template), "utf8"));
  row.validate = D.validateDefinition(def).filter((i) => i.level === "error").map((i) => `${i.where}: ${i.message}`);
  const params = rec.params;
  const assets = assetsFor(rec);
  const issues = [];
  // ② submit
  let ctx;
  try {
    ({ ctx } = D.buildContext(def, params, assets));
    const req = D.renderRequest(def.submit, { ...ctx, api_key: params.api_key }, def.enum_maps, def.auth);
    const sub = (rec.requests.submit || [])[0];
    if (!sub) {
      row.submit = { note: "内置未发出提交请求（本地校验先拒）", rendered_url: req.url };
    } else {
      const urlOk = req.url === sub.url;
      const hdrDiff = [];
      for (const [k, v] of Object.entries(req.headers)) {
        const bv = sub.headers[k.toLowerCase()];
        if (bv !== undefined && bv !== v && !bv.startsWith("Bearer <")) hdrDiff.push({ header: k, builtin: bv, declarative: v });
      }
      for (const k of Object.keys(sub.headers)) if (!Object.keys(req.headers).some((h) => h.toLowerCase() === k)) hdrDiff.push({ header: k, builtin: sub.headers[k], declarative: "<缺>" });
      const bodyDiff = deepDiff(sub.body, req.body);
      row.submit = { url_match: urlOk, url_builtin: sub.url, url_declarative: req.url, header_diff: hdrDiff, body_diff: bodyDiff };
      if (!urlOk) issues.push("submit.url");
      if (hdrDiff.length) issues.push("submit.headers");
      if (bodyDiff.length) issues.push("submit.body");
    }
  } catch (e) {
    row.submit = { render_error: `${e.name}: ${e.message}` };
    issues.push("submit.render");
  }
  // ③④ reducer over the same fixtures
  let s = D.initialState(def, params, assets);
  const submitFixture = (rec.fixtures.submit || [])[0];
  s = D.reduce(s, { type: "SUBMIT", response: { status_code: 200, body: submitFixture } });
  const pollFixtures = rec.fixtures.poll || [];
  let pollUrl = null;
  for (const body of pollFixtures) {
    if (s.phase !== "submitted" && s.phase !== "polling") break;
    s = D.reduce(s, { type: "POLL", response: { status_code: 200, body } });
    if (s.poll && s.poll.request) pollUrl = s.poll.request.url;
  }
  const builtinPoll = (rec.requests.poll || [])[0];
  row.poll = { url_builtin: builtinPoll ? builtinPoll.url : null, url_declarative: pollUrl, url_match: builtinPoll ? builtinPoll.url === pollUrl : null };
  if (builtinPoll && pollUrl && builtinPoll.url !== pollUrl) issues.push("poll.url");
  row.declarative = { phase: s.phase, status: s.status ? `${s.status.raw}→${s.status.canonical}(${s.status.source})` : null, video_url: s.videoUrl, error: s.error, usage: s.usage };
  const builtinOk = !!rec.result;
  const declOk = s.phase === "done";
  const builtinDownload = ((rec.requests.download || [])[0] || {}).url || null;
  row.builtin.download_url = builtinDownload;
  // 内置失败而声明式仍在轮询（业务码未被识别为终态）同样是结局分歧：会一路轮到运行时超时
  if (builtinOk !== declOk || (!builtinOk && s.phase !== "failed")) issues.push("outcome");
  else if (builtinOk && builtinDownload !== s.videoUrl) issues.push("video_url");
  if (row.validate.length) issues.push("validate");
  row.issues = issues;
  row.verdict = issues.length ? "GAP" : "PASS";
  if (issues.length) gap++; else pass++;
  report.push(row);
}

// ── 打印 ─────────────────────────────────────────────────────────
for (const r of report) {
  console.log(`\n[${r.verdict}] ${r.id}  (${r.template || "-"})${r.note ? "  — " + r.note : ""}`);
  if (r.verdict === "N/A") { console.log("   " + r.detail); continue; }
  if (r.validate.length) for (const v of r.validate) console.log("   validate: " + v);
  if (r.submit.render_error) console.log("   submit 渲染失败: " + r.submit.render_error);
  else if (r.submit.note) console.log("   " + r.submit.note);
  else {
    if (!r.submit.url_match) console.log(`   submit.url 内置=${r.submit.url_builtin} 声明式=${r.submit.url_declarative}`);
    for (const h of r.submit.header_diff) console.log(`   header ${h.header}: 内置=${h.builtin} 声明式=${h.declarative}`);
    for (const d of r.submit.body_diff) console.log(`   body ${d.path}: 内置=${JSON.stringify(d.builtin)} 声明式=${JSON.stringify(d.declarative)}`);
  }
  if (r.poll.url_match === false) console.log(`   poll.url 内置=${r.poll.url_builtin} 声明式=${r.poll.url_declarative}`);
  const b = r.builtin.error ? `失败「${r.builtin.error.slice(0, 90)}」` : `成功 下载 ${r.builtin.download_url}`;
  const d = r.declarative.phase === "done" ? `成功 ${r.declarative.video_url}` : `${r.declarative.phase}「${(r.declarative.error || "").slice(0, 90)}」`;
  console.log(`   结局 内置: ${b}`);
  console.log(`   结局 声明式: ${d}  status=${r.declarative.status}`);
  if (Object.keys(r.declarative.usage || {}).length) console.log(`   usage 声明式: ${JSON.stringify(r.declarative.usage)}`);
}
console.log(`\nPASS ${pass} / GAP ${gap} / N/A ${report.length - pass - gap}`);
writeFileSync(join(here, "builtin_compare.json"), JSON.stringify(report, null, 1) + "\n");
