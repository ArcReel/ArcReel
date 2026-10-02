// PROTOTYPE — #2972「自定义供应商的模型列表：卡片还是可行内展开编辑的表格」。
// cards：每个模型一张卡，首行「启用 / 模型 ID / 默认 / 更多」，其余属性走共用标签列；
// table：一行一个模型，点行在下方展开编辑区；
// inspector：表格只负责浏览与选择，编辑在右侧检查器栏（详情栏够宽时常驻，否则用 Sheet）。
// 三者共用同一份字段编辑器 ModelFields，差别只在「列表怎么摆、编辑区在哪」。

import { Fragment, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronRight, Link2, MoreHorizontal, Plus, Search, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { CONTROL, FieldRow, Trunc } from "./bits";
import { MEDIA_LABEL, useEndpointLabel, useEndpoints } from "./data";
import { EndpointRef, MarketLink } from "./EndpointRef";
import type { ProtoModel } from "./samples";
import { useAxis } from "./store";

const BUCKET_LABEL: Record<string, string> = {
  default_video_backend: "默认视频模型",
  default_video_backend_i2v: "图生视频",
  default_image_backend: "默认图片模型",
};

const PRICE_UNIT: Record<string, [string, string?]> = {
  text: ["每百万输入 token", "每百万输出 token"],
  image: ["每张"],
  video: ["每秒"],
  audio: ["每百万字符"],
};

export interface ModelListProps {
  providerId: number;
  models: ProtoModel[];
  comfyui: boolean;
  onChange: (models: ProtoModel[]) => void;
  /** inspector 模式：详情栏右侧检查器的挂载点（详情栏够宽时才有） */
  asideTarget: HTMLElement | null;
  wide: boolean;
}

function useMedia() {
  const endpoints = useEndpoints();
  return (key: string) => endpoints.find((e) => e.key === key)?.media_type ?? "";
}

// ---------------------------------------------------------------------------
// 共用字段编辑器
// ---------------------------------------------------------------------------

function ModelFields({
  model,
  update,
  providerId,
  comfyui,
  withId,
}: {
  model: ProtoModel;
  update: (patch: Partial<ProtoModel>) => void;
  providerId: number;
  comfyui: boolean;
  withId: boolean;
}) {
  const endpoints = useEndpoints();
  const label = useEndpointLabel();
  const media = endpoints.find((e) => e.key === model.endpoint)?.media_type ?? "";
  const unit = PRICE_UNIT[media] ?? ["每次"];
  const id = `mf-${model.key}`;
  const options = endpoints.filter((e) => (e.kind === "comfyui") === comfyui);
  return (
    <div className="space-y-3">
      {withId && (
        <FieldRow label="模型 ID" labelFor={`${id}-id`}>
          <input
            id={`${id}-id`}
            value={model.model_id}
            onChange={(e) => update({ model_id: e.target.value })}
            placeholder="例如 gpt-5.1"
            className={cn(CONTROL, "w-full font-mono")}
          />
        </FieldRow>
      )}
      <FieldRow label="调用端点" labelFor={`${id}-ep`}>
        <div className="min-w-0">
          <select
            id={`${id}-ep`}
            value={model.endpoint}
            onChange={(e) => update({ endpoint: e.target.value, is_default: false })}
            className={cn(CONTROL, "w-full")}
          >
            {(["text", "image", "video", "audio"] as const).map((mt) => {
              const group = options.filter((e) => e.media_type === mt);
              return group.length === 0 ? null : (
                <optgroup key={mt} label={MEDIA_LABEL[mt]}>
                  {group.map((e) => (
                    <option key={e.key} value={e.key}>
                      {label(e)}　{e.request_path_template}
                    </option>
                  ))}
                </optgroup>
              );
            })}
          </select>
          <div className="mt-1 flex flex-wrap items-center gap-x-3">
            <EndpointRef endpointKey={model.endpoint} fromProvider={providerId} />
            <MarketLink />
          </div>
        </div>
      </FieldRow>
      <FieldRow label="价格">
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="币种"
            value={model.currency}
            onChange={(e) => update({ currency: e.target.value as "USD" | "CNY" })}
            className={cn(CONTROL, "w-16")}
          >
            <option value="CNY">¥</option>
            <option value="USD">$</option>
          </select>
          <input
            aria-label={`价格（${unit[0]}）`}
            inputMode="decimal"
            value={model.price_input}
            onChange={(e) => update({ price_input: e.target.value })}
            placeholder="0.00"
            className={cn(CONTROL, "w-24 tabular-nums")}
          />
          <span className="text-[13px] text-text-3">{unit[0]}</span>
          {unit[1] && (
            <>
              <input
                aria-label={`价格（${unit[1]}）`}
                inputMode="decimal"
                value={model.price_output}
                onChange={(e) => update({ price_output: e.target.value })}
                placeholder="0.00"
                className={cn(CONTROL, "ml-2 w-24 tabular-nums")}
              />
              <span className="text-[13px] text-text-3">{unit[1]}</span>
            </>
          )}
        </div>
      </FieldRow>
      {media === "text" && (
        <FieldRow label="最大输出" labelFor={`${id}-max`} hint="分集规划按它决定每批规划几集。">
          <input
            id={`${id}-max`}
            inputMode="numeric"
            value={model.max_output_tokens}
            onChange={(e) => update({ max_output_tokens: e.target.value })}
            placeholder="token 数"
            className={cn(CONTROL, "w-40 tabular-nums")}
          />
        </FieldRow>
      )}
      {(media === "image" || media === "video") && (
        <FieldRow label="分辨率" labelFor={`${id}-res`}>
          <input
            id={`${id}-res`}
            value={model.resolution}
            onChange={(e) => update({ resolution: e.target.value })}
            placeholder={media === "video" ? "例如 1080p" : "例如 1024x1024"}
            className={cn(CONTROL, "w-40")}
          />
        </FieldRow>
      )}
      {media === "video" && (
        <FieldRow label="支持时长" labelFor={`${id}-dur`} hint="单位为秒，多个值用逗号分隔。">
          <input
            id={`${id}-dur`}
            value={model.durations}
            onChange={(e) => update({ durations: e.target.value })}
            placeholder="例如 5, 10"
            className={cn(CONTROL, "w-40")}
          />
        </FieldRow>
      )}
      {media === "video" && !comfyui && (
        <FieldRow label="尾帧">
          <ToggleGroup
            value={[model.last_frame]}
            onValueChange={(v: string[]) => v[0] && update({ last_frame: v[0] as ProtoModel["last_frame"] })}
            variant="outline"
            size="sm"
          >
            <ToggleGroupItem value="auto">跟随判定</ToggleGroupItem>
            <ToggleGroupItem value="on">支持</ToggleGroupItem>
            <ToggleGroupItem value="off">不支持</ToggleGroupItem>
          </ToggleGroup>
        </FieldRow>
      )}
      {model.global_refs.length > 0 && (
        <p className="flex items-center gap-1.5 pl-[108px] text-[12px] text-text-3">
          <Link2 className="size-3 shrink-0" aria-hidden />
          被全局默认引用：{model.global_refs.map((k) => BUCKET_LABEL[k] ?? k).join("、")}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 工具栏：标题、搜索、按类型筛选、发现与添加
// ---------------------------------------------------------------------------

type MediaFilter = "all" | "text" | "image" | "video" | "audio";

function Toolbar({
  count,
  query,
  setQuery,
  filter,
  setFilter,
  onAdd,
  comfyui,
}: {
  count: number;
  query: string;
  setQuery: (q: string) => void;
  filter: MediaFilter;
  setFilter: (f: MediaFilter) => void;
  onAdd: () => void;
  comfyui: boolean;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <h3 className="mr-2 text-[15px] font-medium">
        模型 <span className="text-text-3 tabular-nums">{count}</span>
      </h3>
      {count > 5 && (
        <>
          <label className="relative">
            <span className="sr-only">搜索模型</span>
            <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-text-3" aria-hidden />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索模型 ID"
              className={cn(CONTROL, "w-48 pl-7")}
            />
          </label>
          <ToggleGroup
            value={[filter]}
            onValueChange={(v: string[]) => v[0] && setFilter(v[0] as MediaFilter)}
            variant="outline"
            size="sm"
          >
            {(["all", "text", "image", "video", "audio"] as const).map((f) => (
              <ToggleGroupItem key={f} value={f}>
                {f === "all" ? "全部" : MEDIA_LABEL[f]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </>
      )}
      <div className="ml-auto flex items-center gap-2">
        {!comfyui && <Button variant="outline">获取模型列表</Button>}
        <Button variant="outline" onClick={onAdd}>
          <Plus />
          添加模型
        </Button>
      </div>
    </div>
  );
}

function DefaultToggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      className={cn(
        "h-6 shrink-0 rounded-md border px-2 text-[12px] transition-colors",
        on ? "border-primary/40 bg-primary/15 text-text" : "border-border text-text-3 hover:text-text",
      )}
    >
      默认
    </button>
  );
}

function priceSummary(m: ProtoModel, media: string) {
  if (!m.price_input) return "—";
  const sym = m.currency === "USD" ? "$" : "¥";
  const unit = media === "text" ? "/M" : media === "video" ? "/秒" : media === "image" ? "/张" : "";
  return m.price_output ? `${sym}${m.price_input} / ${sym}${m.price_output}${unit}` : `${sym}${m.price_input}${unit}`;
}

// ---------------------------------------------------------------------------
// 列表本体
// ---------------------------------------------------------------------------

export function ModelList(props: ModelListProps) {
  const mode = useAxis("models");
  const mediaOf = useMedia();
  const endpoints = useEndpoints();
  const label = useEndpointLabel();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<MediaFilter>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const { models, onChange } = props;

  const update = (key: string, patch: Partial<ProtoModel>) => {
    let next = models.map((m) => (m.key === key ? { ...m, ...patch } : m));
    if (patch.is_default) {
      const media = mediaOf(next.find((m) => m.key === key)?.endpoint ?? "");
      next = next.map((m) => (m.key !== key && mediaOf(m.endpoint) === media ? { ...m, is_default: false } : m));
    }
    onChange(next);
  };
  const remove = (key: string) => onChange(models.filter((m) => m.key !== key));
  const add = () => {
    const key = `new${Date.now()}`;
    const endpoint = props.comfyui ? (endpoints.find((e) => e.kind === "comfyui")?.key ?? "") : "openai-chat";
    onChange([
      ...models,
      { key, model_id: "", endpoint, is_enabled: true, is_default: false, currency: "CNY", price_input: "", price_output: "", resolution: "", durations: "", last_frame: "auto", max_output_tokens: "", global_refs: [] },
    ]);
    setExpanded((s) => new Set(s).add(key));
    setSelected(key);
  };

  const visible = models.filter(
    (m) => m.model_id.toLowerCase().includes(query.trim().toLowerCase()) && (filter === "all" || mediaOf(m.endpoint) === filter),
  );

  const toolbar = (
    <Toolbar count={models.length} query={query} setQuery={setQuery} filter={filter} setFilter={setFilter} onAdd={add} comfyui={props.comfyui} />
  );

  if (mode === "cards") {
    return (
      <div className="max-w-[760px]">
        {toolbar}
        <div className="space-y-2">
          {visible.map((m) => (
            <div key={m.key} className={cn("rounded-[10px] border border-border bg-card/60 p-3", !m.is_enabled && "opacity-70")}>
              <div className="flex items-center gap-2">
                <Checkbox
                  checked={m.is_enabled}
                  onCheckedChange={(v) => update(m.key, { is_enabled: v === true })}
                  aria-label="启用"
                />
                <input
                  value={m.model_id}
                  onChange={(e) => update(m.key, { model_id: e.target.value })}
                  aria-label="模型 ID"
                  placeholder="模型 ID"
                  className={cn(CONTROL, "min-w-0 flex-1 font-mono", !m.model_id && "border-destructive/60")}
                />
                <Badge variant="outline" className="shrink-0">
                  {MEDIA_LABEL[mediaOf(m.endpoint)] ?? "未知"}
                </Badge>
                <DefaultToggle on={m.is_default} onToggle={() => update(m.key, { is_default: !m.is_default })} />
                <RowMenu onDelete={() => remove(m.key)} />
              </div>
              <div className="mt-3 border-t border-border/60 pt-3">
                <ModelFields model={m} update={(p) => update(m.key, p)} providerId={props.providerId} comfyui={props.comfyui} withId={false} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const columns = (
    <TableRow className="hover:bg-transparent">
      <TableHead className="w-10">
        <span className="sr-only">启用</span>
      </TableHead>
      <TableHead>模型 ID</TableHead>
      <TableHead className="w-16">类型</TableHead>
      <TableHead>调用端点</TableHead>
      <TableHead className="w-40">价格</TableHead>
      <TableHead className="w-16">默认</TableHead>
      <TableHead className="w-10">
        <span className="sr-only">展开</span>
      </TableHead>
    </TableRow>
  );

  const row = (m: ProtoModel, opts: { open: boolean; onClick: () => void; active?: boolean }) => {
    const ep = endpoints.find((e) => e.key === m.endpoint);
    const media = mediaOf(m.endpoint);
    return (
      <TableRow
        key={m.key}
        data-state={opts.active ? "selected" : undefined}
        onClick={opts.onClick}
        className={cn("cursor-pointer", !m.is_enabled && "text-text-3")}
      >
        <TableCell onClick={(e) => e.stopPropagation()}>
          <Checkbox checked={m.is_enabled} onCheckedChange={(v) => update(m.key, { is_enabled: v === true })} aria-label={`启用 ${m.model_id}`} />
        </TableCell>
        <TableCell className="max-w-0">
          {m.model_id ? <Trunc text={m.model_id} mono className="text-[13px]" /> : <span className="text-destructive">未填写模型 ID</span>}
        </TableCell>
        <TableCell>{MEDIA_LABEL[media] ?? "—"}</TableCell>
        <TableCell className="max-w-0">
          <Trunc text={label(ep)} className="text-text-2" />
        </TableCell>
        <TableCell className="tabular-nums text-text-2">{priceSummary(m, media)}</TableCell>
        <TableCell>
          <DefaultToggle on={m.is_default} onToggle={() => update(m.key, { is_default: !m.is_default })} />
        </TableCell>
        <TableCell>
          {mode === "table" ? (
            opts.open ? (
              <ChevronDown className="size-4 text-text-3" aria-label="收起" />
            ) : (
              <ChevronRight className="size-4 text-text-3" aria-label="展开" />
            )
          ) : (
            <ChevronRight className={cn("size-4", opts.active ? "text-primary" : "text-text-3")} aria-hidden />
          )}
        </TableCell>
      </TableRow>
    );
  };

  const table = (body: ReactNode) => (
    <div className="overflow-x-auto rounded-[10px] border border-border">
      <Table className="table-fixed">
        <TableHeader className="bg-muted/40">{columns}</TableHeader>
        <TableBody>{body}</TableBody>
      </Table>
    </div>
  );

  if (mode === "table") {
    return (
      <div className="max-w-[1200px]">
        {toolbar}
        {table(
          visible.map((m) => {
            const open = expanded.has(m.key);
            return (
              <Fragment key={m.key}>
                {row(m, {
                  open,
                  onClick: () =>
                    setExpanded((s) => {
                      const n = new Set(s);
                      if (n.has(m.key)) n.delete(m.key);
                      else n.add(m.key);
                      return n;
                    }),
                })}
                {open && (
                  <TableRow className="bg-muted/20 hover:bg-muted/20">
                    <TableCell colSpan={7} className="px-4 py-4 whitespace-normal">
                      <div className="max-w-[720px]">
                        <ModelFields model={m} update={(p) => update(m.key, p)} providerId={props.providerId} comfyui={props.comfyui} withId />
                        <div className="mt-4 flex justify-end">
                          <Button variant="destructive" size="sm" onClick={() => remove(m.key)}>
                            <Trash2 />
                            删除模型
                          </Button>
                        </div>
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          }),
        )}
      </div>
    );
  }

  // inspector
  const current = models.find((m) => m.key === selected) ?? null;
  const editor = current && (
    <>
      <ModelFields model={current} update={(p) => update(current.key, p)} providerId={props.providerId} comfyui={props.comfyui} withId />
      <div className="mt-5 flex justify-end">
        <Button
          variant="destructive"
          size="sm"
          onClick={() => {
            remove(current.key);
            setSelected(null);
          }}
        >
          <Trash2 />
          删除模型
        </Button>
      </div>
    </>
  );
  const inspector =
    props.wide ? (
      props.asideTarget &&
      createPortal(
      <aside data-zone="inspector" className="flex h-full w-[420px] shrink-0 flex-col border-l border-border bg-card/40">
        <div className="shrink-0 border-b border-border px-5 py-3 text-[14px] font-medium">
          {current ? <Trunc text={current.model_id || "新模型"} mono /> : "模型属性"}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {editor ?? <p className="text-[13px] text-text-3">在左侧表格中选择一个模型，在这里编辑它的端点、价格与能力。</p>}
        </div>
      </aside>,
      props.asideTarget,
      )
    ) : (
      <Sheet open={current !== null} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent className="gap-0 p-0 data-[side=right]:w-[min(480px,90vw)] data-[side=right]:sm:max-w-none">
          <SheetHeader className="border-b border-border px-5 py-4">
            <SheetTitle className="font-mono">{current?.model_id || "新模型"}</SheetTitle>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{editor}</div>
        </SheetContent>
      </Sheet>
    );
  return (
    <div>
      {inspector}
      {toolbar}
      {table(visible.map((m) => row(m, { open: false, active: m.key === selected, onClick: () => setSelected(m.key) })))}
      {!props.wide && <p className="mt-2 text-[12px] text-text-3">详情栏不足 1100px，检查器改为 Sheet。</p>}
    </div>
  );
}

function RowMenu({ onDelete }: { onDelete: () => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="更多操作" />}>
        <MoreHorizontal />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem variant="destructive" onClick={onDelete}>
          <Trash2 />
          删除模型
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
