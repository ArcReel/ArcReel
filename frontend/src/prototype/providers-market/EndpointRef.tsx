// PROTOTYPE — #2972「在供应商表单里管理端点：Sheet 还是跳转」的三种答法。
// jump：跳到「调用端点」分区（有草稿先拦截），端点页顶部给「返回供应商」；
// sheet：就地打开 Sheet，供应商草稿留在原处；
// peek：Popover 速览关键信息，要改再跳转。

import { useSyncExternalStore } from "react";
import { useLocation } from "wouter";
import { ArrowUpRight, PanelRightOpen, Store } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { guarded } from "./bits";
import { MEDIA_LABEL, useCatalog, useEndpointLabel, useEndpoints } from "./data";
import { EndpointBody } from "./EndpointsProto";
import { referencesOf } from "./samples";
import { useAxis } from "./store";

const CLEAR_KEYS = ["provider", "custom", "model", "endpoint", "from", "mtab"];

/** 换分区时保留原型参数（variant、p.*），清掉分区自己的选中参数。 */
export function useGoSection() {
  const [, navigate] = useLocation();
  return (section: string, extra: Record<string, string> = {}) => {
    const params = new URLSearchParams(window.location.search);
    for (const k of CLEAR_KEYS) params.delete(k);
    params.set("section", section);
    for (const [k, v] of Object.entries(extra)) params.set(k, v);
    navigate(`/app/settings?${params.toString()}`, { replace: false });
  };
}

// --- Sheet 的打开状态放在模块里，Sheet 只挂一份 ---
let sheetKey: string | null = null;
const listeners = new Set<() => void>();
function setSheet(k: string | null) {
  sheetKey = k;
  listeners.forEach((l) => l());
}

export function EndpointRef({ endpointKey, fromProvider }: { endpointKey: string; fromProvider: number }) {
  const mode = useAxis("endpoint");
  const goSection = useGoSection();
  const jump = () => guarded(() => goSection("endpoints", { endpoint: endpointKey, from: String(fromProvider) }));
  if (!endpointKey) return null;

  if (mode === "jump") {
    return (
      <Button variant="ghost" size="sm" onClick={jump} className="text-text-2">
        <ArrowUpRight />
        在调用端点中打开
      </Button>
    );
  }
  if (mode === "sheet") {
    return (
      <Button variant="ghost" size="sm" onClick={() => setSheet(endpointKey)} className="text-text-2">
        <PanelRightOpen />
        查看端点
      </Button>
    );
  }
  return <EndpointPeek endpointKey={endpointKey} onJump={jump} />;
}

function EndpointPeek({ endpointKey, onJump }: { endpointKey: string; onJump: () => void }) {
  const endpoints = useEndpoints();
  const label = useEndpointLabel();
  const { custom } = useCatalog();
  const e = endpoints.find((x) => x.key === endpointKey);
  const refs = referencesOf(endpointKey, custom);
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="ghost" size="sm" className="text-text-2" />}>端点详情</PopoverTrigger>
      <PopoverContent align="end" className="w-96 gap-3 p-4">
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-[14px] font-medium">{label(e)}</span>
          <Badge variant="outline">{e?.source === "custom" ? "我的端点" : "内置"}</Badge>
        </div>
        <div className="rounded-md bg-muted/60 px-2.5 py-1.5 font-mono text-[12px] break-all text-text-2">
          {e?.request_method} {e?.request_path_template}
        </div>
        <dl className="grid grid-cols-[72px_1fr] gap-y-1 text-[13px]">
          <dt className="text-text-3">媒体类型</dt>
          <dd>{MEDIA_LABEL[e?.media_type ?? ""] ?? "—"}</dd>
          <dt className="text-text-3">被引用</dt>
          <dd>{refs.length} 个模型</dd>
          {e?.media_type === "video" && (
            <>
              <dt className="text-text-3">尾帧</dt>
              <dd>{e.end_image_capable ? "支持" : "不支持"}</dd>
            </>
          )}
        </dl>
        <div className="flex justify-end">
          <Button size="sm" variant="outline" onClick={onJump}>
            <ArrowUpRight />
            在调用端点中打开
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

/** Sheet 模式的唯一实例，挂在供应商分区根部。 */
export function EndpointSheetHost() {
  const key = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => sheetKey,
  );
  const endpoints = useEndpoints();
  const label = useEndpointLabel();
  const e = endpoints.find((x) => x.key === key);
  return (
    <Sheet open={key !== null} onOpenChange={(o) => !o && setSheet(null)}>
      <SheetContent className="gap-0 p-0 data-[side=right]:w-[min(640px,90vw)] data-[side=right]:sm:max-w-none">
        <SheetHeader className="border-b border-border px-6 py-4">
          <SheetTitle>{label(e)}</SheetTitle>
          <SheetDescription>供应商表单的草稿保留在原处，关闭 Sheet 后继续编辑。</SheetDescription>
        </SheetHeader>
        {e && <EndpointBody endpoint={e} inSheet />}
      </SheetContent>
    </Sheet>
  );
}

export function MarketLink() {
  const goSection = useGoSection();
  return (
    <button
      type="button"
      onClick={() => guarded(() => goSection("market"))}
      className="inline-flex items-center gap-1 text-[12px] text-text-3 underline-offset-4 hover:text-text hover:underline"
    >
      <Store className="size-3" aria-hidden />
      没有合适的端点？从市场获取
    </button>
  );
}
