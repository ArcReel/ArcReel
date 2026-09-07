// PROTOTYPE — wayfinder #2290 记录详情：`record=<id>` 深链打开的居中弹窗，字段分组对齐 #2288 的 UsageRecord
//（失败原因 / 输入：prompt、参考图、首帧、音色、参数 / 调用 / 产出 / 用量 / 参考费用；供应商原始响应默认折叠）。列表接口不带的字段在此用假数据补齐。评审后整目录删除。
import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ChevronRight, ExternalLink, X } from "lucide-react";

import { ERROR_LABELS, MEDIA_LABELS, PURPOSE_LABELS, RECORDS, durationLabel, money, projectLabel, providerLabel, type UsageRecord } from "./usage-prototype-data";
import { Kicker, MediaGlyph, StatusPill, useProtoFilters, useRecordParam } from "./usage-prototype-shared";

const EDITORIAL = { fontWeight: 400, fontSize: 20, lineHeight: 1.15, letterSpacing: "-0.01em" } as const;

function fullTime(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

interface RefImage {
  label: string;
  path: string;
  hue: number;
}

/** 仅详情接口返回的字段（prompt / last_provider_response / 输入与产出参数）按媒体类型伪造。 */
function extrasOf(r: UsageRecord) {
  const seg = r.segment_id ?? "E1S1";
  const prompt =
    r.media_type === "text"
      ? `你是分镜编剧。根据下面的分集大纲，为第 ${seg.slice(1, 2)} 集写出逐镜脚本，每镜给出画面、台词与时长。\n\n大纲：雨夜，侦探在末班车站台等一个不会来的人……`
      : r.media_type === "audio"
        ? `（旁白，低沉）雨还没停。月台上只剩他一个人，和一盏忽明忽暗的灯。`
        : `分镜 ${seg}：雨夜的车站月台，霓虹倒映在积水里，镜头从远处缓慢推向站在灯下的侦探，冷色调，胶片颗粒。`;
  const refs: RefImage[] =
    r.media_type === "image"
      ? [
          { label: "角色 · 侦探", path: "characters/detective/sheet.png", hue: 290 },
          { label: "场景 · 月台", path: "scenes/platform/ref.png", hue: 220 },
        ]
      : r.media_type === "video"
        ? [{ label: "首帧 · 本分镜图片 v3", path: `segments/${seg}/image_v3.png`, hue: 250 }]
        : [];
  const voice = r.media_type === "audio" ? "Adam · 旁白" : null;
  const params: Array<[string, string]> =
    r.media_type === "video"
      ? [["分辨率", "1280 × 720"], ["画幅", "16:9"], ["时长", "5 s"], ["生成音频", "否"]]
      : r.media_type === "image"
        ? [["分辨率", "1024 × 1024"], ["画幅", "1:1"]]
        : r.media_type === "audio"
          ? [["格式", "mp3 · 44.1 kHz"]]
          : [["温度", "0.7"], ["最大输出", "8 192 tokens"]];
  const response = {
    id: `resp_${r.id.toString(36)}`,
    model: r.model,
    status: r.status === "success" ? "succeeded" : r.status,
    ...(r.error_code ? { error: { code: r.error_code, message: r.error_message } } : {}),
    ...(r.input_tokens !== null ? { usage: { input_tokens: r.input_tokens, output_tokens: r.output_tokens } } : {}),
    created_at: r.started_at,
  };
  return { prompt, refs, voice, params, response };
}

function Thumb({ img }: { img: RefImage }) {
  return (
    <figure className="w-[92px] min-w-0">
      <div
        className="aspect-square w-full rounded-[6px] border border-hairline"
        style={{ background: `linear-gradient(145deg, oklch(0.34 0.06 ${img.hue}), oklch(0.20 0.03 ${img.hue}))` }}
        role="img"
        aria-label={img.label}
      />
      <figcaption className="mt-1 truncate text-[10.5px] text-text-3" title={img.path}>{img.label}</figcaption>
    </figure>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-x-3 py-1 text-[12px]">
      <span className="text-text-4">{label}</span>
      <span className="min-w-0 break-words text-text-2">{children}</span>
    </div>
  );
}

function Group({ kicker, children }: { kicker: string; children: ReactNode }) {
  return (
    <section className="border-t border-hairline-soft pt-3">
      <Kicker tone="muted">{kicker}</Kicker>
      <div className="mt-1.5">{children}</div>
    </section>
  );
}

function Fold({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="border-t border-hairline-soft pt-2">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full items-center gap-1.5 py-1 text-left text-[12px] text-text-3 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
        <ChevronRight className={"h-3.5 w-3.5 transition-transform" + (open ? " rotate-90" : "")} />
        {title}
      </button>
      {open && <div className="mt-1.5 mb-1">{children}</div>}
    </section>
  );
}

function targetOf(r: UsageRecord) {
  if (r.media_type === "text") return PURPOSE_LABELS[r.purpose];
  if (r.purpose === "endpoint_trial") return "端点试跑";
  return r.segment_id ? `分镜 ${r.segment_id}` : MEDIA_LABELS[r.media_type];
}

export function RecordDetailPanel({ record: r, onClose }: { record: UsageRecord; onClose: () => void }) {
  const [, set] = useProtoFilters();
  const x = extrasOf(r);
  const failed = r.status === "failed";
  const error = failed ? (r.error_code ? ERROR_LABELS[r.error_code] : r.error_message) : null;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 设置页内容区带 transform，fixed 会被困在里面；挂到 body 才能贴住视口
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
      <div className="absolute inset-0 bg-black/55" onClick={onClose} aria-hidden />
      <section
        role="dialog"
        aria-modal="true"
        aria-label={`记录 #${r.id} 详情`}
        className="relative flex max-h-[85vh] w-[36rem] max-w-full flex-col rounded-[12px] border border-hairline shadow-2xl shadow-black/60"
        style={{ background: "linear-gradient(180deg, oklch(0.19 0.011 265), oklch(0.15 0.010 265))" }}
      >
        <header className="flex items-start gap-3 px-6 pt-5 pb-4">
          <div className="min-w-0 flex-1">
            <Kicker>Record · #{r.id}</Kicker>
            <h3 className="font-editorial mt-1 truncate text-text" style={EDITORIAL}>{targetOf(r)}</h3>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-text-3">
              <MediaGlyph type={r.media_type} size={12} />
              <span>{MEDIA_LABELS[r.media_type]}</span>
              <span className="text-text-4">·</span>
              <span>{projectLabel(r.project_name)}</span>
              <StatusPill status={r.status} />
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭" className="rounded-[6px] p-1 text-text-4 hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-6 pb-6">
          {failed && (
            <section className="rounded-[8px] border border-danger/25 bg-[oklch(0.30_0.10_25/0.14)] px-3 py-2.5 text-[12px] leading-[1.55]">
              <div className="text-danger-2">{error}</div>
              {r.error_message && <div className="mt-1 break-all font-mono text-[10.5px] text-text-4">{r.error_message}</div>}
            </section>
          )}

          <Group kicker="Inputs">
            <p className="whitespace-pre-wrap rounded-[6px] bg-bg-grad-b/60 px-3 py-2 text-[12px] leading-[1.6] text-text-2">{x.prompt}</p>
            {x.refs.length > 0 && (
              <div className="mt-2.5 flex flex-wrap gap-3">
                {x.refs.map((img) => <Thumb key={img.path} img={img} />)}
              </div>
            )}
            <div className="mt-2">
              {x.voice && <Field label="音色">{x.voice}</Field>}
              {x.params.map(([k, v]) => <Field key={k} label={k}><span className="num">{v}</span></Field>)}
            </div>
          </Group>

          <Group kicker="Call">
            <Field label="供应商">{providerLabel(r.provider)}</Field>
            <Field label="模型"><span className="font-mono">{r.model}</span></Field>
            <Field label="用途">{PURPOSE_LABELS[r.purpose]}</Field>
            {r.task_type && <Field label="任务"><span className="font-mono">{r.task_type}</span></Field>}
            <Field label="开始"><span className="num">{fullTime(r.started_at)}</span></Field>
            <Field label="结束"><span className="num">{fullTime(r.finished_at)}</span></Field>
            <Field label="耗时"><span className="num">{durationLabel(r.duration_ms)}</span></Field>
          </Group>

          {r.output_path && (
            <Group kicker="Output">
              <div className="flex items-start gap-3">
                {r.media_type !== "audio" && <Thumb img={{ label: r.media_type === "video" ? "视频 · 首帧" : "图片", path: r.output_path, hue: 150 }} />}
                <div className="min-w-0 flex-1">
                  <Field label="文件">
                    <button type="button" className="inline-flex max-w-full items-center gap-1 font-mono text-[11.5px] text-accent-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                      <span className="truncate">{r.output_path}</span>
                      <ExternalLink className="h-3 w-3 shrink-0" />
                    </button>
                  </Field>
                </div>
              </div>
            </Group>
          )}

          {r.input_tokens !== null && (
            <Group kicker="Usage">
              <Field label="输入"><span className="num">{r.input_tokens.toLocaleString()} tokens</span></Field>
              <Field label="输出"><span className="num">{(r.output_tokens ?? 0).toLocaleString()} tokens</span></Field>
            </Group>
          )}

          <Group kicker="Ref. cost">
            <div className="flex items-baseline gap-2">
              <span className="font-editorial text-[26px] text-text">{r.cost_amount > 0 ? money(r.currency, r.cost_amount, r.cost_amount < 0.1 ? 4 : 2) : "—"}</span>
              {r.cost_amount > 0 && <span className="font-mono text-[10.5px] text-text-4">{r.currency}</span>}
            </div>
            <div className="mt-1 text-[11px] text-text-4">{r.cost_amount > 0 ? "按你配置的单价估算，只作参考" : "未产生费用"}</div>
          </Group>

          <Fold title="供应商原始响应">
            <pre className="overflow-x-auto rounded-[6px] bg-bg-grad-b/60 px-3 py-2 font-mono text-[10.5px] leading-[1.55] text-text-3">{JSON.stringify(x.response, null, 2)}</pre>
          </Fold>
        </div>

        {r.segment_id && (
          <footer className="flex items-center gap-2 border-t border-hairline px-6 py-3">
            <button
              type="button"
              onClick={() => {
                set({ segment: r.segment_id, status: null });
                onClose();
              }}
              className="rounded-[6px] border border-hairline px-2.5 py-1 text-[12px] text-text-2 transition-colors hover:border-accent/50 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              只看分镜 {r.segment_id}
            </button>
            <span className="text-[11px] text-text-4">写入筛选后关闭面板</span>
          </footer>
        )}
      </section>
    </div>,
    document.body,
  );
}

/** 挂在原型宿主上：读 record=<id>，找得到就渲染面板；找不到（伪造 404）给一条提示。 */
export function RecordDetailHost() {
  const [id, setId] = useRecordParam();
  if (id === null) return null;
  const record = RECORDS.find((r) => r.id === id);
  const close = () => setId(null);
  if (!record)
    return (
      <div className="fixed bottom-16 right-5 z-50 rounded-[8px] border border-hairline bg-surface-2 px-3 py-2 text-[12px] text-text-3 shadow-lg">
        记录 #{id} 不存在或已被清理
        <button type="button" onClick={close} className="ml-3 text-accent-2 hover:underline">关闭</button>
      </div>
    );
  return <RecordDetailPanel record={record} onClose={close} />;
}
