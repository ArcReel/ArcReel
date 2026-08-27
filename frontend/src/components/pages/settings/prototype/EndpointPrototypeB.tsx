// PROTOTYPE — 变体 B「代码工作台」（wayfinder #2129）
// 结构主张：
//  * 位置：独立小节，但落地页是一张「登记簿」表格（键 / 实现 / 版本 / 引用数），
//    信息密度优先；点行进入全屏工作台。
//  * 编辑器：原始 JSON 编辑器为唯一编辑形态（无结构化表单），辅以节大纲快跳
//    与实时诊断列表（与保存/导入共用同一校验器，422 diagnostic 同构）。
//  * 试跑器：工作台右栏三卡常驻并列（验证响应 / 预览请求 / 测试连接），
//    不藏在 tab 后，随改随看。
import { useState } from "react";
import { ArrowLeft, CircleAlert, Play, Plus, TriangleAlert, Upload } from "lucide-react";
import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { MOCK_CHECK_RESULT, MOCK_ENDPOINTS, MOCK_PREVIEW_REQUEST, MOCK_TRIAL_TIMELINE, SAMPLE_DEFINITION } from "./endpoint-prototype-data";

const KICKER_CLS = "font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-text-4";

const OUTLINE = ["meta", "auth", "inputs", "submit", "poll", "status_map", "capabilities"];

const MOCK_DIAGNOSTICS = [
  { level: "error" as const, path: "$.auth", code: "auth_missing_api_key", message: "auth 节没有引用 {{ api_key }}，凭证无处附带" },
  { level: "warning" as const, path: "$.poll.extract.video_url[1]", code: "path_double_dot", message: "使用了被禁用的 `..` 递归下降，请改写为 child segment" },
];

// ---------------------------------------------------------------------------
// 登记簿（落地列表）
// ---------------------------------------------------------------------------

function Ledger({ onOpen }: { onOpen: (key: string) => void }) {
  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-5 flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">Endpoint Ledger</div>
          <h2 className="font-editorial mt-0.5 text-[22px] text-text">调用端点登记簿</h2>
        </div>
        <button type="button" className={GHOST_BTN_CLS}>
          <Upload className="h-3.5 w-3.5" />
          导入定义文件
        </button>
        <button type="button" className={ACCENT_BTN_SM_CLS} style={ACCENT_BUTTON_STYLE}>
          <Plus className="h-3.5 w-3.5" />
          新建端点
        </button>
      </div>

      <div className="overflow-hidden rounded-[12px] border border-hairline" style={CARD_STYLE}>
        <div className="grid grid-cols-[1fr_150px_110px_90px_90px] gap-3 border-b border-hairline px-4 py-2.5">
          {["端点", "键", "实现", "版本", "引用"].map((h) => (
            <span key={h} className={KICKER_CLS}>
              {h}
            </span>
          ))}
        </div>
        {MOCK_ENDPOINTS.map((e) => (
          <button
            key={e.key}
            type="button"
            onClick={() => onOpen(e.key)}
            className="grid w-full grid-cols-[1fr_150px_110px_90px_90px] items-center gap-3 border-b border-hairline-soft px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-bg-grad-a/50"
          >
            <span className="min-w-0">
              <span className="block truncate text-[13px] text-text">{e.name}</span>
              <span className="block truncate font-mono text-[10.5px] text-text-4">POST {e.path}</span>
            </span>
            <span className="truncate font-mono text-[11px] text-good/70">{e.key}</span>
            <span
              className={`justify-self-start rounded-[5px] border px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] ${
                e.kind === "custom" ? "border-accent/35 bg-accent-dim text-accent-2" : "border-hairline-soft text-text-3"
              }`}
            >
              {e.kind === "custom" ? "自定义" : e.kind === "declarative" ? "声明式" : "Python"}
            </span>
            <span className="font-mono text-[11px] text-text-3">{e.version}</span>
            <span className="font-mono text-[11px] text-text-3">{e.refCount > 0 ? `${e.refCount} 模型行` : "—"}</span>
          </button>
        ))}
      </div>
      <p className="mt-3 text-[11.5px] text-text-4">内置端点（声明式 / Python）随版分发、只读；点入可查看，声明式可复制为自定义副本。被模型行引用的端点不可删除。</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 工作台
// ---------------------------------------------------------------------------

function Workbench({ endpointKey, onBack }: { endpointKey: string; onBack: () => void }) {
  const ep = MOCK_ENDPOINTS.find((e) => e.key === endpointKey) ?? MOCK_ENDPOINTS[0];
  const [checked, setChecked] = useState(false);

  return (
    <div className="flex min-h-full flex-col">
      {/* 工作台顶条 */}
      <div className="flex items-center gap-3 border-b border-hairline px-5 py-3">
        <button type="button" onClick={onBack} className={GHOST_BTN_CLS}>
          <ArrowLeft className="h-3.5 w-3.5" />
          登记簿
        </button>
        <div className="min-w-0 flex-1">
          <span className="text-[13.5px] text-text">{ep.name}</span>
          <span className="ml-2.5 font-mono text-[11px] text-good/70">{ep.key}</span>
        </div>
        <span className="font-mono text-[10.5px] text-text-4">
          <TriangleAlert className="mr-1 inline h-3 w-3 text-warm-bright" />1 错误 · 1 警告
        </span>
        <button type="button" disabled className={`${ACCENT_BTN_SM_CLS} disabled:opacity-40`} style={ACCENT_BUTTON_STYLE} title="有校验错误时不可保存">
          保存
        </button>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_360px]">
        {/* 左：JSON 编辑器 + 诊断 */}
        <div className="flex min-h-0 flex-col border-r border-hairline">
          {/* 节大纲快跳 */}
          <div className="flex items-center gap-1 overflow-x-auto border-b border-hairline-soft px-4 py-2">
            {OUTLINE.map((s) => (
              <button key={s} type="button" className="shrink-0 rounded-[5px] border border-transparent px-2 py-0.5 font-mono text-[10.5px] text-text-4 transition-colors hover:border-hairline-soft hover:text-text-2">
                {s}
              </button>
            ))}
          </div>
          <textarea
            spellCheck={false}
            className="min-h-[380px] flex-1 resize-none bg-transparent px-5 py-4 font-mono text-[11.5px] leading-[1.7] text-text-2 outline-none"
            defaultValue={SAMPLE_DEFINITION}
          />
          {/* 诊断列表（与保存/导入共用校验器） */}
          <div className="border-t border-hairline">
            <div className={`${KICKER_CLS} px-4 pb-1 pt-2.5`}>诊断 · 与保存 / 导入共用同一校验器</div>
            {MOCK_DIAGNOSTICS.map((d) => (
              <button key={d.code} type="button" className="flex w-full items-baseline gap-2.5 px-4 py-1.5 text-left transition-colors hover:bg-bg-grad-a/50">
                <CircleAlert className={`h-3 w-3 shrink-0 self-center ${d.level === "error" ? "text-warm-bright" : "text-text-4"}`} />
                <span className="shrink-0 font-mono text-[10.5px] text-good/70">{d.path}</span>
                <span className="min-w-0 flex-1 truncate text-[11.5px] text-text-3">{d.message}</span>
                <span className="shrink-0 font-mono text-[9.5px] uppercase text-text-4">{d.code}</span>
              </button>
            ))}
          </div>
        </div>

        {/* 右：三卡常驻试跑器 */}
        <div className="min-h-0 space-y-4 overflow-y-auto px-4 py-4" style={{ background: "oklch(0.15 0.010 265 / 0.5)" }}>
          <section className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
            <div className="mb-2 flex items-center gap-2">
              <span className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-accent-2">① 验证响应</span>
              <span className="text-[10.5px] text-text-4">离线 · 免费</span>
            </div>
            <textarea className={`${INPUT_CLS} h-20 resize-none font-mono text-[11px]`} placeholder="粘贴一段真实供应商响应…" defaultValue={'{ "status": "processing", "data": { … } }'} />
            <button type="button" onClick={() => setChecked(true)} className={`${GHOST_BTN_CLS} mt-2`}>
              校验取值路径
            </button>
            {checked && (
              <div className="mt-2.5 space-y-1">
                {MOCK_CHECK_RESULT.map((r) => (
                  <div key={r.field} className="flex items-baseline gap-2">
                    <span className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${r.hit ? "bg-good" : "bg-text-4"}`} />
                    <span className="w-32 shrink-0 truncate font-mono text-[10.5px] text-text-2">{r.field}</span>
                    <span className="min-w-0 truncate text-[10.5px] text-text-4">{r.hit ? r.path : "无命中"}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
            <div className="mb-2 flex items-center gap-2">
              <span className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-accent-2">② 预览请求</span>
              <span className="text-[10.5px] text-text-4">随编辑实时渲染</span>
            </div>
            <pre className="max-h-44 overflow-auto rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 p-2.5 font-mono text-[10.5px] leading-[1.6] text-text-3">{MOCK_PREVIEW_REQUEST}</pre>
          </section>

          <section className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
            <div className="mb-2 flex items-center gap-2">
              <span className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-accent-2">③ 测试连接</span>
              <span className="text-[10.5px] text-warm-bright/85">真实调用 · 计费</span>
            </div>
            <div className="mb-2 grid grid-cols-2 gap-2">
              <select className={`${INPUT_CLS} px-2 py-1.5 text-[11.5px]`}>
                <option>偶得中转站</option>
                <option>临时内联凭证…</option>
              </select>
              <input readOnly value="oude-video-std" className={`${INPUT_CLS} px-2 py-1.5 font-mono text-[11.5px]`} />
            </div>
            <button type="button" className={ACCENT_BTN_SM_CLS} style={ACCENT_BUTTON_STYLE}>
              <Play className="h-3 w-3" />
              发起试跑
            </button>
            <div className="mt-2.5 space-y-1">
              {MOCK_TRIAL_TIMELINE.slice(0, 4).map((row) => (
                <div key={row.t} className="flex items-baseline gap-2 font-mono text-[10.5px]">
                  <span className="w-9 shrink-0 text-text-4">{row.t}</span>
                  <span className="min-w-0 truncate text-text-3">{row.detail}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

export function EndpointPrototypeB() {
  const [openKey, setOpenKey] = useState<string | null>(null);
  return openKey ? <Workbench endpointKey={openKey} onBack={() => setOpenKey(null)} /> : <Ledger onOpen={setOpenKey} />;
}
