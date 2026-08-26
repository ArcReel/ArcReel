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
t("auth.query rendered + encoded", D.renderRequest({ method: "GET", url: "u?x=1" }, { api_key: "K/1" }, null, { query: { key: "{{ api_key }}" } }).url === "u?x=1&key=K%2F1");
t("status expired → failed (builtin)", D.mapStatus("EXPIRED", {}).canonical === "failed" && D.mapStatus("EXPIRED", {}).source === "builtin");
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
const clash = { ...gdef, poll: { ...gdef.poll, headers: { AUTHORIZATION: "x" } }, submit: { ...gdef.submit, headers: { authorization: "y" } }, auth: { headers: { Authorization: "Bearer {{ api_key }}" } } };
const clashIssues = D.validateDefinition(clash).filter((i) => i.level === "error");
t("auth clash: " + clashIssues.map((i) => i.where).join(","), clashIssues.length === 2);
t("extract.source rejected", D.validateDefinition({ ...gdef, poll: { ...gdef.poll, extract: { ...gdef.poll.extract, status: { paths: ["$.x"], source: "headers" } } } }).some((i) => i.where === "poll.extract.status.source"));
const removed = { ...gdef, submit: { ...gdef.submit, query: { a: "b" }, success_status_codes: [200] }, poll: { ...gdef.poll, interval_seconds: 5 }, capabilities: { first_frame: true }, meta: { ...gdef.meta, media_type: "video" } };
const removedIssues = D.validateDefinition(removed).filter((i) => i.level === "error").map((i) => i.where).sort();
t("removed fields flagged: " + removedIssues.join(","), removedIssues.join(",") === "capabilities.first_frame,meta.media_type,poll.interval_seconds,submit.query,submit.success_status_codes");
const caps = D.deriveCapabilities(gdef);
t("derive caps from inputs", caps.first_frame === true && caps.last_frame === false && caps.audio_track === "controllable" && caps.max_reference_images === 0);
const unused = { ...gdef, inputs: { ...gdef.inputs, tail: { source: "end_image", encoding: "base64" } } };
t("unreferenced input → error, not derived", D.validateDefinition(unused).some((i) => i.level === "error" && i.where === "inputs.tail") && D.deriveCapabilities(unused).last_frame === false);
const dupq = { ...gdef, auth: { query: { key: "{{ api_key }}" } }, poll: { ...gdef.poll, url: gdef.poll.url + "?key=static&x=1" } };
t("url query clashes auth.query → validate error", D.validateDefinition(dupq).some((i) => i.level === "error" && i.where === "poll.url"));
let dupThrown = null; try { D.renderRequest({ method: "GET", url: "u?key=1" }, { api_key: "K" }, null, { query: { key: "{{ api_key }}" } }); } catch (e) { dupThrown = e.name; }
t("url query clashes auth.query → RenderError", dupThrown === "RenderError");
t("url query without clash ok", D.renderRequest({ method: "GET", url: "u?x=1" }, { api_key: "K" }, null, { query: { key: "{{ api_key }}" } }).url === "u?x=1&key=K");
// 轮询 HTTP 码：retry 保持上一状态，其余非成功码 ⇒ 失败
const gparams = { base_url: "https://h", api_key: "K", model: "m", prompt: "p", duration: 5, aspect_ratio: "16:9", generate_audio: false, seed: null };
let g = D.initialState(gdef, gparams, {});
g = D.reduce(g, { type: "SUBMIT", response: { status_code: 200, body: { task_id: "T" } } });
g = D.reduce(g, { type: "POLL", response: { status_code: 503, body: { error: "busy" } } });
t("poll 503 → keep polling (runtime retry)", g.phase === "polling" && g.poll.mapped.source === "runtime_retry");
g = D.reduce(g, { type: "POLL", response: { status_code: 422, body: { error: "NSFW" } } });
t("poll 422 → failed with error", g.phase === "failed" && g.error === "HTTP 422：NSFW");
let x = D.initialState(gdef, gparams, {});
x = D.reduce(x, { type: "SUBMIT", response: { status_code: 200, body: { task_id: "T" } } });
x = D.reduce(x, { type: "POLL", response: { status_code: 200, body: { status: "expired" } } });
t("poll body expired → failed", x.phase === "failed");
let y = D.initialState(gdef, gparams, {});
y = D.reduce(y, { type: "SUBMIT", response: { status_code: 200, body: { task_id: "T" } } });
y = D.reduce(y, { type: "POLL", response: { status_code: 404, body: { error: "task not found" } } });
t("poll 404 → failed (non-2xx, not transient)", y.phase === "failed" && y.error === "HTTP 404：task not found");
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
