// PROTOTYPE — #2973：四类资产共用的详情编辑器。
// 布局按容器宽度切换：窄（Sheet、主从的窄档）单栏；宽（≥ 720px 容器）左图右表单。
// 草稿规则沿用「保存方式」结论：文本字段进草稿、显式保存；改名、上传、生成立即执行；
// 有草稿时生成按钮变为「保存并生成」，底部出现内联未保存提示条。

import { useId, useState, type ReactNode } from "react";
import { Copy, ImageUp, Maximize2, Pause, Pencil, Play, Plus, Sparkles, Upload, X } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import type { ActionsMode } from "./proto-params";
import { TYPE_LABEL, type ProtoAsset, type ProtoDerivative, type ProtoStatus } from "./asset-model";
import { AssetMoreMenu, SheetImage, StatusChip, stub } from "./parts";
import { useVersions } from "./use-versions";

export interface Draft {
  description: string;
  voiceStyle: string;
  brand: string;
  sellingPoints: string;
}

export function draftOf(asset: ProtoAsset): Draft {
  return {
    description: asset.description,
    voiceStyle: asset.voiceStyle ?? "",
    brand: asset.brand ?? "",
    sellingPoints: (asset.sellingPoints ?? []).join("\n"),
  };
}

export function isDirty(asset: ProtoAsset, draft: Draft) {
  const base = draftOf(asset);
  return (Object.keys(base) as (keyof Draft)[]).some((k) => base[k] !== draft[k]);
}

const FIELD =
  "w-full rounded-lg border border-input bg-input/30 px-3 py-2 text-[13px] leading-[1.55] text-foreground outline-none transition-colors duration-150 placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";
const AUTO_TEXTAREA = cn(FIELD, "resize-none [field-sizing:content] min-h-[4.5rem] max-h-80 overflow-y-auto");

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex min-h-7 items-center justify-between gap-2">
        <h3 className="text-[12px] font-medium text-muted-foreground">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function AssetDetailEditor({
  asset,
  status,
  projectName,
  actions,
  draft,
  onDraftChange,
  onView,
  headerEnd,
  headerStart,
  className,
}: {
  asset: ProtoAsset;
  status: ProtoStatus;
  projectName: string;
  actions: ActionsMode;
  draft: Draft;
  onDraftChange: (d: Draft) => void;
  onView: () => void;
  headerEnd?: ReactNode;
  headerStart?: ReactNode;
  className?: string;
}) {
  const dirty = isDirty(asset, draft);
  const pinned = actions === "pinned";
  const versions = useVersions(projectName, asset.versionResource, asset.name, Boolean(asset.sheetPath));
  const set = (patch: Partial<Draft>) => onDraftChange({ ...draft, ...patch });
  const descId = useId();

  const generateLabel = dirty
    ? "保存并生成"
    : asset.sheetPath
      ? "重新生成资产图"
      : "生成资产图";

  const media = (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <div className="group/img relative overflow-hidden rounded-lg border border-border">
          <button
            type="button"
            className="block w-full outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default"
            onClick={onView}
            disabled={!asset.sheetUrl}
            aria-label={`${asset.name}：查看大图`}
          >
            <SheetImage asset={asset} />
          </button>
          <StatusChip status={status} className="absolute left-2 top-2" />
          {asset.sheetUrl && (
            <span className="pointer-events-none absolute right-2 top-2 grid size-7 place-items-center rounded-md bg-background/80 text-foreground opacity-0 transition-opacity duration-150 group-hover/img:opacity-100 group-focus-within/img:opacity-100">
              <Maximize2 className="size-3.5" />
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            className="flex-1"
            disabled={status === "generating" || !draft.description.trim()}
            onClick={() => stub(generateLabel)}
          >
            <Sparkles />
            {status === "generating" ? "生成中…" : generateLabel}
          </Button>
          {asset.sheetPath && versions && versions.versions.length > 0 && (
            <button
              type="button"
              onClick={() => stub("版本历史")}
              className="shrink-0 rounded-md px-1.5 py-1 text-[12px] tabular-nums text-muted-foreground transition-colors hover:text-foreground"
            >
              第 {versions.current} 版 / 共 {versions.versions.length} 版
            </button>
          )}
        </div>
        {!draft.description.trim() && (
          <p className="text-[12px] text-warn">先填写描述，才能生成资产图。</p>
        )}
      </div>

      {asset.type === "character" && (
        <Section
          title="原图"
          aside={
            <Button variant="ghost" size="xs" onClick={() => stub("上传原图")}>
              <Upload /> {asset.referenceUrls.length ? "替换" : "上传"}
            </Button>
          }
        >
          {asset.referenceUrls[0] ? (
            <img src={asset.referenceUrls[0]} alt={`${asset.name} 原图`} className="h-28 w-full rounded-lg border border-border object-cover" />
          ) : (
            <p className="text-[12px] text-muted-foreground">可选。上传真人照片或手绘稿，生成资产图时作为外观依据。</p>
          )}
        </Section>
      )}

      {asset.type === "product" && (
        <Section
          title="商品原图"
          aside={
            <Button variant="ghost" size="xs" onClick={() => stub("上传商品原图")}>
              <ImageUp /> 上传
            </Button>
          }
        >
          {asset.referenceUrls.length ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(72px,1fr))] gap-2">
              {asset.referenceUrls.map((u) => (
                <img key={u} src={u} alt="" className="aspect-square w-full rounded-md border border-border object-cover" />
              ))}
            </div>
          ) : (
            <p className="text-[12px] text-muted-foreground">至少上传一张商品原图，作为外观真实性依据。</p>
          )}
        </Section>
      )}
    </div>
  );

  const fields = (
    <div className="flex flex-col gap-5">
      <Section
        title="描述"
        aside={
          <Button variant="link" size="xs" className="px-0" onClick={() => stub("预览提示词")}>
            预览提示词
          </Button>
        }
      >
        <textarea
          id={descId}
          aria-label="描述"
          className={AUTO_TEXTAREA}
          value={draft.description}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="外貌、服装、气质……"
        />
      </Section>

      {asset.type !== "product" && (
        <Section title="别名">
          <div className="flex flex-wrap items-center gap-1.5">
            {asset.aliases.map((a) => (
              <span key={a} className="inline-flex h-6 items-center gap-1 rounded-md bg-muted px-2 text-[12px] text-foreground">
                {a}
                <button type="button" aria-label={`移除别名 ${a}`} onClick={() => stub("移除别名")} className="text-muted-foreground hover:text-foreground">
                  <X className="size-3" />
                </button>
              </span>
            ))}
            <Button variant="ghost" size="xs" onClick={() => stub("添加别名")}>
              <Plus /> 添加
            </Button>
          </div>
          {asset.aliases.length === 0 && (
            <p className="text-[12px] text-muted-foreground">源文里的其他叫法。只用于 AI 认出同一个{TYPE_LABEL[asset.type]}，不能在脚本中引用。</p>
          )}
        </Section>
      )}

      {asset.type === "character" && (
        <Section title="声音">
          <textarea
            aria-label="声音风格"
            rows={1}
            className={cn(AUTO_TEXTAREA, "min-h-0")}
            value={draft.voiceStyle}
            onChange={(e) => set({ voiceStyle: e.target.value })}
            placeholder="如：语调沉稳，语速偏慢"
          />
          <AudioRow asset={asset} />
        </Section>
      )}

      {asset.type === "product" && (
        <>
          <Section title="品牌">
            <input aria-label="品牌" className={FIELD} value={draft.brand} onChange={(e) => set({ brand: e.target.value })} />
          </Section>
          <Section title="卖点">
            <textarea
              aria-label="卖点，每行一条"
              className={AUTO_TEXTAREA}
              value={draft.sellingPoints}
              onChange={(e) => set({ sellingPoints: e.target.value })}
              placeholder="每行一条"
            />
          </Section>
        </>
      )}

      {asset.type === "character" && <DerivativeSection asset={asset} />}
    </div>
  );

  return (
    <div className={cn("@container flex min-h-0 flex-col", className)}>
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
        {headerStart}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <h2 className="truncate text-[16px] font-semibold text-foreground" title={asset.name}>
            {asset.name}
          </h2>
          <Button variant="ghost" size="icon-xs" aria-label="改名" onClick={() => stub("改名")}>
            <Pencil />
          </Button>
          <span className="shrink-0 text-[12px] text-muted-foreground">{TYPE_LABEL[asset.type]}</span>
        </div>
        <div className="flex shrink-0 items-center">
          {pinned && (
            <Button variant="ghost" size="icon-sm" aria-label="上传资产图" onClick={() => stub("上传资产图")}>
              <Upload />
            </Button>
          )}
          <AssetMoreMenu asset={asset} includePinned={false} onView={onView} />
          {headerEnd}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="grid gap-6 p-4 @3xl:grid-cols-[minmax(320px,5fr)_7fr] @3xl:gap-8 @3xl:p-6">
          <div className="@3xl:sticky @3xl:top-6 @3xl:self-start">{media}</div>
          {fields}
        </div>
      </div>

      {dirty && (
        <div className="flex shrink-0 items-center gap-2 border-t border-border bg-muted/60 px-4 py-2">
          <span aria-hidden className="size-1.5 rounded-full bg-primary" />
          <span className="flex-1 text-[13px] text-foreground">有未保存的修改</span>
          <Button variant="ghost" size="sm" onClick={() => onDraftChange(draftOf(asset))}>
            放弃
          </Button>
          <Button size="sm" onClick={() => stub("保存")}>
            保存
          </Button>
        </div>
      )}
    </div>
  );
}

function AudioRow({ asset }: { asset: ProtoAsset }) {
  const [playing, setPlaying] = useState(false);
  if (!asset.audioUrl) {
    return (
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => stub("上传参考音频")}>
          <Upload /> 上传参考音频
        </Button>
        <Button variant="ghost" size="sm" onClick={() => stub("生成声音样本")}>
          <Sparkles /> 生成声音样本
        </Button>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 rounded-lg border border-input px-2 py-1.5">
      <Button variant="secondary" size="icon-sm" aria-label={playing ? "暂停" : "播放"} onClick={() => setPlaying(!playing)}>
        {playing ? <Pause /> : <Play />}
      </Button>
      <div className="h-1 flex-1 rounded-full bg-border" />
      <Button variant="ghost" size="icon-xs" aria-label="删除参考音频" onClick={() => stub("删除参考音频")}>
        <X />
      </Button>
    </div>
  );
}

function DerivativeSection({ asset }: { asset: ProtoAsset }) {
  return (
    <Section
      title={`衍生${asset.derivatives.length ? `（${asset.derivatives.length}）` : ""}`}
      aside={
        <Button variant="ghost" size="xs" onClick={() => stub("新增衍生")}>
          <Plus /> 新增衍生
        </Button>
      }
    >
      {asset.derivatives.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">
          衍生是这个角色的另一套持续外观（换装、变身、年龄跨越），只写相对本体的变化。
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
          {asset.derivatives.map((d) => (
            <DerivativeRow key={d.name} owner={asset} d={d} />
          ))}
        </ul>
      )}
    </Section>
  );
}

function DerivativeRow({ owner, d }: { owner: ProtoAsset; d: ProtoDerivative }) {
  const token = `@[${owner.name}/${d.name}]`;
  const [text, setText] = useState(d.description);
  const dirty = text !== d.description;
  return (
    <li className="flex gap-3 p-3">
      <div className="w-28 shrink-0">
        <SheetImage asset={{ sheetUrl: d.sheetUrl, name: d.name, type: "character" }} className="rounded-md" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-[13px] font-medium text-foreground">{d.name}</span>
          {d.sample && <span className="rounded bg-muted px-1 text-[11px] text-muted-foreground">原型样例</span>}
          <button
            type="button"
            onClick={() => stub("复制引用记号")}
            className="ml-auto inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title="复制引用记号"
          >
            {token}
            <Copy className="size-3" />
          </button>
        </div>
        <textarea
          aria-label={`「${d.name}」的外观变化`}
          className={cn(AUTO_TEXTAREA, "min-h-0 py-1.5 text-[12.5px]")}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="flex items-center gap-1.5">
          {!d.referenced && <span className="text-[12px] text-muted-foreground">脚本中尚未引用</span>}
          <div className="ml-auto flex items-center gap-1">
            {dirty && (
              <Button variant="ghost" size="xs" onClick={() => setText(d.description)}>
                放弃
              </Button>
            )}
            <Button variant={dirty ? "default" : "outline"} size="xs" onClick={() => stub(dirty ? "保存衍生" : "生成衍生图")}>
              {dirty ? "保存" : d.sheetUrl ? "重新生成" : "生成"}
            </Button>
            <Button variant="ghost" size="icon-xs" aria-label={`删除衍生 ${d.name}`} onClick={() => stub("删除衍生")}>
              <X />
            </Button>
          </div>
        </div>
      </div>
    </li>
  );
}
