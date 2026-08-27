// PROTOTYPE — 变体 C「就地接线」（wayfinder #2129）
// 结构主张：
//  * 位置：不新增设置小节。自定义调用端点在「用到它的地方」管理——供应商表单模型行的
//    EndpointSelect 下拉里长出「自定义」分组与「新建调用端点…」入口，管理与挂接零距离。
//  * 编辑器：右侧滑出抽屉里的四步向导（基础与鉴权 → 提交 → 轮询与提取 → 试跑核验），
//    每步只面对少量字段；无 JSON 视图，导入/导出走定义文件按钮。
//  * 试跑器：不是独立面板，而是向导第 4 步的「核验清单」——三模式各一行，
//    验证响应/预览请求免费先行，测试连接最后压轴，全绿才鼓励保存。
import { useState } from "react";
import { Check, ChevronDown, ChevronRight, CircleDashed, Film, Pencil, Play, Plus, X } from "lucide-react";
import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { MOCK_ENDPOINTS } from "./endpoint-prototype-data";

const KICKER_CLS = "font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-text-4";

// ---------------------------------------------------------------------------
// 宿主 mock：自定义供应商表单里的一条模型行 + 展开的 EndpointSelect 弹层
// ---------------------------------------------------------------------------

function HostModelRow({ onNew, onEdit }: { onNew: () => void; onEdit: () => void }) {
  const [open, setOpen] = useState(true);
  const builtins = MOCK_ENDPOINTS.filter((e) => e.kind !== "custom");
  const customs = MOCK_ENDPOINTS.filter((e) => e.kind === "custom");

  return (
    <div className="rounded-[10px] border border-hairline p-3" style={CARD_STYLE}>
      <div className="flex flex-wrap items-center gap-2">
        <input type="checkbox" defaultChecked className="h-3.5 w-3.5 accent-[var(--color-accent)]" aria-label="启用模型" />
        <input readOnly value="oude-video-std" className="min-w-0 flex-1 rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 font-mono text-[12.5px] text-text" aria-label="模型 ID" />
        {/* EndpointSelect trigger（展开态） */}
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="group inline-flex items-center gap-2 rounded-[8px] border border-accent/55 bg-bg-grad-a/55 px-2.5 py-1.5 text-left text-sm text-text ring-2 ring-accent"
        >
          <span className="truncate">偶得视频 · 提交+轮询</span>
          <span className="hidden font-mono text-[11px] tracking-tight text-good/80 sm:inline">/video/generations</span>
          <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-text-4 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        <span className="rounded-[6px] border border-hairline px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3">默认</span>
      </div>

      {/* 弹层（原型内联渲染，实际为 Popover） */}
      {open && (
        <div className="mt-2 w-[24rem] rounded-xl border border-hairline shadow-2xl shadow-black/40" style={{ background: "linear-gradient(180deg, oklch(0.20 0.011 265 / 0.97), oklch(0.16 0.010 265 / 0.97))" }}>
          <div className="max-h-72 overflow-y-auto py-1.5">
            <div className="flex items-center gap-1.5 px-3 pb-1 pt-2">
              <Film className="h-3 w-3 text-text-4" />
              <span className={KICKER_CLS}>视频 · 内置</span>
            </div>
            <ul className="px-1.5">
              {builtins.slice(0, 3).map((e) => (
                <li key={e.key}>
                  <button type="button" className="w-full rounded-lg px-3.5 py-2 text-left transition-colors hover:bg-bg-grad-a/50">
                    <div className="truncate text-sm text-text-2">{e.name}</div>
                    <div className="mt-0.5 flex items-baseline gap-1.5 font-mono text-[11px] leading-none">
                      <span className="text-text-4">POST</span>
                      <span className="truncate text-good/80">{e.path}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
            <div className="mx-3 my-1 h-px bg-hairline-soft" />
            <div className="flex items-center gap-1.5 px-3 pb-1 pt-2">
              <Film className="h-3 w-3 text-accent-2" />
              <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-accent-2">视频 · 自定义</span>
            </div>
            <ul className="px-1.5">
              {customs.map((e, i) => (
                <li key={e.key}>
                  <div className={`group flex items-center rounded-lg transition-colors hover:bg-bg-grad-a/50 ${i === 0 ? "bg-accent-dim" : ""}`}>
                    <button type="button" className="min-w-0 flex-1 px-3.5 py-2 text-left">
                      <div className={`truncate text-sm ${i === 0 ? "text-text" : "text-text-2"}`}>{e.name}</div>
                      <div className="mt-0.5 flex items-baseline gap-1.5 font-mono text-[11px] leading-none">
                        <span className="truncate text-good/80">{e.key}</span>
                        <span className="text-text-4">· v{e.version}</span>
                      </div>
                    </button>
                    <button type="button" onClick={onEdit} aria-label="编辑该端点" className="mr-2 rounded p-1 text-text-4 opacity-0 transition-opacity hover:text-text group-hover:opacity-100">
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
          {/* 底部动作条：管理入口就长在下拉里 */}
          <div className="flex items-center border-t border-hairline-soft px-1.5 py-1">
            <button type="button" onClick={onNew} className="flex flex-1 items-center gap-2 rounded-lg px-3.5 py-2 text-left text-[12.5px] text-accent-2 transition-colors hover:bg-bg-grad-a/50">
              <Plus className="h-3.5 w-3.5" />
              新建调用端点…
            </button>
            <button type="button" className="rounded-lg px-3 py-2 text-[12px] text-text-4 transition-colors hover:bg-bg-grad-a/50 hover:text-text-2">
              导入定义文件
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 四步向导抽屉
// ---------------------------------------------------------------------------

const STEPS = ["基础与鉴权", "提交请求", "轮询与提取", "试跑核验"];

function Wizard({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [trialState, setTrialState] = useState<[boolean, boolean, boolean]>([false, false, false]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" role="dialog" aria-label="新建调用端点">
      <div className="flex h-full w-[560px] flex-col border-l border-hairline" style={{ background: "linear-gradient(180deg, oklch(0.19 0.011 265), oklch(0.14 0.010 265))" }}>
        {/* 抽屉头 */}
        <div className="flex items-center gap-3 border-b border-hairline px-5 py-4">
          <div className="min-w-0 flex-1">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">New Endpoint</div>
            <h2 className="font-editorial mt-0.5 text-[18px] text-text">新建调用端点</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭" className="rounded p-1.5 text-text-4 transition-colors hover:text-text">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 步骤条 */}
        <div className="flex items-center gap-1 border-b border-hairline-soft px-5 py-3">
          {STEPS.map((s, i) => (
            <div key={s} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="h-3 w-3 text-text-4" />}
              <button
                type="button"
                onClick={() => setStep(i)}
                aria-current={step === i ? "step" : undefined}
                className={`flex items-center gap-1.5 rounded-[6px] px-2 py-1 font-mono text-[10.5px] font-bold uppercase tracking-[0.1em] transition-colors ${
                  step === i ? "bg-accent-dim text-accent-2" : i < step ? "text-good/80" : "text-text-4 hover:text-text-2"
                }`}
              >
                {i < step ? <Check className="h-3 w-3" /> : <span>{i + 1}</span>}
                {s}
              </button>
            </div>
          ))}
        </div>

        {/* 步骤内容 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          {step === 0 && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className={`${KICKER_CLS} mb-1 block`}>名称</span>
                  <input className={INPUT_CLS} defaultValue="偶得视频 · 提交+轮询" />
                </label>
                <label className="block">
                  <span className={`${KICKER_CLS} mb-1 block`}>版本</span>
                  <input className={`${INPUT_CLS} font-mono`} defaultValue="1.0.0" />
                </label>
              </div>
              <label className="block">
                <span className={`${KICKER_CLS} mb-1 block`}>建议 base_url</span>
                <input className={`${INPUT_CLS} font-mono text-[12px]`} defaultValue="https://api.oude.example.com" />
              </label>
              <div>
                <span className={`${KICKER_CLS} mb-1 block`}>鉴权 · Header</span>
                <div className="grid grid-cols-[150px_1fr] gap-2">
                  <input className={`${INPUT_CLS} font-mono text-[12px]`} defaultValue="Authorization" />
                  <input className={`${INPUT_CLS} font-mono text-[12px]`} defaultValue="Bearer {{ api_key }}" />
                </div>
                <p className="mt-1.5 text-[11px] text-text-4">凭证保存在供应商上；定义里只写模板，须至少一处引用 {"{{ api_key }}"}。</p>
              </div>
              <div>
                <span className={`${KICKER_CLS} mb-1 block`}>素材输入</span>
                <div className="grid grid-cols-[130px_1fr_1fr] gap-2">
                  <input className={`${INPUT_CLS} font-mono text-[12px]`} defaultValue="image" />
                  <select className={INPUT_CLS}>
                    <option>首帧图</option>
                    <option>尾帧图</option>
                    <option>参考图</option>
                  </select>
                  <select className={INPUT_CLS}>
                    <option>base64 data URI</option>
                    <option>裸 base64</option>
                  </select>
                </div>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <label className="block">
                <span className={`${KICKER_CLS} mb-1 block`}>URL 模板</span>
                <input className={`${INPUT_CLS} font-mono text-[12px]`} defaultValue="{{ base_url }}/v1/video/generations" />
              </label>
              <div>
                <span className={`${KICKER_CLS} mb-1 block`}>请求体模板</span>
                <textarea
                  spellCheck={false}
                  className={`${INPUT_CLS} h-44 resize-none font-mono text-[11.5px] leading-[1.6]`}
                  defaultValue={`{\n  "model": "{{ model }}",\n  "prompt": "{{ prompt }}",\n  "image": "{{ image }}",\n  "duration": "{{ duration_seconds }}"\n}`}
                />
              </div>
              <label className="block">
                <span className={`${KICKER_CLS} mb-1 block`}>task_id 提取路径（可多条，依次尝试）</span>
                <input className={`${INPUT_CLS} font-mono text-[12px]`} defaultValue="$.id, $.data.task_id" />
              </label>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <label className="block">
                <span className={`${KICKER_CLS} mb-1 block`}>轮询 URL 模板</span>
                <input className={`${INPUT_CLS} font-mono text-[12px]`} defaultValue="{{ base_url }}/v1/video/generations/{{ task_id }}" />
              </label>
              {[
                ["status", "$.status"],
                ["video_url", "$.data.video_url, $.video_url"],
                ["error", "$.error.message, $.message"],
                ["usage.duration_seconds", "$.data.duration"],
              ].map(([f, p]) => (
                <label key={f} className="block">
                  <span className={`${KICKER_CLS} mb-1 block`}>{f}</span>
                  <input className={`${INPUT_CLS} font-mono text-[12px]`} defaultValue={p} />
                </label>
              ))}
              <div>
                <span className={`${KICKER_CLS} mb-1 block`}>状态映射</span>
                <div className="flex flex-wrap gap-2">
                  {[
                    ["queued", "queued"],
                    ["processing", "running"],
                    ["succeeded", "succeeded"],
                    ["failed", "failed"],
                  ].map(([from, to]) => (
                    <span key={from} className="inline-flex items-center gap-1.5 rounded-[6px] border border-hairline bg-bg-grad-a/55 px-2 py-1 font-mono text-[11px]">
                      <span className="text-text-2">{from}</span>
                      <span className="text-text-4">→</span>
                      <span className="text-accent-2">{to}</span>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-3">
              <p className="text-[12px] leading-[1.6] text-text-3">保存前把三项核验跑绿。前两项离线免费，最后一项发起一次真实调用（计费）。</p>
              {[
                { label: "验证响应 · 粘贴真实响应校验取值路径", free: true },
                { label: "预览请求 · 检查渲染出的提交请求", free: true },
                { label: "测试连接 · 真实提交一次并跑完全链路", free: false },
              ].map((item, i) => (
                <div key={item.label} className="flex items-center gap-3 rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
                  {trialState[i] ? <Check className="h-4 w-4 shrink-0 text-good" /> : <CircleDashed className="h-4 w-4 shrink-0 text-text-4" />}
                  <span className="min-w-0 flex-1 text-[12.5px] text-text-2">{item.label}</span>
                  {!item.free && <span className="shrink-0 text-[10.5px] text-warm-bright/85">计费</span>}
                  <button
                    type="button"
                    onClick={() =>
                      setTrialState((prev) => {
                        const next: [boolean, boolean, boolean] = [...prev];
                        next[i] = true;
                        return next;
                      })
                    }
                    className={GHOST_BTN_CLS}
                  >
                    <Play className="h-3 w-3" />
                    运行
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 抽屉脚 */}
        <div className="flex items-center gap-2.5 border-t border-hairline px-5 py-3.5">
          <button type="button" onClick={onClose} className={GHOST_BTN_CLS}>
            取消
          </button>
          <span className="flex-1" />
          {step > 0 && (
            <button type="button" onClick={() => setStep((s) => s - 1)} className={GHOST_BTN_CLS}>
              上一步
            </button>
          )}
          {step < 3 ? (
            <button type="button" onClick={() => setStep((s) => s + 1)} className={ACCENT_BTN_SM_CLS} style={ACCENT_BUTTON_STYLE}>
              下一步
            </button>
          ) : (
            <button type="button" onClick={onClose} disabled={!trialState.every(Boolean)} title={trialState.every(Boolean) ? undefined : "三项核验跑绿后保存（原型中可点「运行」模拟）"} className={`${ACCENT_BTN_SM_CLS} disabled:opacity-40`} style={ACCENT_BUTTON_STYLE}>
              保存并挂接到模型行
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 变体 C 主组件
// ---------------------------------------------------------------------------

export function EndpointPrototypeC() {
  const [wizardOpen, setWizardOpen] = useState(false);

  return (
    <div className="mx-auto max-w-3xl px-8 py-8">
      <div className="mb-1 font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">In-place Wiring</div>
      <h2 className="font-editorial mb-2 text-[22px] text-text">供应商表单 · 模型行（宿主 mock）</h2>
      <p className="mb-5 max-w-xl text-[12px] leading-[1.6] text-text-3">
        本变体不新增设置小节：下面是既有「自定义供应商」表单里的一条模型行，EndpointSelect 下拉新增「自定义」分组，管理入口（新建 / 编辑 / 导入）就长在下拉里，选完即挂接。
      </p>
      <HostModelRow onNew={() => setWizardOpen(true)} onEdit={() => setWizardOpen(true)} />
      <p className="mt-4 text-[11.5px] text-text-4">代价：没有集中列表页——查看全部端点、导出、删除要靠下拉与编辑抽屉承担；跨供应商复用时入口分散。</p>
      {wizardOpen && <Wizard onClose={() => setWizardOpen(false)} />}
    </div>
  );
}
