// PROTOTYPE — 变体 A「主从表单」修订版 3（wayfinder #2129，按用户反馈迭代）
// 本轮改动：
//  * 文案清理：任务编号→任务 ID，服务商→供应商，删去「两种视图编辑同一份定义」等冗余说明。
//  * 状态对照演示多对一映射（pending→排队中、expired→失败）。
//  * 测试连接：测试模型可输入；密钥来源选「临时输入」时出现密钥输入框。
//  * 离线测试卡不再标注「免费」，仅「测试连接」保留计费提示。
//  * 头部常驻「创建供应商」按钮（新建供应商并预选该端点、预填默认接口地址）。
//  * 访问凭证节支持「该接口无需凭证」（空 auth 节）。
import { useState } from "react";
import {
  CircleAlert,
  Copy,
  Download,
  FileJson2,
  Lock,
  Play,
  Plus,
  Trash2,
  TriangleAlert,
  Upload,
} from "lucide-react";
import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { MOCK_CHECK_RESULT, MOCK_ENDPOINTS, MOCK_PREVIEW_REQUEST, MOCK_TRIAL_TIMELINE, SAMPLE_DEFINITION, type MockEndpoint } from "./endpoint-prototype-data";

const KICKER_CLS = "font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-text-3";
const LABEL_CLS = "mb-1 block text-[12px] font-medium text-text-2";
const HINT_CLS = "mt-1.5 block text-[12px] leading-[1.55] text-text-3";

const FIELD_LABELS: Record<string, string> = {
  status: "任务状态",
  video_url: "视频地址",
  error: "错误信息",
  "usage.duration_seconds": "计费时长",
};

const AUTH_VARS = [{ name: "{{ api_key }}", desc: "供应商设置中保存的 API 密钥" }];

const SUBMIT_VARS = [
  { name: "{{ prompt }}", desc: "提示词" },
  { name: "{{ model }}", desc: "模型" },
  { name: "{{ image }}", desc: "首帧图片" },
  { name: "{{ image_tail }}", desc: "尾帧图片" },
  { name: "{{ ref_images }}", desc: "参考图" },
  { name: "{{ audio }}", desc: "参考音频" },
  { name: "{{ duration_seconds }}", desc: "时长（秒）" },
  { name: "{{ resolution }}", desc: "分辨率档位，如 720p" },
  { name: "{{ aspect_ratio }}", desc: "画面比例，如 16:9" },
  { name: "{{ width }} · {{ height }}", desc: "像素宽高，由比例与档位派生" },
];

const MOCK_INPUT_ROWS = [
  { kind: "首帧图片", format: "base64 data URI", variable: "{{ image }}" },
  { kind: "尾帧图片", format: "base64 data URI", variable: "{{ image_tail }}" },
  { kind: "参考图（最多 4 张）", format: "裸 base64 · 逐张展开", variable: "{{ ref_images }}" },
];

const MOCK_STATUS_ROWS = [
  { from: "queued", to: "排队中" },
  { from: "pending", to: "排队中" },
  { from: "processing", to: "生成中" },
  { from: "succeeded", to: "已完成" },
  { from: "failed", to: "失败" },
  { from: "expired", to: "失败" },
];

const MOCK_DIAGNOSTICS = [
  {
    level: "error" as const,
    section: "查询进度与结果",
    message: "「视频地址」第 2 条路径包含不支持的 .. 语法，需改为逐层路径（如 $.data.video_url）",
  },
  {
    level: "warning" as const,
    section: "状态对照",
    message: "状态值 cancelled 未配置映射，将按「生成中」持续查询；若为终态，请映射为「失败」",
  },
];

function KindBadge({ kind }: { kind: MockEndpoint["kind"] }) {
  const label = kind === "custom" ? "自定义" : kind === "declarative" ? "内置" : "内置 · Python";
  const cls =
    kind === "custom"
      ? "border-accent/35 bg-accent-dim text-accent-2"
      : "border-hairline-soft bg-bg-grad-a/55 text-text-3";
  return (
    <span className={`shrink-0 rounded-[5px] border px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] ${cls}`}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 生命周期分节（结构化表单）
// ---------------------------------------------------------------------------

function FormSection({ step, title, desc, children }: { step: string; title: string; desc: string; children: React.ReactNode }) {
  return (
    <section className="relative pl-7">
      {/* 生命周期竖轨 */}
      <span aria-hidden className="absolute left-[7px] top-6 bottom-0 w-px bg-hairline-soft" />
      <span
        aria-hidden
        className="absolute left-0 top-0.5 grid h-4 w-4 place-items-center rounded-full border border-accent/40 bg-accent-dim font-mono text-[8.5px] font-bold text-accent-2"
      >
        {step}
      </span>
      <h3 className="text-[13.5px] font-semibold text-text">{title}</h3>
      <p className="mb-2.5 mt-0.5 text-[12px] leading-[1.55] text-text-3">{desc}</p>
      <div className="mb-6 rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        {children}
      </div>
    </section>
  );
}

function Field({ label, value, mono, hint }: { label: string; value: string; mono?: boolean; hint?: string }) {
  return (
    <label className="block">
      <span className={LABEL_CLS}>{label}</span>
      <input type="text" readOnly value={value} className={`${INPUT_CLS} ${mono ? "font-mono text-[12px]" : ""}`} />
      {hint && <span className={HINT_CLS}>{hint}</span>}
    </label>
  );
}

function PathsField({ label, paths, hint }: { label: string; paths: string[]; hint?: string }) {
  return (
    <div>
      <span className={LABEL_CLS}>{label}</span>
      <div className="flex flex-wrap items-center gap-1.5">
        {paths.map((p, i) => (
          <span key={p} className="inline-flex items-center gap-1.5 rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 font-mono text-[11.5px] text-good/90">
            <span className="text-text-3">{i + 1}</span>
            {p}
          </span>
        ))}
        <button type="button" className="rounded-[6px] border border-dashed border-hairline px-2 py-1 text-[11.5px] text-text-3 hover:border-hairline-strong hover:text-text">
          + 备选路径
        </button>
      </div>
      {hint && <span className={HINT_CLS}>{hint}</span>}
    </div>
  );
}

/** 可用变量提示条。 */
function VariableChips({ vars, note }: { vars: { name: string; desc: string }[]; note?: string }) {
  return (
    <div className="mt-3 rounded-[8px] border border-hairline-soft bg-bg-grad-a/35 px-3 py-2.5">
      <span className="mb-1.5 block text-[11.5px] text-text-3">可用变量 · 点击插入</span>
      <div className="flex flex-wrap gap-1.5">
        {vars.map((v) => (
          <button
            key={v.name}
            type="button"
            className="inline-flex items-baseline gap-1.5 rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 transition-colors hover:border-accent/40 hover:bg-accent-dim"
          >
            <span className="font-mono text-[11px] text-accent-2">{v.name}</span>
            <span className="text-[11px] text-text-3">{v.desc}</span>
          </button>
        ))}
      </div>
      {note && <span className="mt-1.5 block text-[11.5px] leading-[1.5] text-text-3">{note}</span>}
    </div>
  );
}

/** 诊断卡（借自变体 B）：与保存 / 导入共用同一校验器，常驻头部下方。 */
function DiagnosticsCard() {
  return (
    <div className="mb-5 overflow-hidden rounded-[10px] border border-hairline" style={CARD_STYLE}>
      <div className="border-b border-hairline-soft px-4 py-2.5 text-[12.5px] font-medium text-text">
        1 个错误 · 1 条警告
      </div>
      {MOCK_DIAGNOSTICS.map((d) => (
        <button key={d.message} type="button" className="flex w-full items-start gap-2.5 border-b border-hairline-soft px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-bg-grad-a/50">
          {d.level === "error" ? (
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warm-bright" />
          ) : (
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-3" />
          )}
          <span className="min-w-0 flex-1 text-[12.5px] leading-[1.55] text-text-2">
            <span className="mr-2 text-text-3">{d.section}</span>
            {d.message}
          </span>
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 测试（表单末节，三模式纵向排列）
// ---------------------------------------------------------------------------

function TestCard({ title, badge, badgeWarm, desc, children }: { title: string; badge?: string; badgeWarm?: boolean; desc: string; children: React.ReactNode }) {
  return (
    <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 p-3.5">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-medium text-text">{title}</span>
        {badge && <span className={`text-[11px] ${badgeWarm ? "text-warm-bright/90" : "text-text-3"}`}>{badge}</span>}
      </div>
      <p className="mb-2.5 mt-0.5 text-[12px] leading-[1.55] text-text-3">{desc}</p>
      {children}
    </div>
  );
}

function TestSection() {
  const [ran, setRan] = useState(false);
  const [keySource, setKeySource] = useState("saved");

  return (
    <FormSection step="8" title="测试" desc="保存前验证定义。仅「测试连接」会发起真实调用。">
      <div className="space-y-3">
        <TestCard title="验证响应" desc="粘贴供应商返回的响应，检查各字段的取值路径。">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <textarea
                className={`${INPUT_CLS} h-36 resize-none font-mono text-[11.5px]`}
                defaultValue={'{\n  "status": "processing",\n  "data": {\n    "video_url": "https://cdn.oude.example.com/v/9f21.mp4",\n    "duration": 6.0\n  }\n}'}
              />
              <button type="button" onClick={() => setRan(true)} className={`${ACCENT_BTN_SM_CLS} mt-2`} style={ACCENT_BUTTON_STYLE}>
                检查取值
              </button>
            </div>
            <div className="overflow-hidden rounded-[8px] border border-hairline">
              {(ran ? MOCK_CHECK_RESULT : []).map((r) => (
                <div key={r.field} className="flex items-baseline gap-2.5 border-b border-hairline-soft px-3 py-2 last:border-b-0">
                  <span className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${r.hit ? "bg-good" : "bg-text-4"}`} />
                  <span className="w-24 shrink-0 text-[12px] text-text-2">{FIELD_LABELS[r.field] ?? r.field}</span>
                  <span className="shrink-0 font-mono text-[10.5px] text-good/85">{r.path}</span>
                  <span className="min-w-0 truncate text-[11.5px] text-text-3">{r.value}</span>
                </div>
              ))}
              {!ran && <div className="px-3 py-8 text-center text-[12px] text-text-3">粘贴响应后点「检查取值」查看结果</div>}
            </div>
          </div>
        </TestCard>

        <TestCard title="预览请求" desc="查看以示例参数渲染的完整提交请求。">
          <pre className="overflow-x-auto rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 p-3 font-mono text-[11.5px] leading-[1.6] text-text-2">{MOCK_PREVIEW_REQUEST}</pre>
        </TestCard>

        <TestCard title="测试连接" badge="产生一次调用费用" badgeWarm desc="发起一次真实生成调用，验证从提交到获取视频的完整流程。">
          <div className="grid grid-cols-[240px_1fr] gap-4">
            <div className="space-y-3">
              <label className="block">
                <span className={LABEL_CLS}>密钥来源</span>
                <select className={INPUT_CLS} value={keySource} onChange={(e) => setKeySource(e.target.value)}>
                  <option value="saved">偶得中转站（已保存供应商）</option>
                  <option value="temp">临时输入</option>
                </select>
              </label>
              {keySource === "temp" && (
                <label className="block">
                  <span className={LABEL_CLS}>API 密钥</span>
                  <input type="password" placeholder="仅用于本次测试，不保存" className={`${INPUT_CLS} font-mono text-[12px]`} />
                </label>
              )}
              <label className="block">
                <span className={LABEL_CLS}>测试模型</span>
                <input defaultValue="oude-video-std" className={`${INPUT_CLS} font-mono text-[12px]`} />
              </label>
              <button type="button" className={ACCENT_BTN_SM_CLS} style={ACCENT_BUTTON_STYLE}>
                <Play className="h-3 w-3" />
                开始测试
              </button>
            </div>
            <div>
              <span className={`${LABEL_CLS} !mb-1`}>上次测试 · 2026-08-25 14:02 · 成功</span>
              <div className="rounded-[8px] border border-hairline">
                {MOCK_TRIAL_TIMELINE.map((row) => (
                  <div key={row.t} className="flex items-baseline gap-3 border-b border-hairline-soft px-3 py-1.5 last:border-b-0">
                    <span className="w-10 shrink-0 font-mono text-[10.5px] text-text-3">{row.t}</span>
                    <span className="w-16 shrink-0 text-[11.5px] text-text-2">{row.label}</span>
                    <span className="min-w-0 truncate font-mono text-[11px] text-text-3">{row.detail}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </TestCard>
      </div>
    </FormSection>
  );
}

// ---------------------------------------------------------------------------
// 变体 A 主组件
// ---------------------------------------------------------------------------

export function EndpointPrototypeA() {
  const [selectedKey, setSelectedKey] = useState("ce-7f3a2c");
  const [editorMode, setEditorMode] = useState<"form" | "json">("form");
  const [noAuth, setNoAuth] = useState(false);
  const selected = MOCK_ENDPOINTS.find((e) => e.key === selectedKey) ?? MOCK_ENDPOINTS[0];
  const readonly = selected.kind !== "custom";

  const groups: { label: string; items: MockEndpoint[] }[] = [
    { label: "我的端点", items: MOCK_ENDPOINTS.filter((e) => e.kind === "custom") },
    { label: "内置", items: MOCK_ENDPOINTS.filter((e) => e.kind === "declarative") },
    { label: "内置 · Python", items: MOCK_ENDPOINTS.filter((e) => e.kind === "python") },
  ];

  return (
    <div className="flex min-h-full">
      {/* 列表侧栏（沿 ProviderSection 模式） */}
      <nav className="sticky top-0 max-h-screen w-60 shrink-0 self-start overflow-y-auto border-r border-hairline-soft px-3 py-5" style={{ background: "oklch(0.16 0.010 265 / 0.45)" }}>
        <div className="mb-3 flex items-center gap-1.5 px-1">
          <button type="button" className={`${GHOST_BTN_CLS} flex-1 justify-center`}>
            <Plus className="h-3.5 w-3.5" />
            新建
          </button>
          <button type="button" className={`${GHOST_BTN_CLS} flex-1 justify-center`} title="导入定义文件（.json）">
            <Upload className="h-3.5 w-3.5" />
            导入
          </button>
        </div>
        {groups.map((g) => (
          <div key={g.label} className="mb-4">
            <div className={`${KICKER_CLS} mb-1.5 px-3`}>{g.label}</div>
            {g.items.map((e) => {
              const isActive = e.key === selectedKey;
              return (
                <button
                  key={e.key}
                  type="button"
                  onClick={() => setSelectedKey(e.key)}
                  aria-pressed={isActive}
                  className={
                    "group relative mb-0.5 flex w-full items-center gap-2 rounded-[8px] border px-3 py-2 text-left text-[12.5px] transition-colors " +
                    (isActive
                      ? "border-accent/35 bg-accent-dim text-text shadow-[inset_0_1px_0_oklch(1_0_0_/_0.04),0_0_22px_-10px_var(--color-accent-glow)]"
                      : "border-transparent text-text-3 hover:border-hairline-soft hover:bg-bg-grad-a/55 hover:text-text")
                  }
                >
                  <span aria-hidden className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r-[2px]" style={{ background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))", opacity: isActive ? 1 : 0 }} />
                  {e.kind === "python" && <Lock className="h-3 w-3 shrink-0 text-text-3" />}
                  {e.kind !== "python" && <FileJson2 className="h-3 w-3 shrink-0 text-text-3" />}
                  <span className="min-w-0 flex-1 truncate">{e.name}</span>
                  {e.refCount > 0 && <span className="shrink-0 text-[10px] text-text-3">{e.refCount} 引用</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* 详情区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 px-6 py-6">
          {/* 头部 */}
          <div className="mb-5 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2.5">
                <h2 className="font-editorial text-[20px] text-text">{selected.name}</h2>
                <KindBadge kind={selected.kind} />
              </div>
              <div className="mt-1 flex items-center gap-2.5 text-[12px] text-text-3">
                <span>
                  {selected.author} · v{selected.version}
                </span>
                {selected.refCount > 0 && (
                  <>
                    <span>·</span>
                    <span>{selected.refCount} 个模型正在使用</span>
                  </>
                )}
              </div>
            </div>
            <button type="button" className={GHOST_BTN_CLS} title="新建供应商并使用该端点，自动填入默认接口地址">
              <Plus className="h-3.5 w-3.5" />
              创建供应商
            </button>
            {selected.kind === "custom" ? (
              <>
                <button type="button" className={GHOST_BTN_CLS} title="下载定义文件（不含密钥）">
                  <Download className="h-3.5 w-3.5" />
                  导出
                </button>
                <button
                  type="button"
                  disabled={selected.refCount > 0}
                  title={selected.refCount > 0 ? "该端点正被模型使用，无法删除" : undefined}
                  className={`${GHOST_BTN_CLS} disabled:cursor-not-allowed disabled:opacity-40`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  删除
                </button>
                <button
                  type="button"
                  disabled
                  title="存在未修正的错误，无法保存"
                  className={`${ACCENT_BTN_SM_CLS} disabled:cursor-not-allowed disabled:opacity-40`}
                  style={ACCENT_BUTTON_STYLE}
                >
                  保存更改
                </button>
              </>
            ) : (
              <button type="button" className={GHOST_BTN_CLS} disabled={selected.kind === "python"} title={selected.kind === "python" ? "该端点由代码实现，不支持复制" : undefined}>
                <Copy className="h-3.5 w-3.5" />
                复制为我的
              </button>
            )}
          </div>

          {readonly && (
            <div className="mb-5 rounded-[10px] border border-hairline bg-bg-grad-a/40 px-4 py-3 text-[12.5px] leading-[1.55] text-text-2">
              {selected.kind === "declarative"
                ? "内置端点随版本更新，不可修改。「复制为我的」可创建一份可编辑副本。"
                : "该端点由代码实现，仅展示接口信息。"}
            </div>
          )}

          {/* 诊断（与保存 / 导入共用同一校验器；仅可编辑端点显示） */}
          {selected.kind === "custom" && <DiagnosticsCard />}

          {/* 编辑器切换 */}
          {selected.kind !== "python" && (
            <div className="mb-4 flex items-center gap-2">
              <div className="inline-flex rounded-[8px] border border-hairline bg-bg-grad-a/40 p-0.5">
                {(["form", "json"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setEditorMode(m)}
                    aria-pressed={editorMode === m}
                    className={`rounded-[6px] px-3 py-1 text-[12px] transition-colors ${
                      editorMode === m ? "bg-accent-dim text-accent-2" : "text-text-3 hover:text-text"
                    }`}
                  >
                    {m === "form" ? "表单" : "JSON"}
                  </button>
                ))}
              </div>
            </div>
          )}

          {selected.kind === "python" ? (
            <div className="rounded-[10px] border border-hairline p-4 font-mono text-[12px] text-text-2" style={CARD_STYLE}>
              POST <span className="text-good/85">{selected.path}</span>
            </div>
          ) : editorMode === "json" ? (
            <pre className="overflow-x-auto rounded-[10px] border border-hairline p-4 font-mono text-[11.5px] leading-[1.65] text-text-2" style={CARD_STYLE}>
              {SAMPLE_DEFINITION}
            </pre>
          ) : (
            <div>
              <FormSection step="1" title="基本信息" desc="端点的名称与版本信息。">
                <div className="grid grid-cols-3 gap-3">
                  <Field label="名称" value={selected.name} />
                  <Field label="作者" value={selected.author} />
                  <Field label="版本" value={selected.version} mono />
                </div>
                <div className="mt-3">
                  <Field label="默认接口地址" value="https://api.oude.example.com" mono hint="他人导入该定义时，自动填入供应商设置中的接口地址。" />
                </div>
              </FormSection>
              <FormSection step="2" title="访问凭证" desc="定义请求如何携带 API 密钥。密钥本身在供应商设置中填写。">
                <label className="flex items-center gap-2 text-[12.5px] text-text-2">
                  <input type="checkbox" checked={noAuth} onChange={(e) => setNoAuth(e.target.checked)} className="h-3.5 w-3.5 accent-[var(--color-accent)]" />
                  该接口无需凭证
                </label>
                {noAuth ? (
                  <span className={HINT_CLS}>请求不携带密钥；供应商设置中的密钥留空即可。</span>
                ) : (
                  <div className="mt-3">
                    <div className="grid grid-cols-[180px_1fr] gap-3">
                      <Field label="请求头" value="Authorization" mono />
                      <Field label="内容" value="Bearer {{ api_key }}" mono hint="必须引用 {{ api_key }}，否则请求不携带密钥。" />
                    </div>
                    <VariableChips vars={AUTH_VARS} />
                  </div>
                )}
              </FormSection>
              <FormSection step="3" title="输入素材" desc="声明接口接受的素材及其发送格式。声明后可在请求体中引用对应变量。">
                <div className="space-y-2">
                  {MOCK_INPUT_ROWS.map((row) => (
                    <div key={row.variable} className="grid grid-cols-[200px_180px_1fr_32px] items-center gap-3">
                      <input type="text" readOnly value={row.kind} className={INPUT_CLS} />
                      <input type="text" readOnly value={row.format} className={INPUT_CLS} />
                      <input type="text" readOnly value={row.variable} className={`${INPUT_CLS} font-mono text-[12px]`} />
                      <button type="button" className="grid h-8 w-8 place-items-center rounded-[6px] text-text-3 transition-colors hover:bg-bg-grad-a/55 hover:text-text" title="删除">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  <div className="grid grid-cols-[200px_180px_1fr_32px] gap-3 text-[11.5px] text-text-3">
                    <span className="px-1">素材类型</span>
                    <span className="px-1">发送格式</span>
                    <span className="px-1">对应变量</span>
                    <span />
                  </div>
                </div>
                <button type="button" className={`${GHOST_BTN_CLS} mt-2`}>
                  <Plus className="h-3.5 w-3.5" />
                  添加素材
                </button>
                <span className={HINT_CLS}>可选类型：首帧图片、尾帧图片、参考图（多张）、参考音频。发送格式支持 base64 data URI 与裸 base64；多张素材在请求体中逐张展开。</span>
              </FormSection>
              <FormSection step="4" title="提交生成任务" desc="定义提交任务的请求，并从响应中提取任务 ID。">
                <Field label="请求地址" value="{{ base_url }}/v1/video/generations" mono hint="{{ base_url }} 为供应商设置中的接口地址。" />
                <div className="mt-3">
                  <span className={LABEL_CLS}>请求体（JSON）</span>
                  <pre className="overflow-x-auto rounded-[8px] border border-hairline bg-bg-grad-a/40 p-3 font-mono text-[11.5px] leading-[1.6] text-text-2">{`{
  "model": "{{ model }}",
  "prompt": "{{ prompt }}",
  "image": "{{ image }}",
  "duration": "{{ duration_seconds }}",
  "size": "{{ width }}x{{ height }}"
}`}</pre>
                  <VariableChips
                    vars={SUBMIT_VARS}
                    note="变量在发送时替换为当次生成的实际值。尺寸字段按接口要求选用档位、比例或派生的像素宽高。{{ api_key }} 仅可在「访问凭证」中使用。"
                  />
                </div>
                <div className="mt-3">
                  <PathsField
                    label="任务 ID 的取值路径"
                    paths={["$.id", "$.data.task_id"]}
                    hint="取值路径以 $ 表示响应根节点，逐层书写，如 $.data.task_id。可配置多条备选，按顺序取第一个命中值。"
                  />
                </div>
              </FormSection>
              <FormSection step="5" title="查询进度与结果" desc="定义查询任务进度的请求与各字段的取值路径。任务提交后按此定期查询，直到获得视频地址或错误信息。">
                <Field label="查询地址" value="{{ base_url }}/v1/video/generations/{{ task_id }}" mono hint="{{ task_id }} 为提交后提取的任务 ID。" />
                <div className="mt-3 space-y-3">
                  <PathsField label="任务状态的取值路径" paths={["$.status"]} />
                  <PathsField label="视频地址的取值路径" paths={["$.data.video_url", "$.video_url"]} />
                  <PathsField label="错误信息的取值路径" paths={["$.error.message", "$.message"]} />
                  <PathsField label="视频时长的取值路径（用于用量统计，可选）" paths={["$.data.duration"]} />
                </div>
                <div className="mt-4 border-t border-hairline-soft pt-3.5">
                  <label className="flex items-center gap-2 text-[12.5px] text-text-2">
                    <input type="checkbox" className="h-3.5 w-3.5 accent-[var(--color-accent)]" />
                    生成完成后需再发一次请求获取产物（取件请求）
                  </label>
                  <span className={HINT_CLS}>
                    适用于查询响应不直接返回视频地址的接口。勾选后配置取件请求的地址与取值路径，可引用 {"{{ result_id }}"}。
                  </span>
                  <span className={HINT_CLS}>获得视频地址后自动下载；下载地址与接口地址同源时携带密钥，跨源不携带。</span>
                </div>
              </FormSection>
              <FormSection step="6" title="状态对照" desc="将供应商返回的状态值映射为标准状态。多个状态值可映射为同一标准状态；未配置的状态值按「生成中」处理，继续查询直至超时。">
                <div className="space-y-2">
                  {MOCK_STATUS_ROWS.map((row) => (
                    <div key={row.from} className="grid grid-cols-[220px_16px_180px_32px] items-center gap-3">
                      <input type="text" readOnly value={row.from} className={`${INPUT_CLS} font-mono text-[12px]`} />
                      <span className="text-center text-text-3">→</span>
                      <select className={INPUT_CLS} defaultValue={row.to}>
                        {["排队中", "生成中", "已完成", "失败"].map((s) => (
                          <option key={s}>{s}</option>
                        ))}
                      </select>
                      <button type="button" className="grid h-8 w-8 place-items-center rounded-[6px] text-text-3 transition-colors hover:bg-bg-grad-a/55 hover:text-text" title="删除">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  <div className="grid grid-cols-[220px_16px_180px_32px] gap-3 text-[11.5px] text-text-3">
                    <span className="px-1">供应商状态值</span>
                    <span />
                    <span className="px-1">标准状态</span>
                    <span />
                  </div>
                </div>
                <button type="button" className={`${GHOST_BTN_CLS} mt-2`}>
                  <Plus className="h-3.5 w-3.5" />
                  添加映射
                </button>
              </FormSection>
              <FormSection step="7" title="支持的功能" desc="声明接口支持的生成方式。">
                <div className="flex gap-4 text-[12.5px] text-text-2">
                  <label className="flex items-center gap-1.5">
                    <input type="checkbox" defaultChecked className="h-3.5 w-3.5 accent-[var(--color-accent)]" /> 文生视频
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input type="checkbox" defaultChecked className="h-3.5 w-3.5 accent-[var(--color-accent)]" /> 图生视频
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input type="checkbox" className="h-3.5 w-3.5 accent-[var(--color-accent)]" /> 尾帧
                  </label>
                </div>
              </FormSection>
              <TestSection />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
