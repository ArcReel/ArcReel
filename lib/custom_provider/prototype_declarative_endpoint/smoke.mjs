// PROTOTYPE — 纯模块冒烟测试：node lib/custom_provider/prototype_declarative_endpoint/smoke.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
const here = dirname(fileURLToPath(import.meta.url));
const D = createRequire(import.meta.url)(join(here, "declarative_endpoint.js"));
let fails = 0;
const t = (l, c) => { console.log((c ? "ok   " : "FAIL ") + l); if (!c) fails++; };
const ev = (p, o) => D.evalPath(D.parsePath(p), o).nodes.map((n) => n.value);
const resp = { data: [{ fileUrl: "a.png", fileType: "png" }, { fileUrl: "b.mp4", fileType: "mp4" }], code: 0, videos: [{ url: "u0" }, { url: "u1" }] };
t("filter", JSON.stringify(ev("$.data[?@.fileType == 'mp4'].fileUrl", resp)) === '["b.mp4"]');
t("neg index", ev("$.videos[-1].url", resp)[0] === "u1");
t("slice", ev("$.videos[0:1].url", resp).length === 1);
t("wildcard", ev("$.videos[*].url", resp).length === 2);
t("bracket name", ev("$['data'][1]['fileUrl']", resp)[0] === "b.mp4");
t("existence test", ev("$.data[?@.fileUrl].fileType", resp).length === 2);
t("and/or/not", ev("$.data[?@.fileType == 'png' || (!(@.fileType == 'png') && @.fileUrl == 'b.mp4')].fileUrl", resp).length === 2);
t("out of range → empty", ev("$.videos[5]", resp).length === 0);
t("string index → empty", ev("$.videos[0].url[0]", resp).length === 0);
for (const bad of ["$..url", "$.a[0,1]", "$[0:2:1]", "$.x[?length(@.a)==1]", "$.x[?$.a==1]", "$.a-b", "x.y", "$.a[?@.*]", "$.a[?@.b =~ /x/]", "$.a[01]", "$.a[-0]", " $.a", "$.a[?@.b and @.c]"]) {
  let ok = false; try { D.parsePath(bad); } catch (e) { ok = e.name === "PathError"; }
  t("reject " + bad, ok);
}
t("scalar accept", D.extract({ paths: ["$.code"], accept: "scalar" }, { body: resp }).value === 0);
t("string skips number", D.extract(["$.code", "$.data[0].fileUrl"], { body: resp }).value === "a.png");
t("json_decode", D.extract({ paths: ["$.r"], json_decode: true, then: ["$.u[0]"] }, { body: { r: '{"u":["k.mp4"]}' } }).value === "k.mp4");
t("headers source", D.extract({ paths: ["$['x-remaining-credits']"], source: "headers" }, { body: {}, headers: { "X-Remaining-Credits": "42" } }).value === "42");
const ctx = { base_url: "https://x", api_key: "K", model: "m/1", prompt: "hi", duration: 5, seed: null, inputs: { first: "data:..", refs: ["A", "B"] } };
const req = D.renderRequest(
  { method: "POST", url: "{{ base_url }}/v/{{ model }}", body: { p: "{{ prompt }}", d: "{{ duration }}", s: "{{ seed }}", mix: "seed={{ prompt }}", refs: [{ $each: { in: "inputs.refs", as: "r", item: { i: "{{ r }}", n: "{{ index }}" } }}], $each: { in: "inputs.refs", as: "r", key: "img_{{ index }}", value: "{{ r }}" } } },
  ctx, { duration: { "5": "5s" } }, { headers: { Authorization: "Bearer {{ api_key }}" } });
t("url no escape", req.url === "https://x/v/m/1");
t("enum map", req.body.d === "5s");
t("null drop", !("s" in req.body));
t("mixed text", req.body.mix === "seed=hi");
t("$each array", JSON.stringify(req.body.refs) === '[{"i":"A","n":0},{"i":"B","n":1}]');
t("$each object", req.body.img_0 === "A" && req.body.img_1 === "B");
t("auth header rendered", req.headers.Authorization === "Bearer K");
let missing = null; try { D.renderRequest({ method: "GET", url: "u" }, {}, null, { headers: { Authorization: "Bearer {{ api_key }}" } }); } catch (e) { missing = e.name; }
t("api_key missing → RenderError (mixed text)", missing === "RenderError");
t("auth wins over request header (case-insensitive)", D.renderRequest({ method: "GET", url: "u", headers: { authorization: "Basic x" } }, { api_key: "K" }, null, { headers: { Authorization: "Bearer {{ api_key }}" } }).headers.Authorization === "Bearer K");
t("auth wins over request query", D.renderRequest({ method: "GET", url: "u", query: { key: "y" } }, { api_key: "K" }, null, { query: { key: "{{ api_key }}" } }).url === "u?key=K");
t("status expired → expired (builtin)", D.mapStatus("EXPIRED", {}).canonical === "expired" && D.mapStatus("EXPIRED", {}).source === "builtin");
t("status_map unknown target coerced → failed", D.mapStatus("x", { x: "gone" }).canonical === "failed");
t("status int", D.mapStatus(813, { "813": "queued" }).canonical === "queued");
t("status builtin", D.mapStatus("IN_PROGRESS", {}).source === "builtin");
t("status fallback", D.mapStatus("WAITING_GPU", {}).source === "fallback");
let thrown = null; try { D.renderRequest({ method: "POST", url: "u", body: { d: "{{ duration }}" } }, { duration: 7 }, { duration: { "5": 5 } }); } catch (e) { thrown = e.name; }
t("enum miss throws", thrown === "RenderError");
for (const f of ["generic-submit-poll", "newapi-video", "comfyui-workflow"]) {
  const def = JSON.parse(readFileSync(join(here, "templates", f + ".json"), "utf8"));
  const issues = D.validateDefinition(def);
  t(`validate ${f}: ${issues.length ? issues.map((i) => i.level + " " + i.where + ": " + i.message).join(" | ") : "clean"}`, !issues.some((i) => i.level === "error"));
}
const def = JSON.parse(readFileSync(join(here, "templates", "comfyui-workflow.json"), "utf8"));
const bad = { ...def, submit: { ...def.submit, body: { k: "{{ api_key }}", z: "{{ nope }}", tid: "{{ task_id }}" } }, poll: { ...def.poll, extract: { ...def.poll.extract, video_url: ["$..url"] } } };
const badIssues = D.validateDefinition(bad).filter((i) => i.level === "error");
t("bad def: " + badIssues.map((i) => i.where).join(","), badIssues.length === 4);
const gdef = JSON.parse(readFileSync(join(here, "templates", "generic-submit-poll.json"), "utf8"));
const clash = { ...gdef, poll: { ...gdef.poll, headers: { AUTHORIZATION: "x" } }, submit: { ...gdef.submit, query: { key: "y" } }, auth: { headers: { Authorization: "Bearer {{ api_key }}" }, query: { key: "{{ api_key }}" } } };
const clashIssues = D.validateDefinition(clash).filter((i) => i.level === "error");
t("auth clash: " + clashIssues.map((i) => i.where).join(","), clashIssues.length === 2);
t("status_code source rejected", D.validateDefinition({ ...gdef, poll: { ...gdef.poll, extract: { ...gdef.poll.extract, status: { paths: ["$.x"], source: "status_code" } } } }).some((i) => i.where === "poll.extract.status.source"));
// 轮询 HTTP 码：retry 保持上一状态，其余非成功码 ⇒ 失败
const gparams = { base_url: "https://h", api_key: "K", model: "m", prompt: "p", duration: 5, aspect_ratio: "16:9", generate_audio: false, seed: null };
let g = D.initialState(gdef, gparams, {});
g = D.reduce(g, { type: "SUBMIT", response: { status_code: 200, body: { task_id: "T" } } });
g = D.reduce(g, { type: "POLL", response: { status_code: 503, body: { error: "busy" } } });
t("poll 503 → keep polling", g.phase === "polling" && g.poll.mapped.source === "retry_status_codes");
g = D.reduce(g, { type: "POLL", response: { status_code: 422, body: { error: "NSFW" } } });
t("poll 422 → failed with error", g.phase === "failed" && g.error === "HTTP 422：NSFW");
let x = D.initialState(gdef, gparams, {});
x = D.reduce(x, { type: "SUBMIT", response: { status_code: 200, body: { task_id: "T" } } });
x = D.reduce(x, { type: "POLL", response: { status_code: 200, body: { status: "expired" } } });
t("poll body expired → expired terminal", x.phase === "expired");
let y = D.initialState(gdef, gparams, {});
y = D.reduce(y, { type: "SUBMIT", response: { status_code: 200, body: { task_id: "T" } } });
y = D.reduce(y, { type: "POLL", response: { status_code: 404, body: { error: "task not found" } } });
t("poll 404 → expired via expired_status_codes", y.phase === "expired" && y.poll.mapped.source === "expired_status_codes");
// reducer 走一遍 comfy
let s = D.initialState(def, { base_url: "https://h", api_key: "K", model: "wf1", prompt: "girl", duration: 5, aspect_ratio: "9:16", generate_audio: true, seed: 7 }, {});
s = D.reduce(s, { type: "SUBMIT", response: { status_code: 200, body: { code: 0, msg: "success", data: { taskId: "T1" } } } });
t("reducer submit", s.phase === "submitted" && s.taskId === "T1" && s.submit.request.body.nodeInfoList[1].fieldValue === 7);
s = D.reduce(s, { type: "POLL", response: { status_code: 200, body: { code: 813, msg: "q" } } });
t("reducer queued", s.phase === "polling" && s.status.canonical === "queued" && s.poll.request.body.taskId === "T1");
s = D.reduce(s, { type: "POLL", response: { status_code: 200, body: { code: 0, data: [{ fileUrl: "p.png", fileType: "png" }, { fileUrl: "v.mp4", fileType: "mp4", consumeCoins: "18" }] } } });
t("reducer done", s.phase === "done" && s.videoUrl === "v.mp4" && s.usage.coins === "18");
console.log(fails ? `\n${fails} FAILED` : "\nall ok");
process.exit(fails ? 1 : 0);
