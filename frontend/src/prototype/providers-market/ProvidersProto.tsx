// PROTOTYPE — #2972 供应商分区。全出血档：二级栏 + 详情栏各自滚动，保存栏在详情栏底部。
// 变体轴：rail（二级栏随宽度的取法）、models（模型列表形态）、endpoint（管理端点的方式）、cred（密钥增改走哪条保存路径）。

import { useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { useSearch } from "wouter";
import { Check, ChevronDown, ChevronRight, Eye, EyeOff, MoreHorizontal, Pencil, Plus, Trash2, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { ProviderCredential, ProviderInfo } from "@/types";
import { CONTROL, DetailPane, FieldRow, LeaveDialog, SampleTag, SaveBar, StatusDot, Trunc, guarded, protoToast, registerGuard, useWidth } from "./bits";
import { MEDIA_LABEL, commitCustomProvider, useCatalog, useOnce } from "./data";
import { EndpointSheetHost, useGoSection } from "./EndpointRef";
import { ModelList } from "./ModelList";
import { SAMPLE_CREDENTIALS, type ProtoCustomProvider } from "./samples";
import { useAxis, useProto } from "./store";

function useStandardTier() {
  return useSyncExternalStore(
    (l) => {
      const mq = window.matchMedia("(min-width: 1280px)");
      mq.addEventListener("change", l);
      return () => mq.removeEventListener("change", l);
    },
    () => window.matchMedia("(min-width: 1280px)").matches,
  );
}

type Sel = { kind: "preset"; id: string } | { kind: "custom"; id: number } | { kind: "new" };

export function ProvidersProto() {
  const { presets, custom, loaded } = useCatalog();
  const search = useSearch();
  const goSection = useGoSection();
  const params = new URLSearchParams(search);
  const sel: Sel | null = params.get("custom")
    ? params.get("custom") === "new"
      ? { kind: "new" }
      : { kind: "custom", id: Number(params.get("custom")) }
    : params.get("provider")
      ? { kind: "preset", id: params.get("provider")! }
      : presets[0]
        ? { kind: "preset", id: presets[0].id }
        : null;
  const select = (s: Sel) =>
    guarded(() =>
      goSection("providers", s.kind === "preset" ? { provider: s.id } : { custom: s.kind === "new" ? "new" : String(s.id) }),
    );

  if (!loaded) return <p className="p-6 text-[13px] text-text-3">正在加载供应商…</p>;

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1">
      <ProviderRail presets={presets} custom={custom} sel={sel} onSelect={select} />
      {sel?.kind === "preset" && <PresetDetail key={sel.id} provider={presets.find((p) => p.id === sel.id)} />}
      {sel?.kind === "custom" &&
        (() => {
          const p = custom.find((c) => c.id === sel.id);
          return p ? <CustomDetail key={p.id} provider={p} focusModel={params.get("model")} /> : null;
        })()}
      {sel?.kind === "new" && (
        <CustomDetail
          key="new"
          provider={{ id: 0, display_name: "", discovery_format: "openai", base_url: "", api_key_masked: "", models: [], workers: { image: "", video: "", audio: "" }, sample: false }}
          isNew
        />
      )}
      <EndpointSheetHost />
      <LeaveDialog />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 二级栏
// ---------------------------------------------------------------------------

function ProviderRail({
  presets,
  custom,
  sel,
  onSelect,
}: {
  presets: ProviderInfo[];
  custom: ProtoCustomProvider[];
  sel: Sel | null;
  onSelect: (s: Sel) => void;
}) {
  const mode = useAxis("rail");
  const standard = useStandardTier();
  const icon = mode === "adaptive" && !standard;
  const twoLine = mode === "adaptive" && standard;

  const item = (key: string, active: boolean, glyph: ReactNode, name: string, meta: string, tone: "good" | "muted", onClick: () => void, sample = false) => {
    const button = (
      <button
        type="button"
        aria-current={active ? "page" : undefined}
        aria-label={icon ? name : undefined}
        onClick={onClick}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-md text-left text-[13px] transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
          icon ? "size-10 justify-center" : twoLine ? "px-2.5 py-2" : "h-8 px-2.5",
          active ? "bg-primary/14 text-text" : "text-text-2 hover:bg-accent hover:text-text",
        )}
      >
        <span className="relative shrink-0">
          {glyph}
          {icon && meta && <span className="absolute -right-0.5 -bottom-0.5"><StatusDot tone={tone} label={meta} /></span>}
        </span>
        {!icon && (
          <>
            <span className="min-w-0 flex-1">
              <Trunc text={name} />
              {twoLine && <span className="block truncate text-[12px] text-text-3">{meta}</span>}
            </span>
            {sample && <SampleTag />}
            {!twoLine && meta && <StatusDot tone={tone} label={meta} />}
          </>
        )}
      </button>
    );
    return icon ? (
      <Tooltip key={key}>
        <TooltipTrigger render={<div className="mb-px" />}>{button}</TooltipTrigger>
        <TooltipContent side="right">{name}</TooltipContent>
      </Tooltip>
    ) : (
      <div key={key} className="mb-px">
        {button}
      </div>
    );
  };

  const letter = (name: string) => (
    <span className="inline-flex size-4 items-center justify-center rounded-sm border border-border bg-muted text-[11px] font-medium text-text-2">
      {Array.from(name)[0] ?? "?"}
    </span>
  );

  const order = useAxis("railOrder");
  const { samples } = useProto();
  const configured = (p: ProviderInfo) => p.status === "ready" || (samples && (SAMPLE_CREDENTIALS[p.id]?.length ?? 0) > 0);
  const [showIdle, setShowIdle] = useState(false);
  const selectedIdle = sel?.kind === "preset" && !configured(presets.find((p) => p.id === sel.id) ?? presets[0]);

  const presetItem = (p: ProviderInfo, compact = false) => {
    const on = configured(p);
    const n = SAMPLE_CREDENTIALS[p.id]?.length ?? 0;
    const meta = on ? (n > 0 ? `已配置 ${n} 个密钥` : "已连接") : "未配置";
    const node = item(
      p.id,
      sel?.kind === "preset" && sel.id === p.id,
      <ProviderIcon providerId={p.id} className="size-4" />,
      p.display_name,
      compact ? "" : meta,
      on ? "good" : "muted",
      () => onSelect({ kind: "preset", id: p.id }),
    );
    return node;
  };
  const customItem = (p: ProtoCustomProvider) =>
    item(
      String(p.id),
      sel?.kind === "custom" && sel.id === p.id,
      letter(p.display_name),
      p.display_name,
      `${p.models.length} 个模型，${p.models.filter((m) => m.is_enabled).length} 个启用`,
      p.base_url ? "good" : "muted",
      () => onSelect({ kind: "custom", id: p.id }),
      p.sample,
    );
  const addItem = item("new", sel?.kind === "new", <Plus className="size-4" />, "添加自定义供应商", "", "muted", () => onSelect({ kind: "new" }));
  const heading = (text: ReactNode) => !icon && <div className="mb-1 flex items-center px-2.5 text-[12px] text-text-3">{text}</div>;
  const divider = <div className={cn("my-3 border-t border-border", icon && "mx-1")} />;

  let body: ReactNode;
  if (order === "status") {
    const active = presets.filter(configured);
    const idle = presets.filter((p) => !configured(p));
    const idleOpen = showIdle || selectedIdle || icon || active.length + custom.length === 0;
    body = (
      <>
        {!icon && (
          <Button variant="outline" className="mb-4 w-full justify-start" onClick={() => onSelect({ kind: "new" })}>
            <Plus />
            添加自定义供应商
          </Button>
        )}
        {heading("已配置")}
        {active.map((p) => presetItem(p))}
        {custom.map(customItem)}
        {active.length + custom.length === 0 && !icon && <p className="px-2.5 py-1 text-[12px] text-text-3">还没有配置任何供应商。</p>}
        {icon && addItem}
        {divider}
        {!icon && (
          <button
            type="button"
            aria-expanded={idleOpen}
            onClick={() => setShowIdle(!showIdle)}
            disabled={selectedIdle}
            className="mb-1 flex h-7 w-full items-center gap-1 rounded-md px-2.5 text-left text-[12px] text-text-3 hover:text-text disabled:hover:text-text-3"
          >
            {idleOpen ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
            未配置的预置供应商
            <span className="ml-auto tabular-nums">{idle.length}</span>
          </button>
        )}
        {idleOpen && idle.map((p) => presetItem(p, true))}
      </>
    );
  } else {
    const presetGroup = (
      <>
        {heading("预置供应商")}
        {presets.map((p) => presetItem(p))}
      </>
    );
    const customGroup = (
      <>
        {heading("自定义供应商")}
        {custom.map(customItem)}
        {addItem}
      </>
    );
    body =
      order === "customFirst" ? (
        <>
          {customGroup}
          {divider}
          {presetGroup}
        </>
      ) : (
        <>
          {presetGroup}
          {divider}
          {customGroup}
        </>
      );
  }

  return (
    <nav
      aria-label="供应商列表"
      data-zone="rail"
      className={cn("h-full shrink-0 overflow-y-auto border-r border-border bg-sidebar/50", icon ? "w-14 px-2 py-3" : mode === "adaptive" ? "w-[264px] px-3 py-4" : "w-60 px-3 py-4")}
    >
      {body}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// 预置供应商
// ---------------------------------------------------------------------------

interface CredDraft extends ProviderCredential {
  state?: "new" | "edited" | "deleted";
  key_input?: string;
}

function PresetDetail({ provider }: { provider: ProviderInfo | undefined }) {
  const credMode = useAxis("cred");
  const config = useOnce(() => (provider ? API.getProviderConfig(provider.id) : Promise.resolve(null)), [provider?.id]);
  const realCreds = useOnce(() => (provider ? API.listCredentials(provider.id) : Promise.resolve({ credentials: [] })), [provider?.id]);
  const baseCreds = useMemo<CredDraft[]>(
    () => (realCreds?.credentials.length ? realCreds.credentials : (SAMPLE_CREDENTIALS[provider?.id ?? ""] ?? [])),
    [realCreds, provider?.id],
  );
  const [creds, setCreds] = useState<CredDraft[] | null>(null);
  const list = creds ?? baseCreds;
  const [fields, setFields] = useState<Record<string, string>>({});
  const [dialog, setDialog] = useState<{ cred: CredDraft | null } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<CredDraft | null>(null);
  const [editing, setEditing] = useState<number | null>(null);

  const credDirty = credMode === "draft" && list.some((c) => c.state);
  const dirty = Object.keys(fields).length > 0 || credDirty;
  const save = () => {
    setFields({});
    setCreds(list.filter((c) => c.state !== "deleted").map((c) => ({ ...c, state: undefined })));
    setEditing(null);
    protoToast("已保存供应商配置");
  };
  const discard = () => {
    setFields({});
    setCreds(null);
    setEditing(null);
  };
  useEffect(() => {
    registerGuard({ dirty, save, discard, label: provider?.display_name ?? "" });
    return () => registerGuard(null);
  });

  if (!provider) return null;
  const sampleCreds = !realCreds?.credentials.length && (SAMPLE_CREDENTIALS[provider.id]?.length ?? 0) > 0;

  const activate = (c: CredDraft) => {
    setCreds(list.map((x) => ({ ...x, is_active: x.id === c.id, state: credMode === "draft" && x.id === c.id && !x.state ? "edited" : x.state })));
    if (credMode === "dialog") protoToast(`已切换到「${c.name}」`);
  };

  return (
    <DetailPane
      header={
        <div className="flex items-start gap-3">
          <ProviderIcon providerId={provider.id} className="mt-0.5 size-7 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-[18px] font-medium">{provider.display_name}</h2>
              <Badge variant={provider.status === "ready" || list.length > 0 ? "secondary" : "outline"}>{provider.status === "ready" || list.length > 0 ? "已配置" : "未配置"}</Badge>
            </div>
            <p className="mt-1 max-w-[40em] text-[13px] text-text-3">{provider.description}</p>
          </div>
        </div>
      }
      footer={
        <SaveBar
          dirty={dirty}
          onSave={save}
          onDiscard={discard}
          scope={credMode === "dialog" ? "保存栏只管高级配置；密钥的增改即时生效" : "密钥与高级配置一起保存"}
        />
      }
    >
      <div className="max-w-[760px] space-y-8 px-6 py-6">
        <div className="flex flex-wrap gap-1.5">
          {provider.media_types.map((m) => (
            <Badge key={m} variant="outline">
              {MEDIA_LABEL[m] ?? m}
            </Badge>
          ))}
        </div>

        <section>
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-[15px] font-medium">密钥</h3>
            {sampleCreds && <SampleTag />}
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={() => {
                if (credMode === "dialog") setDialog({ cred: null });
                else {
                  const id = -Date.now();
                  setCreds([...list, { id, provider: provider.id, name: "", api_key_masked: null, credentials_filename: null, base_url: null, is_active: list.length === 0, created_at: "", state: "new" }]);
                  setEditing(id);
                }
              }}
            >
              <Plus />
              添加密钥
            </Button>
          </div>
          {list.length === 0 ? (
            <div className="rounded-[10px] border border-dashed border-border px-4 py-6 text-center text-[13px] text-text-3">
              还没有密钥。添加一个密钥后，这个供应商的模型才能使用。
            </div>
          ) : (
            <ul className="divide-y divide-border rounded-[10px] border border-border">
              {list.map((c) => (
                <li key={c.id} className={cn(c.state === "deleted" && "opacity-50")}>
                  <div className="flex items-center gap-3 px-4 py-2.5">
                    <button
                      type="button"
                      onClick={() => activate(c)}
                      aria-pressed={c.is_active}
                      aria-label={c.is_active ? "当前使用中" : `改用「${c.name}」`}
                      className={cn(
                        "flex size-4 shrink-0 items-center justify-center rounded-full border",
                        c.is_active ? "border-primary bg-primary text-primary-foreground" : "border-input hover:border-text-3",
                      )}
                    >
                      {c.is_active && <Check className="size-3" />}
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 items-center gap-2">
                        <Trunc text={c.name || "未命名密钥"} className={cn("text-[13px]", c.state === "deleted" && "line-through")} />
                        {c.is_active && <Badge variant="secondary">使用中</Badge>}
                        {c.state && <Badge variant="outline" className="border-warn/50 text-warn">{c.state === "new" ? "新增，未保存" : c.state === "deleted" ? "将删除" : "已修改，未保存"}</Badge>}
                      </div>
                      <div className="mt-0.5 flex min-w-0 gap-3 text-[12px] text-text-3">
                        <span className="shrink-0 font-mono">{c.api_key_masked ?? "未填写"}</span>
                        {c.base_url && <Trunc text={c.base_url} mono />}
                      </div>
                    </div>
                    <Button variant="ghost" size="sm" onClick={() => protoToast(`已测试「${c.name}」：连接正常`)}>
                      <Zap />
                      测试
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`「${c.name}」的更多操作`} />}>
                        <MoreHorizontal />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => (credMode === "dialog" ? setDialog({ cred: c }) : setEditing(c.id))}>
                          <Pencil />
                          编辑
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="destructive"
                          onClick={() =>
                            credMode === "dialog"
                              ? setConfirmDelete(c)
                              : setCreds(c.state === "new" ? list.filter((x) => x.id !== c.id) : list.map((x) => (x.id === c.id ? { ...x, state: "deleted" } : x)))
                          }
                        >
                          <Trash2 />
                          删除
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  {credMode === "draft" && editing === c.id && (
                    <div className="border-t border-border bg-muted/20 px-4 py-4">
                      <CredFields
                        cred={c}
                        onChange={(patch) => setCreds(list.map((x) => (x.id === c.id ? { ...x, ...patch, state: x.state === "new" ? "new" : "edited" } : x)))}
                      />
                      <div className="mt-3 flex justify-end">
                        <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>
                          收起
                        </Button>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {config && config.fields.length > 0 && (
          <section>
            <h3 className="mb-1 text-[15px] font-medium">高级配置</h3>
            <p className="mb-3 text-[13px] text-text-3">留空则使用全局默认值。</p>
            <div className="space-y-3">
              {config.fields.map((f) => (
                <FieldRow key={f.key} label={f.key === "image_max_workers" ? "图片并发" : f.key === "video_max_workers" ? "视频并发" : f.label} labelFor={`pf-${f.key}`}>
                  <input
                    id={`pf-${f.key}`}
                    inputMode="numeric"
                    value={fields[f.key] ?? f.value ?? ""}
                    onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })}
                    placeholder="默认"
                    className={cn(CONTROL, "w-32 tabular-nums")}
                  />
                </FieldRow>
              ))}
            </div>
          </section>
        )}
      </div>

      <Dialog open={dialog !== null} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{dialog?.cred ? "编辑密钥" : "添加密钥"}</DialogTitle>
            <DialogDescription>保存后立即生效，不经过页面底部的保存栏。</DialogDescription>
          </DialogHeader>
          {dialog && <CredDialogBody initial={dialog.cred} onDone={(c) => {
            setCreds(dialog.cred ? list.map((x) => (x.id === c.id ? c : x)) : [...list, c]);
            setDialog(null);
            protoToast(dialog.cred ? "已更新密钥" : "已添加密钥");
          }} onCancel={() => setDialog(null)} />}
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmDelete !== null} onOpenChange={(o) => !o && setConfirmDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除密钥「{confirmDelete?.name}」？</AlertDialogTitle>
            <AlertDialogDescription>删除后无法恢复。使用这个密钥的生成任务会改用其他密钥；没有其他密钥时会失败。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setCreds(list.filter((x) => x.id !== confirmDelete?.id));
                setConfirmDelete(null);
                protoToast("已删除密钥");
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </DetailPane>
  );
}

function CredFields({ cred, onChange }: { cred: CredDraft; onChange: (patch: Partial<CredDraft>) => void }) {
  const [show, setShow] = useState(false);
  return (
    <div className="space-y-3">
      <FieldRow label="名称" labelFor={`cf-n-${cred.id}`}>
        <input id={`cf-n-${cred.id}`} value={cred.name} onChange={(e) => onChange({ name: e.target.value })} placeholder="例如 主账号" className={cn(CONTROL, "w-full")} />
      </FieldRow>
      <FieldRow label="密钥" labelFor={`cf-k-${cred.id}`} hint={cred.api_key_masked ? "留空则保留现有密钥。" : undefined}>
        <div className="relative">
          <input
            id={`cf-k-${cred.id}`}
            type={show ? "text" : "password"}
            autoComplete="off"
            value={cred.key_input ?? ""}
            onChange={(e) => onChange({ key_input: e.target.value })}
            placeholder={cred.api_key_masked ?? "粘贴密钥"}
            className={cn(CONTROL, "w-full pr-9 font-mono")}
          />
          <button type="button" onClick={() => setShow(!show)} aria-label={show ? "隐藏" : "显示"} className="absolute top-1/2 right-2 -translate-y-1/2 text-text-3 hover:text-text">
            {show ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
          </button>
        </div>
      </FieldRow>
      <FieldRow label="接口地址" labelFor={`cf-u-${cred.id}`} hint="可选。留空使用官方地址。">
        <input id={`cf-u-${cred.id}`} value={cred.base_url ?? ""} onChange={(e) => onChange({ base_url: e.target.value })} placeholder="https://" className={cn(CONTROL, "w-full font-mono")} />
      </FieldRow>
    </div>
  );
}

function CredDialogBody({ initial, onDone, onCancel }: { initial: CredDraft | null; onDone: (c: CredDraft) => void; onCancel: () => void }) {
  const [c, setC] = useState<CredDraft>(
    initial ?? { id: -Date.now(), provider: "", name: "", api_key_masked: null, credentials_filename: null, base_url: null, is_active: false, created_at: "" },
  );
  return (
    <>
      <CredFields cred={c} onChange={(p) => setC({ ...c, ...p })} />
      <DialogFooter>
        <Button variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button onClick={() => onDone({ ...c, api_key_masked: c.key_input ? `••••••••${c.key_input.slice(-4)}` : c.api_key_masked })}>
          {initial ? "保存密钥" : "添加密钥"}
        </Button>
      </DialogFooter>
    </>
  );
}

// ---------------------------------------------------------------------------
// 自定义供应商：没有只读态与「编辑」按钮，打开就是表单（「保存方式」结论：取消编辑模式）
// ---------------------------------------------------------------------------

function CustomDetail({ provider, isNew, focusModel }: { provider: ProtoCustomProvider; isNew?: boolean; focusModel?: string | null }) {
  const modelsMode = useAxis("models");
  const goSection = useGoSection();
  const [draft, setDraft] = useState<ProtoCustomProvider>(() => structuredClone(provider));
  const [showKey, setShowKey] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [measureRef, width] = useWidth<HTMLDivElement>();
  const [asideEl, setAsideEl] = useState<HTMLElement | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(provider);
  const save = () => {
    commitCustomProvider(draft);
    protoToast(isNew ? "已添加供应商" : "已保存供应商");
  };
  const discard = () => setDraft(structuredClone(provider));
  useEffect(() => {
    registerGuard({ dirty, save, discard, label: draft.display_name || "新供应商" });
    return () => registerGuard(null);
  });
  useEffect(() => {
    if (!focusModel) return;
    document.querySelector(`[data-model-id="${CSS.escape(focusModel)}"]`)?.scrollIntoView({ block: "center" });
  }, [focusModel]);

  const comfyui = draft.discovery_format === "comfyui";
  const wide = width >= 1100;
  const set = (patch: Partial<ProtoCustomProvider>) => setDraft({ ...draft, ...patch });

  return (
    <div ref={measureRef} className="flex h-full min-w-0 flex-1">
      <DetailPane
        aside={modelsMode === "inspector" && wide ? <div ref={setAsideEl} className="contents" /> : undefined}
        header={
          <div className="flex items-center gap-3">
            <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-[13px] font-medium">
              {Array.from(draft.display_name)[0] ?? "+"}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-2">
                <h2 className="min-w-0 text-[18px] font-medium">
                  <Trunc text={draft.display_name || "新的自定义供应商"} />
                </h2>
                {provider.sample && <SampleTag />}
              </div>
              {!isNew && (
                <div className="mt-0.5 flex min-w-0 gap-2 text-[13px] text-text-3">
                  <span className="shrink-0">{comfyui ? "ComfyUI" : draft.discovery_format === "google" ? "Google" : "OpenAI 兼容"}</span>
                  <Trunc text={draft.base_url} mono />
                </div>
              )}
            </div>
            {!isNew && (
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="更多操作" />}>
                  <MoreHorizontal />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem variant="destructive" onClick={() => setConfirmDelete(true)}>
                    <Trash2 />
                    删除供应商
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        }
        footer={
          <SaveBar
            dirty={dirty || !!isNew}
            onSave={save}
            onDiscard={() => (isNew ? goSection("providers") : discard())}
            saveLabel={isNew ? "添加供应商" : "保存"}
            extra={
              <Button variant="outline" onClick={() => protoToast("连接测试通过")}>
                测试连接
              </Button>
            }
          />
        }
      >
        <div className={cn("space-y-8 px-6 py-6", modelsMode === "cards" && "max-w-[760px]")}>
          <section className="max-w-[760px] space-y-3">
            <h3 className="text-[15px] font-medium">连接</h3>
            <FieldRow label="名称" labelFor="cp-name">
              <input id="cp-name" value={draft.display_name} onChange={(e) => set({ display_name: e.target.value })} placeholder="例如 公司中转站" className={cn(CONTROL, "w-full")} />
            </FieldRow>
            <FieldRow label="协议" hint={isNew ? "决定如何获取模型列表。创建后不能修改。" : "创建后不能修改。"}>
              <select value={draft.discovery_format} disabled={!isNew} onChange={(e) => set({ discovery_format: e.target.value })} className={cn(CONTROL, "w-48")}>
                <option value="openai">OpenAI 兼容</option>
                <option value="google">Google</option>
                <option value="comfyui">ComfyUI</option>
              </select>
            </FieldRow>
            <FieldRow label="接口地址" labelFor="cp-url" hint={draft.base_url && !comfyui ? <>模型列表地址：<span className="font-mono">{draft.base_url.replace(/\/$/, "")}/models</span></> : undefined}>
              <input id="cp-url" value={draft.base_url} onChange={(e) => set({ base_url: e.target.value })} placeholder="https://api.example.com/v1" className={cn(CONTROL, "w-full font-mono")} />
            </FieldRow>
            <FieldRow label="密钥" labelFor="cp-key">
              <div className="relative">
                <input id="cp-key" type={showKey ? "text" : "password"} autoComplete="off" placeholder={draft.api_key_masked || "粘贴密钥"} className={cn(CONTROL, "w-full pr-9 font-mono")} />
                <button type="button" onClick={() => setShowKey(!showKey)} aria-label={showKey ? "隐藏" : "显示"} className="absolute top-1/2 right-2 -translate-y-1/2 text-text-3 hover:text-text">
                  {showKey ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                </button>
              </div>
              <label className="mt-2 flex items-center gap-2 text-[13px] text-text-2">
                <Checkbox defaultChecked={!draft.api_key_masked && !!draft.base_url} />
                这个服务不需要密钥
              </label>
            </FieldRow>
          </section>

          <section>
            <ModelList
              providerId={draft.id}
              models={draft.models}
              comfyui={comfyui}
              onChange={(models) => set({ models })}
              asideTarget={asideEl}
              wide={wide}
            />
          </section>

          <section className="max-w-[760px] space-y-3">
            <div>
              <h3 className="text-[15px] font-medium">并发上限</h3>
              <p className="mt-1 text-[13px] text-text-3">同一时间最多向这个供应商发出的请求数。留空使用全局默认值。</p>
            </div>
            {(["image", "video", "audio"] as const).map((k) => (
              <FieldRow key={k} label={`${MEDIA_LABEL[k]}并发`} labelFor={`cp-w-${k}`}>
                <input
                  id={`cp-w-${k}`}
                  inputMode="numeric"
                  value={draft.workers[k]}
                  onChange={(e) => set({ workers: { ...draft.workers, [k]: e.target.value } })}
                  placeholder="默认"
                  className={cn(CONTROL, "w-32 tabular-nums")}
                />
              </FieldRow>
            ))}
          </section>
        </div>
      </DetailPane>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除供应商「{provider.display_name}」？</AlertDialogTitle>
            <AlertDialogDescription>
              它的 {provider.models.length} 个模型会一起删除，无法恢复。正在使用这些模型的项目会改用其他可用模型。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => protoToast("已删除供应商")}>
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
