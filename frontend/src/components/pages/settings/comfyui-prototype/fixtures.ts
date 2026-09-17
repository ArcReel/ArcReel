// PROTOTYPE — 一次性代码，不进 main。
// ComfyUI 接入原型的内存夹具与假状态机：一台服务、一份 workflow、绑定表、模型行、端点测试。
// 不打后端。绑定推断是一个简化版打分器（按 #2522 决议的信号顺序缩水），
// 目的是让粘贴真实 API 格式 workflow 时也能看到像样的候选，而不是复现后端算法。
import { useCallback, useMemo, useRef, useState } from "react";

// ---------- 槽位 ----------

export type SlotId =
  | "prompt"
  | "negative_prompt"
  | "start_image"
  | "end_image"
  | "reference_images"
  | "width"
  | "height"
  | "frames"
  | "fps"
  | "seed"
  | "output";

export interface SlotMeta {
  id: SlotId;
  label: string;
  required: boolean;
  direction: "write" | "read" | "output";
  group: "文本" | "图像" | "尺寸与时长" | "其他" | "产物";
  hint: string;
}

export const SLOTS: SlotMeta[] = [
  { id: "prompt", label: "正向提示词", required: true, direction: "write", group: "文本", hint: "请求正文（去掉 Avoid 段）" },
  { id: "negative_prompt", label: "负向提示词", required: false, direction: "write", group: "文本", hint: "正文末尾的 Avoid 段；未绑定则丢弃" },
  { id: "start_image", label: "首帧", required: false, direction: "write", group: "图像", hint: "视频请求 start_image" },
  { id: "end_image", label: "尾帧", required: false, direction: "write", group: "图像", hint: "视频请求 end_image" },
  { id: "reference_images", label: "参考图", required: false, direction: "write", group: "图像", hint: "有序；张数不足按 consumer 形态改图" },
  { id: "width", label: "宽", required: false, direction: "write", group: "尺寸与时长", hint: "由比例 + 分辨率派生，按步长对齐" },
  { id: "height", label: "高", required: false, direction: "write", group: "尺寸与时长", hint: "由比例 + 分辨率派生，按步长对齐" },
  { id: "frames", label: "帧数", required: false, direction: "write", group: "尺寸与时长", hint: "时长 × fps，向下对齐到 step·n + 1" },
  { id: "fps", label: "帧率（只读）", required: false, direction: "read", group: "尺寸与时长", hint: "从 workflow 字面值读出，不写入" },
  { id: "seed", label: "种子", required: false, direction: "write", group: "其他", hint: "默认每次随机，避开同输入缓存" },
  { id: "output", label: "产物节点", required: true, direction: "output", group: "产物", hint: "唯一；只读该节点在 history 里的输出" },
];

export const SLOT_BY_ID = Object.fromEntries(SLOTS.map((s) => [s.id, s])) as Record<SlotId, SlotMeta>;

// ---------- workflow ----------

export interface WfNode {
  id: string;
  class_type: string;
  title: string;
  inputs: Record<string, unknown>;
}

export interface Candidate {
  node: string;
  /** output 槽位没有 input */
  input?: string;
  class_type: string;
  title: string;
  score: number;
  signals: string[];
}

export type SlotStatus = "auto" | "manual" | "ambiguous" | "unsupported" | "missing";

export interface SlotBinding {
  status: SlotStatus;
  chosen: Candidate | null;
  candidates: Candidate[];
  /** 仅 width / height / frames */
  step?: number;
  /** 仅 seed */
  policy?: "random" | "keep";
}

export type Bindings = Record<SlotId, SlotBinding>;

export function isLink(v: unknown): v is [string, number] {
  return Array.isArray(v) && v.length === 2 && typeof v[0] === "string" && typeof v[1] === "number";
}

export function parseApiWorkflow(raw: Record<string, unknown>): WfNode[] {
  return Object.entries(raw).map(([id, node]) => {
    const n = node as { class_type?: string; inputs?: Record<string, unknown>; _meta?: { title?: string } };
    return {
      id,
      class_type: n.class_type ?? "?",
      title: n._meta?.title ?? "",
      inputs: n.inputs ?? {},
    };
  });
}

/** 节点里可参数化的字段（排除连线）。 */
export function writableInputs(node: WfNode): [string, unknown][] {
  return Object.entries(node.inputs).filter(([, v]) => !isLink(v));
}

// ---------- 推断（简化版打分器） ----------

const SIZE_CLASS_SCORE: [RegExp, number, number][] = [
  // [class_type 匹配, 分, 步长]
  [/ImageToVideo|LatentVideo|TextToVideo|ImgToVideo|EmptySD3Latent|EmptyMochi|EmptyCosmos/i, 30, 16],
  [/^EmptyLatentImage$/, 25, 8],
  [/Scale|Resize|Crop/i, 15, 8],
];

function traceToText(nodes: Map<string, WfNode>, from: unknown, hops = 0): WfNode | null {
  if (!isLink(from) || hops > 4) return null;
  const n = nodes.get(from[0]);
  if (!n) return null;
  if (typeof n.inputs.text === "string") return n;
  const next = n.inputs.positive ?? n.inputs.negative ?? n.inputs.conditioning;
  return traceToText(nodes, next, hops + 1);
}

function traceToLoader(nodes: Map<string, WfNode>, from: unknown, hops = 0): WfNode | null {
  if (!isLink(from) || hops > 4) return null;
  const n = nodes.get(from[0]);
  if (!n) return null;
  if (/LoadImage/i.test(n.class_type)) return n;
  return traceToLoader(nodes, n.inputs.image ?? n.inputs.images ?? n.inputs.pixels, hops + 1);
}

function depthOf(nodes: Map<string, WfNode>, id: string, memo = new Map<string, number>()): number {
  const cached = memo.get(id);
  if (cached !== undefined) return cached;
  memo.set(id, 0);
  const n = nodes.get(id);
  if (!n) return 0;
  let best = 0;
  for (const v of Object.values(n.inputs)) {
    if (isLink(v)) best = Math.max(best, depthOf(nodes, v[0], memo) + 1);
  }
  memo.set(id, best);
  return best;
}

function finish(cands: Candidate[], extra?: Partial<SlotBinding>): SlotBinding {
  const sorted = [...cands].sort((a, b) => b.score - a.score);
  if (sorted.length === 0) return { status: "missing", chosen: null, candidates: [], ...extra };
  if (sorted.length > 1 && sorted[0].score === sorted[1].score) {
    return { status: "ambiguous", chosen: null, candidates: sorted, ...extra };
  }
  return { status: "auto", chosen: sorted[0], candidates: sorted, ...extra };
}

function cand(n: WfNode, input: string | undefined, score: number, signals: string[]): Candidate {
  return { node: n.id, input, class_type: n.class_type, title: n.title, score, signals };
}

export function inferBindings(list: WfNode[]): Bindings {
  const nodes = new Map(list.map((n) => [n.id, n]));
  const byId = (id: string) => nodes.get(id);
  const titleSlot = (n: WfNode): SlotId | null => {
    const m = /^arcreel:([a-z_]+)/i.exec(n.title.trim());
    return m && m[1].toLowerCase() in SLOT_BY_ID ? (m[1].toLowerCase() as SlotId) : null;
  };

  const out = {} as Bindings;
  const pinned = new Map<SlotId, Candidate>();
  for (const n of list) {
    const s = titleSlot(n);
    if (!s) continue;
    const inputName =
      s === "output" ? undefined : writableInputs(n).find(([k]) => k === s)?.[0] ?? writableInputs(n)[0]?.[0];
    pinned.set(s, cand(n, inputName, 100, ["标题前缀 ARCREEL:" + s]));
  }

  // 正负提示词：采样器端口反溯 + 白名单 + 裸字段名
  const promptC: Candidate[] = [];
  const negC: Candidate[] = [];
  for (const n of list) {
    for (const [port, bucket, label] of [
      ["positive", promptC, "positive 端口反溯"],
      ["negative", negC, "negative 端口反溯"],
    ] as const) {
      const hit = traceToText(nodes, n.inputs[port]);
      if (hit && !bucket.some((c) => c.node === hit.id)) {
        const sig = [`${n.class_type}.${port} ${label}`];
        let score = 40;
        if (/CLIPTextEncode/i.test(hit.class_type)) {
          score += 20;
          sig.push("CLIPTextEncode 白名单");
        }
        if (new RegExp(port, "i").test(hit.title)) {
          score += 5;
          sig.push("标题含 " + port);
        }
        bucket.push(cand(hit, "text", score, sig));
      }
    }
  }
  for (const n of list) {
    for (const [k, v] of writableInputs(n)) {
      if (typeof v !== "string") continue;
      if (["text", "prompt", "positive_prompt"].includes(k) && !promptC.some((c) => c.node === n.id) && !negC.some((c) => c.node === n.id)) {
        promptC.push(cand(n, k, 10, [`字段名 ${k}`]));
      }
      if (k === "negative_prompt" && !negC.some((c) => c.node === n.id)) {
        negC.push(cand(n, k, 10, [`字段名 ${k}`]));
      }
    }
  }
  out.prompt = finish(promptC);
  out.negative_prompt = finish(negC);

  // 图输入
  const startC: Candidate[] = [];
  const endC: Candidate[] = [];
  const usedLoaders = new Set<string>();
  for (const n of list) {
    for (const [k, v] of Object.entries(n.inputs)) {
      if (!isLink(v)) continue;
      const loader = traceToLoader(nodes, v);
      if (!loader) continue;
      if (/^(start_image|first_frame|image)$/.test(k) && /Video/i.test(n.class_type)) {
        startC.push(cand(loader, "image", 30, [`${n.class_type}.${k} 连线回溯`, "LoadImage 白名单"]));
        usedLoaders.add(loader.id);
      }
      if (/^(end_image|last_frame)$/.test(k)) {
        endC.push(cand(loader, "image", 30, [`${n.class_type}.${k} 连线回溯`, "LoadImage 白名单"]));
        usedLoaders.add(loader.id);
      }
    }
  }
  out.start_image = finish(startC);
  out.end_image = finish(endC);
  const refC: Candidate[] = [];
  for (const n of list) {
    if (!/LoadImage/i.test(n.class_type) || usedLoaders.has(n.id)) continue;
    const consumer = list.find((m) => Object.entries(m.inputs).some(([k, v]) => isLink(v) && v[0] === n.id && /^(image\d*|images|reference)/.test(k)));
    if (consumer) refC.push(cand(n, "image", 20, [`喂给 ${consumer.class_type}`]));
  }
  out.reference_images = refC.length ? { status: "auto", chosen: refC[0], candidates: refC } : finish([]);

  // 尺寸
  for (const slot of ["width", "height"] as const) {
    const cs: Candidate[] = [];
    let step = 16;
    for (const n of list) {
      const v = n.inputs[slot];
      if (typeof v !== "number") continue;
      let score = 10;
      const sig = [`字段名 ${slot}`];
      for (const [re, sc, st] of SIZE_CLASS_SCORE) {
        if (re.test(n.class_type)) {
          score = sc;
          step = st;
          sig.push(`${n.class_type} 白名单`);
          break;
        }
      }
      cs.push(cand(n, slot, score, sig));
    }
    out[slot] = finish(cs, { step });
  }

  // 帧数 / fps
  const frameC: Candidate[] = [];
  let frameStep = 4;
  for (const n of list) {
    for (const k of ["length", "num_frames", "video_frames", "frames"]) {
      if (typeof n.inputs[k] === "number") {
        frameC.push(cand(n, k, 30, [`字段名 ${k}`, `${n.class_type} 白名单`]));
        if (/LTX/i.test(n.class_type)) frameStep = 8;
      }
    }
  }
  out.frames = finish(frameC, { step: frameStep });
  const fpsC: Candidate[] = [];
  for (const n of list) {
    if (/CreateVideo/i.test(n.class_type) && typeof n.inputs.fps === "number") fpsC.push(cand(n, "fps", 30, ["CreateVideo.fps"]));
    if (/VHS_VideoCombine/i.test(n.class_type) && typeof n.inputs.frame_rate === "number") fpsC.push(cand(n, "frame_rate", 28, ["VHS_VideoCombine.frame_rate"]));
  }
  out.fps = finish(fpsC);

  // 种子
  const seedC: Candidate[] = [];
  for (const n of list) {
    if (!/Sampler/i.test(n.class_type)) continue;
    if (n.inputs.add_noise === "disable") continue;
    for (const k of ["seed", "noise_seed"]) {
      if (typeof n.inputs[k] === "number") {
        const sig = [`字段名 ${k}`, `${n.class_type} 白名单`];
        if (n.inputs.add_noise === "enable") sig.push("add_noise = enable");
        seedC.push(cand(n, k, 30, sig));
      }
    }
  }
  out.seed = finish(seedC, { policy: "random" });

  // 产物
  const outC: Candidate[] = [];
  const memo = new Map<string, number>();
  for (const n of list) {
    let base = 0;
    if (/^SaveVideo$/.test(n.class_type)) base = 40;
    else if (/VHS_VideoCombine/.test(n.class_type)) base = n.inputs.save_output === false ? 0 : 38;
    else if (/^SaveImage/.test(n.class_type)) base = 36;
    else if (/SaveAnimatedWEBP|SaveWEBM/.test(n.class_type)) base = 34;
    if (!base) continue;
    const d = depthOf(nodes, n.id, memo);
    outC.push(cand(n, undefined, base + d, [`${n.class_type} 产物节点`, `依赖深度 ${d}`]));
  }
  out.output = finish(outC);

  for (const [slot, c] of pinned) {
    out[slot] = { ...out[slot], status: "auto", chosen: c, candidates: [c, ...out[slot].candidates.filter((x) => x.node !== c.node)] };
  }
  void byId;
  return out;
}

// ---------- 示例文件 ----------

const SAMPLE_API: Record<string, unknown> = {
  "37": { class_type: "UNETLoader", inputs: { unet_name: "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", weight_dtype: "default" }, _meta: { title: "Load Diffusion Model" } },
  "56": { class_type: "UNETLoader", inputs: { unet_name: "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", weight_dtype: "default" }, _meta: { title: "Load Diffusion Model" } },
  "38": { class_type: "CLIPLoader", inputs: { clip_name: "umt5_xxl_fp8_e4m3fn_scaled.safetensors", type: "wan", device: "default" }, _meta: { title: "Load CLIP" } },
  "39": { class_type: "VAELoader", inputs: { vae_name: "wan_2.1_vae.safetensors" }, _meta: { title: "Load VAE" } },
  "6": { class_type: "CLIPTextEncode", inputs: { text: "a fox running through snowy forest, cinematic, soft light", clip: ["38", 0] }, _meta: { title: "CLIP Text Encode (Positive Prompt)" } },
  "7": { class_type: "CLIPTextEncode", inputs: { text: "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止", clip: ["38", 0] }, _meta: { title: "CLIP Text Encode (Negative Prompt)" } },
  "52": { class_type: "LoadImage", inputs: { image: "example.png", upload: "image" }, _meta: { title: "Load Image" } },
  "50": { class_type: "WanImageToVideo", inputs: { width: 640, height: 640, length: 81, batch_size: 1, positive: ["6", 0], negative: ["7", 0], vae: ["39", 0], start_image: ["52", 0] }, _meta: { title: "WanImageToVideo" } },
  "57": { class_type: "KSamplerAdvanced", inputs: { add_noise: "enable", noise_seed: 1029384756, steps: 20, cfg: 3.5, sampler_name: "euler", scheduler: "simple", start_at_step: 0, end_at_step: 10, return_with_leftover_noise: "enable", model: ["37", 0], positive: ["50", 0], negative: ["50", 1], latent_image: ["50", 2] }, _meta: { title: "KSampler (Advanced) - high noise" } },
  "58": { class_type: "KSamplerAdvanced", inputs: { add_noise: "disable", noise_seed: 0, steps: 20, cfg: 3.5, sampler_name: "euler", scheduler: "simple", start_at_step: 10, end_at_step: 10000, return_with_leftover_noise: "disable", model: ["56", 0], positive: ["50", 0], negative: ["50", 1], latent_image: ["57", 0] }, _meta: { title: "KSampler (Advanced) - low noise" } },
  "8": { class_type: "VAEDecode", inputs: { samples: ["58", 0], vae: ["39", 0] }, _meta: { title: "VAE Decode" } },
  "60": { class_type: "CreateVideo", inputs: { fps: 16, images: ["8", 0] }, _meta: { title: "Create Video" } },
  "61": { class_type: "SaveVideo", inputs: { filename_prefix: "video/ComfyUI", format: "auto", codec: "auto", video: ["60", 0] }, _meta: { title: "Save Video" } },
  "62": { class_type: "SaveVideo", inputs: { filename_prefix: "video/archive/ComfyUI", format: "mp4", codec: "h264", video: ["60", 0] }, _meta: { title: "Save Video (archive copy)" } },
};

const SAMPLE_UI = {
  last_node_id: 62,
  last_link_id: 120,
  nodes: [
    { id: 6, type: "CLIPTextEncode", pos: [415, 186], widgets_values: ["a fox running through snowy forest"] },
    { id: 61, type: "SaveVideo", pos: [1500, 186], widgets_values: ["video/ComfyUI", "auto", "auto"] },
  ],
  links: [[1, 38, 0, 6, 0, "CLIP"]],
  version: 0.4,
};

const SAMPLE_DEFINITION = {
  kind: "comfyui",
  schema_version: 1,
  meta: { name: "Wan 2.2 I2V 14B", author: "kaze", version: "1.0.0" },
  media_type: "video",
  auth: { headers: { Authorization: "Bearer {{api_key}}" } },
  workflow: SAMPLE_API,
  bindings: {
    prompt: [{ node: "6", input: "text", class_type: "CLIPTextEncode", title: "CLIP Text Encode (Positive Prompt)" }],
    negative_prompt: [{ node: "7", input: "text", class_type: "CLIPTextEncode", title: "CLIP Text Encode (Negative Prompt)" }],
    start_image: [{ node: "52", input: "image", class_type: "LoadImage", title: "Load Image" }],
    end_image: [],
    reference_images: [],
    width: [{ node: "50", input: "width", class_type: "WanImageToVideo", title: "WanImageToVideo", step: 16 }],
    height: [{ node: "50", input: "height", class_type: "WanImageToVideo", title: "WanImageToVideo", step: 16 }],
    frames: [{ node: "50", input: "length", class_type: "WanImageToVideo", title: "WanImageToVideo", step: 4 }],
    fps: [{ node: "60", input: "fps", class_type: "CreateVideo", title: "Create Video", direction: "read" }],
    seed: [{ node: "57", input: "noise_seed", class_type: "KSamplerAdvanced", title: "KSampler (Advanced) - high noise", policy: "random" }],
    output: [{ node: "61", class_type: "SaveVideo", title: "Save Video" }],
  },
};

export const SAMPLES = {
  api: { label: "API 格式 workflow（Wan 2.2 I2V）", file: "wan22_i2v_api.json", text: JSON.stringify(SAMPLE_API, null, 2) },
  ui: { label: "UI 格式 workflow（会被拒绝）", file: "wan22_i2v_ui.json", text: JSON.stringify(SAMPLE_UI, null, 2) },
  definition: { label: "带 kind 的 workflow 端点定义", file: "wan22_i2v.arcreel.json", text: JSON.stringify(SAMPLE_DEFINITION, null, 2) },
} as const;

// ---------- 形状分流 ----------

export type ImportShape =
  | { kind: "invalid"; message: string }
  | { kind: "ui"; message: string }
  | { kind: "declarative"; message: string }
  | { kind: "definition"; name: string; nodes: WfNode[]; bindings: Bindings }
  | { kind: "raw"; nodes: WfNode[]; bindings: Bindings };

export function detectShape(text: string): ImportShape {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { kind: "invalid", message: "不是合法 JSON。" };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { kind: "invalid", message: "顶层应是一个对象。" };
  }
  const obj = parsed as Record<string, unknown>;
  if (Array.isArray(obj.nodes) && Array.isArray(obj.links)) {
    return { kind: "ui", message: "这是 ComfyUI 的 UI 格式（含 nodes / links）。请在 ComfyUI 里用「Export (API)」导出后再导入。" };
  }
  if (obj.kind === "declarative") {
    return { kind: "declarative", message: "这是声明式协议端点的定义，请在「调用端点」小节导入。" };
  }
  if (obj.kind === "comfyui") {
    const nodes = parseApiWorkflow((obj.workflow as Record<string, unknown>) ?? {});
    const inferred = inferBindings(nodes);
    const stored = (obj.bindings ?? {}) as Record<string, { node: string; input?: string; class_type: string; title: string; step?: number; policy?: "random" | "keep" }[]>;
    const bindings = { ...inferred };
    for (const slot of SLOTS) {
      const list = stored[slot.id];
      if (!list) continue;
      if (list.length === 0) {
        bindings[slot.id] = { ...inferred[slot.id], status: "unsupported", chosen: null };
        continue;
      }
      const t = list[0];
      const c: Candidate = { node: t.node, input: t.input, class_type: t.class_type, title: t.title, score: 100, signals: ["来自定义文件"] };
      bindings[slot.id] = { ...inferred[slot.id], status: "manual", chosen: c, candidates: [c, ...inferred[slot.id].candidates.filter((x) => x.node !== t.node || x.input !== t.input)], step: t.step ?? inferred[slot.id].step, policy: t.policy ?? inferred[slot.id].policy };
    }
    const meta = obj.meta as { name?: string } | undefined;
    return { kind: "definition", name: meta?.name ?? "未命名 workflow 端点", nodes, bindings };
  }
  const nodes = parseApiWorkflow(obj);
  if (nodes.length === 0 || nodes.some((n) => n.class_type === "?")) {
    return { kind: "invalid", message: "看不出这是哪种文件：既不是 API 格式 workflow（每个键应是带 class_type 的节点），也不是端点定义。" };
  }
  return { kind: "raw", nodes, bindings: inferBindings(nodes) };
}

// ---------- 假状态机 ----------

export interface ProviderState {
  name: string;
  baseUrl: string;
  apiKey: string;
  check: { status: "idle" | "checking" | "ok" | "fail"; version?: string; message?: string };
}

export interface WorkflowEndpoint {
  key: string;
  name: string;
  fileName: string;
  mediaType: "video" | "image";
  nodes: WfNode[];
  bindings: Bindings;
  saved: boolean;
  origin: "raw" | "definition";
}

export interface ModelRow {
  id: string;
  name: string;
  endpointKey: string;
  isDefault: boolean;
  price: string;
}

export type TrialPhase = "idle" | "uploading" | "queued" | "running" | "done" | "failed";
export interface TrialState {
  phase: TrialPhase;
  startedAt?: number;
  elapsedMs?: number;
  promptId?: string;
  artifact?: { filename: string; size: string; duration: string };
  error?: { code: string; message: string; detail?: string };
}

export interface ProjectContext {
  aspect: string;
  resolution: string;
  durationSeconds: number;
  prompt: string;
  avoid: string;
}

export const PROJECT_CONTEXT: ProjectContext = {
  aspect: "16:9",
  resolution: "480p",
  durationSeconds: 5,
  prompt: "A red fox darts between snow-laden pines at dawn, low tracking shot, soft volumetric light",
  avoid: "text, watermark, blurry, static frame",
};

export function savePendingReasons(b: Bindings): string[] {
  const reasons: string[] = [];
  for (const slot of SLOTS) {
    const s = b[slot.id];
    if (slot.required && !s.chosen) reasons.push(`「${slot.label}」是必填槽位，尚未绑定`);
    else if (s.status === "ambiguous") reasons.push(`「${slot.label}」有并列候选，需要你选一个或标为不支持`);
  }
  return reasons;
}

/** 由项目上下文 + 绑定表推出本次会写进去的值（用于预览请求与讲解）。 */
export function deriveValues(ep: WorkflowEndpoint, ctx: ProjectContext) {
  const b = ep.bindings;
  const literal = (slot: SlotId): number | undefined => {
    const c = b[slot].chosen;
    if (!c || !c.input) return undefined;
    const v = ep.nodes.find((n) => n.id === c.node)?.inputs[c.input];
    return typeof v === "number" ? v : undefined;
  };
  const stepW = b.width.step ?? 16;
  const stepH = b.height.step ?? 16;
  const roundTo = lcm(stepW, stepH);
  const short = ctx.resolution === "720p" ? 720 : 480;
  const [aw, ah] = ctx.aspect.split(":").map(Number);
  const long = Math.round((short * aw) / ah);
  const width = b.width.chosen ? Math.floor(long / roundTo) * roundTo : literal("width");
  const height = b.height.chosen ? Math.floor(short / roundTo) * roundTo : literal("height");
  const fps = literal("fps") ?? 16;
  const step = b.frames.step ?? 4;
  const rawFrames = ctx.durationSeconds * fps;
  const frames = b.frames.chosen ? Math.floor((rawFrames - 1) / step) * step + 1 : literal("frames");
  const seed = b.seed.policy === "keep" ? literal("seed") : Math.floor(Math.random() * 2 ** 32);
  return { width, height, fps, frames, seed, roundTo, step, short, long, rawFrames };
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}
function lcm(a: number, b: number): number {
  return (a * b) / gcd(a, b);
}

export function renderPromptBody(ep: WorkflowEndpoint, ctx: ProjectContext, values: ReturnType<typeof deriveValues>) {
  const wf: Record<string, { class_type: string; inputs: Record<string, unknown>; _meta: { title: string } }> = {};
  for (const n of ep.nodes) wf[n.id] = { class_type: n.class_type, inputs: { ...n.inputs }, _meta: { title: n.title } };
  const set = (slot: SlotId, v: unknown) => {
    const c = ep.bindings[slot].chosen;
    if (!c || !c.input || v === undefined) return;
    if (wf[c.node]) wf[c.node].inputs[c.input] = v;
  };
  set("prompt", ctx.prompt);
  set("negative_prompt", ctx.avoid);
  set("start_image", "arcreel/task_8f3a_start.png");
  set("width", values.width);
  set("height", values.height);
  set("frames", values.frames);
  set("seed", values.seed);
  return { client_id: "arcreel-task_8f3a", prompt: wf };
}

export interface ComfyProto {
  provider: ProviderState;
  setProvider: (patch: Partial<Pick<ProviderState, "name" | "baseUrl" | "apiKey">>) => void;
  checkConnectivity: () => void;
  endpoints: WorkflowEndpoint[];
  draft: WorkflowEndpoint | null;
  importText: (text: string, fileName?: string) => ImportShape;
  lastShape: ImportShape | null;
  clearShape: () => void;
  choose: (slot: SlotId, c: Candidate) => void;
  markUnsupported: (slot: SlotId) => void;
  reinfer: (slot: SlotId) => void;
  setStep: (slot: SlotId, step: number) => void;
  setSeedPolicy: (policy: "random" | "keep") => void;
  setDraftMeta: (patch: Partial<Pick<WorkflowEndpoint, "name" | "mediaType">>) => void;
  saveDraft: () => WorkflowEndpoint | null;
  openEndpoint: (key: string) => void;
  discardDraft: () => void;
  models: ModelRow[];
  addModel: (endpointKey: string) => void;
  updateModel: (id: string, patch: Partial<ModelRow>) => void;
  removeModel: (id: string) => void;
  trial: TrialState;
  runTrial: (opts?: { simulateMissingModel?: boolean }) => void;
  resetTrial: () => void;
  ctx: ProjectContext;
  /** 每次预览按钮重新算一遍（种子会变）。 */
  previewSeq: number;
  bumpPreview: () => void;
}

export function useComfyProto(): ComfyProto {
  const [provider, setProviderState] = useState<ProviderState>({
    name: "工作室 4090 机",
    baseUrl: "http://192.168.1.42:8188",
    apiKey: "",
    check: { status: "idle" },
  });
  const [endpoints, setEndpoints] = useState<WorkflowEndpoint[]>([]);
  const [draft, setDraft] = useState<WorkflowEndpoint | null>(null);
  const [lastShape, setLastShape] = useState<ImportShape | null>(null);
  const [models, setModels] = useState<ModelRow[]>([]);
  const [trial, setTrial] = useState<TrialState>({ phase: "idle" });
  const [previewSeq, setPreviewSeq] = useState(0);
  const timers = useRef<number[]>([]);

  const setProvider = useCallback((patch: Partial<Pick<ProviderState, "name" | "baseUrl" | "apiKey">>) => {
    setProviderState((p) => ({ ...p, ...patch, check: { status: "idle" } }));
  }, []);

  const checkConnectivity = useCallback(() => {
    setProviderState((p) => ({ ...p, check: { status: "checking" } }));
    window.setTimeout(() => {
      setProviderState((p) =>
        /^https?:\/\//.test(p.baseUrl)
          ? { ...p, check: { status: "ok", version: "0.3.61", message: "GET /system_stats 200 · 12.4 GB VRAM 可用" } }
          : { ...p, check: { status: "fail", message: "地址需以 http:// 或 https:// 开头" } },
      );
    }, 700);
  }, []);

  const importText = useCallback((text: string, fileName?: string): ImportShape => {
    const shape = detectShape(text);
    setLastShape(shape);
    if (shape.kind === "raw" || shape.kind === "definition") {
      const name = shape.kind === "definition" ? shape.name : (fileName ?? "workflow.json").replace(/\.json$/i, "");
      setDraft({
        key: `ce-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "workflow"}`,
        name,
        fileName: fileName ?? "粘贴的文本",
        mediaType: shape.nodes.some((n) => /Video/i.test(n.class_type)) ? "video" : "image",
        nodes: shape.nodes,
        bindings: shape.bindings,
        saved: false,
        origin: shape.kind,
      });
    }
    return shape;
  }, []);

  const patchDraftBinding = useCallback((slot: SlotId, fn: (b: SlotBinding) => SlotBinding) => {
    setDraft((d) => (d ? { ...d, saved: false, bindings: { ...d.bindings, [slot]: fn(d.bindings[slot]) } } : d));
  }, []);

  const choose = useCallback((slot: SlotId, c: Candidate) => {
    patchDraftBinding(slot, (b) => ({
      ...b,
      status: c.score >= 100 || b.candidates.some((x) => x.node === c.node && x.input === c.input) ? (b.candidates[0]?.node === c.node && b.status === "auto" ? "auto" : "manual") : "manual",
      chosen: c,
      candidates: b.candidates.some((x) => x.node === c.node && x.input === c.input) ? b.candidates : [c, ...b.candidates],
    }));
  }, [patchDraftBinding]);

  const markUnsupported = useCallback((slot: SlotId) => {
    patchDraftBinding(slot, (b) => ({ ...b, status: "unsupported", chosen: null }));
  }, [patchDraftBinding]);

  const reinfer = useCallback((slot: SlotId) => {
    setDraft((d) => (d ? { ...d, saved: false, bindings: { ...d.bindings, [slot]: inferBindings(d.nodes)[slot] } } : d));
  }, []);

  const setStep = useCallback((slot: SlotId, step: number) => patchDraftBinding(slot, (b) => ({ ...b, step })), [patchDraftBinding]);
  const setSeedPolicy = useCallback((policy: "random" | "keep") => patchDraftBinding("seed", (b) => ({ ...b, policy })), [patchDraftBinding]);
  const setDraftMeta = useCallback((patch: Partial<Pick<WorkflowEndpoint, "name" | "mediaType">>) => {
    setDraft((d) => (d ? { ...d, ...patch, saved: false } : d));
  }, []);

  const saveDraft = useCallback((): WorkflowEndpoint | null => {
    if (!draft || savePendingReasons(draft.bindings).length > 0) return null;
    const saved = { ...draft, saved: true };
    setEndpoints((list) => [...list.filter((e) => e.key !== saved.key), saved]);
    setDraft(saved);
    return saved;
  }, [draft]);

  const openEndpoint = useCallback((key: string) => {
    setEndpoints((list) => {
      const ep = list.find((e) => e.key === key);
      if (ep) setDraft(ep);
      return list;
    });
  }, []);

  const discardDraft = useCallback(() => {
    setDraft(null);
    setLastShape(null);
  }, []);

  const addModel = useCallback((endpointKey: string) => {
    setModels((m) => {
      const ep = endpoints.find((e) => e.key === endpointKey);
      return [...m, { id: `m${Date.now()}`, name: ep?.name ?? endpointKey, endpointKey, isDefault: m.length === 0, price: "0" }];
    });
  }, [endpoints]);
  const updateModel = useCallback((id: string, patch: Partial<ModelRow>) => {
    setModels((m) => m.map((row) => (row.id === id ? { ...row, ...patch } : patch.isDefault ? { ...row, isDefault: false } : row)));
  }, []);
  const removeModel = useCallback((id: string) => setModels((m) => m.filter((r) => r.id !== id)), []);

  const clearTimers = () => {
    for (const t of timers.current) window.clearTimeout(t);
    timers.current = [];
  };
  const runTrial = useCallback((opts?: { simulateMissingModel?: boolean }) => {
    clearTimers();
    const startedAt = Date.now();
    setTrial({ phase: "uploading", startedAt });
    const later = (ms: number, fn: () => void) => timers.current.push(window.setTimeout(fn, ms));
    if (opts?.simulateMissingModel) {
      later(900, () =>
        setTrial({
          phase: "failed",
          startedAt,
          elapsedMs: 900,
          error: {
            code: "comfyui_node_errors",
            message: "POST /prompt 返回 node_errors：workflow 里有节点无法执行",
            detail: 'UNETLoader (#37) · unet_name: "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors" 不在 models/diffusion_models 里',
          },
        }),
      );
      return;
    }
    later(900, () => setTrial({ phase: "queued", startedAt, promptId: "5f1c0a3e-9b2d-4d1c-8a77-0f2e6b9c1d44" }));
    later(2200, () => setTrial({ phase: "running", startedAt, promptId: "5f1c0a3e-9b2d-4d1c-8a77-0f2e6b9c1d44" }));
    later(5200, () =>
      setTrial({
        phase: "done",
        startedAt,
        elapsedMs: 4300,
        promptId: "5f1c0a3e-9b2d-4d1c-8a77-0f2e6b9c1d44",
        artifact: { filename: "ComfyUI_00012_.mp4", size: "2.4 MB", duration: "5.0 s · 848×480 · 16 fps" },
      }),
    );
  }, []);
  const resetTrial = useCallback(() => {
    clearTimers();
    setTrial({ phase: "idle" });
  }, []);

  const bumpPreview = useCallback(() => setPreviewSeq((n) => n + 1), []);

  return useMemo(
    () => ({
      provider,
      setProvider,
      checkConnectivity,
      endpoints,
      draft,
      importText,
      lastShape,
      clearShape: () => setLastShape(null),
      choose,
      markUnsupported,
      reinfer,
      setStep,
      setSeedPolicy,
      setDraftMeta,
      saveDraft,
      openEndpoint,
      discardDraft,
      models,
      addModel,
      updateModel,
      removeModel,
      trial,
      runTrial,
      resetTrial,
      ctx: PROJECT_CONTEXT,
      previewSeq,
      bumpPreview,
    }),
    [provider, setProvider, checkConnectivity, endpoints, draft, importText, lastShape, choose, markUnsupported, reinfer, setStep, setSeedPolicy, setDraftMeta, saveDraft, openEndpoint, discardDraft, models, addModel, updateModel, removeModel, trial, runTrial, resetTrial, previewSeq, bumpPreview],
  );
}
