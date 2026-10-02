// PROTOTYPE — #2972 调用端点分区。全出血档，与供应商同一套「二级栏 + 详情栏」，保存栏从页头右上角挪到详情栏底部。
// 端点详情的「被引用」区给出跳回供应商模型的反向链接；从供应商跳过来时页头有「返回」。
// 定义表单主体沿用现有 EndpointForm，本原型只摆出它的位置，不重做表单。

import { useEffect, useState } from "react";
import { useSearch } from "wouter";
import { ArrowLeft, Copy, Download, FileJson2, Lock, MoreHorizontal, Plus, Share2, Store, Trash2, Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { ButtonGroup } from "@/components/ui/button-group";
import type { EndpointDescriptor } from "@/types";
import { CONTROL, DetailPane, FieldRow, LeaveDialog, SampleTag, SaveBar, Trunc, guarded, protoToast, registerGuard } from "./bits";
import { MEDIA_LABEL, useCatalog, useEndpointLabel, useEndpoints } from "./data";
import { useGoSection } from "./EndpointRef";
import { referencesOf } from "./samples";
import { useAxis } from "./store";

export function EndpointsProto() {
  const endpoints = useEndpoints();
  const label = useEndpointLabel();
  const search = useSearch();
  const goSection = useGoSection();
  const { custom } = useCatalog();
  const railMode = useAxis("rail");
  const params = new URLSearchParams(search);
  const selectedKey = params.get("endpoint") ?? endpoints.find((e) => e.source === "custom")?.key ?? endpoints[0]?.key;
  const from = params.get("from");
  const fromProvider = from ? custom.find((p) => String(p.id) === from) : undefined;
  const selected = endpoints.find((e) => e.key === selectedKey);

  const mine = endpoints.filter((e) => e.source === "custom");
  const builtin = endpoints.filter((e) => e.source !== "custom");
  const refCount = (k: string) => referencesOf(k, custom).length;

  const item = (e: EndpointDescriptor) => {
    const active = e.key === selectedKey;
    const n = refCount(e.key);
    return (
      <button
        key={e.key}
        type="button"
        aria-current={active ? "page" : undefined}
        onClick={() => guarded(() => goSection("endpoints", { endpoint: e.key, ...(from ? { from } : {}) }))}
        className={cn(
          "mb-px flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-left text-[13px] transition-colors",
          active ? "bg-primary/14 text-text" : "text-text-2 hover:bg-accent hover:text-text",
        )}
      >
        {e.kind === "python" ? <Lock className="size-3.5 shrink-0 text-text-3" aria-hidden /> : <FileJson2 className="size-3.5 shrink-0 text-text-3" aria-hidden />}
        <span className="min-w-0 flex-1">
          <Trunc text={label(e)} />
        </span>
        {n > 0 && (
          <span className="shrink-0 text-[12px] text-text-3 tabular-nums" aria-label={`${n} 个模型在用`}>
            {n}
          </span>
        )}
      </button>
    );
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1">
      <nav aria-label="调用端点列表" data-zone="rail" className={cn("h-full shrink-0 overflow-y-auto border-r border-border bg-sidebar/50 px-3 py-4", railMode === "adaptive" ? "w-[264px]" : "w-60")}>
        <div className="mb-4 flex flex-col gap-2">
          <ButtonGroup className="w-full">
            <Button variant="outline" className="flex-1" onClick={() => protoToast("新建端点")}>
              <Plus />
              新建端点
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="outline" size="icon" aria-label="更多新建方式" />}>
                <Upload />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem>导入 JSON 定义</DropdownMenuItem>
                <DropdownMenuItem>导入 ComfyUI 工作流</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </ButtonGroup>
          <Button variant="ghost" className="justify-start text-text-2" onClick={() => guarded(() => goSection("market"))}>
            <Store />
            从市场获取
          </Button>
        </div>
        <div className="mb-1 px-2.5 text-[12px] text-text-3">我的端点</div>
        {mine.length === 0 ? <p className="px-2.5 py-1 text-[12px] text-text-3">还没有自定义端点。</p> : mine.map(item)}
        {(["text", "image", "video", "audio"] as const).map((mt) => (
          <div key={mt} className="mt-4">
            <div className="mb-1 px-2.5 text-[12px] text-text-3">内置{MEDIA_LABEL[mt]}端点</div>
            {builtin.filter((e) => e.media_type === mt).map(item)}
          </div>
        ))}
      </nav>
      {selected && <EndpointPage key={selected.key} endpoint={selected} fromProvider={fromProvider ? { id: fromProvider.id, name: fromProvider.display_name } : undefined} />}
      <LeaveDialog />
    </div>
  );
}

function EndpointPage({ endpoint, fromProvider }: { endpoint: EndpointDescriptor; fromProvider?: { id: number; name: string } }) {
  const label = useEndpointLabel();
  const goSection = useGoSection();
  const editable = endpoint.source === "custom";
  return (
    <EndpointBody
      endpoint={endpoint}
      header={
        <div>
          {fromProvider && (
            <Button variant="ghost" size="sm" className="-ml-2 mb-2 text-text-2" onClick={() => guarded(() => goSection("providers", { custom: String(fromProvider.id) }))}>
              <ArrowLeft />
              返回「{fromProvider.name}」
            </Button>
          )}
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-2">
                <h2 className="min-w-0 text-[18px] font-medium">
                  <Trunc text={label(endpoint)} />
                </h2>
                <Badge variant="outline">{editable ? "我的端点" : "内置，只读"}</Badge>
                <Badge variant="outline">{MEDIA_LABEL[endpoint.media_type]}</Badge>
              </div>
              <p className="mt-0.5 font-mono text-[12px] text-text-3">
                {endpoint.request_method} {endpoint.request_path_template}
              </p>
            </div>
            {editable ? (
              <Button variant="outline" onClick={() => goSection("providers", { custom: "new" })}>
                <Plus />
                新建供应商并使用
              </Button>
            ) : (
              <Button variant="outline" onClick={() => protoToast("已复制为我的端点")}>
                <Copy />
                复制为我的端点
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="更多操作" />}>
                <MoreHorizontal />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem>
                  <Download />
                  导出 JSON
                </DropdownMenuItem>
                {editable && (
                  <>
                    <DropdownMenuItem>
                      <Share2 />
                      分享到市场
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive">
                      <Trash2 />
                      删除端点
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      }
    />
  );
}

/** 端点详情主体；页面里与 Sheet 里共用。Sheet 里没有页头，保存栏留在 Sheet 底部。 */
export function EndpointBody({ endpoint, header, inSheet }: { endpoint: EndpointDescriptor; header?: React.ReactNode; inSheet?: boolean }) {
  const label = useEndpointLabel();
  const { custom } = useCatalog();
  const goSection = useGoSection();
  const editable = endpoint.source === "custom";
  const initial = { name: label(endpoint), path: endpoint.request_path_template };
  const [draft, setDraft] = useState(initial);
  const dirty = draft.name !== initial.name || draft.path !== initial.path;
  const save = () => protoToast("已保存端点");
  const discard = () => setDraft(initial);
  useEffect(() => {
    if (inSheet) return;
    registerGuard({ dirty, save, discard, label: initial.name });
    return () => registerGuard(null);
  });
  const refs = referencesOf(endpoint.key, custom);

  return (
    <DetailPane
      header={header}
      footer={
        editable ? (
          <SaveBar
            dirty={dirty}
            onSave={save}
            onDiscard={discard}
            extra={
              <Button variant="outline" onClick={() => protoToast("试调用成功")}>
                试调用
              </Button>
            }
          />
        ) : undefined
      }
    >
      <div className="max-w-[760px] space-y-8 px-6 py-6">
        <section>
          <div className="mb-2 flex items-center gap-2">
            <h3 className="text-[15px] font-medium">使用这个端点的模型</h3>
            {refs.some((r) => r.provider.sample) && <SampleTag />}
          </div>
          {refs.length === 0 ? (
            <p className="text-[13px] text-text-3">还没有模型使用这个端点。</p>
          ) : (
            <ul className="divide-y divide-border rounded-[10px] border border-border">
              {refs.map(({ provider, model }) => (
                <li key={`${provider.id}/${model.key}`} className="flex items-center gap-3 px-4 py-2">
                  <span className="min-w-0 flex-1">
                    <Trunc text={model.model_id} mono className="text-[13px]" />
                  </span>
                  <span className="max-w-[40%] min-w-0 text-[13px] text-text-3">
                    <Trunc text={provider.display_name} />
                  </span>
                  {!inSheet && (
                    <Button variant="ghost" size="sm" onClick={() => guarded(() => goSection("providers", { custom: String(provider.id), model: model.model_id }))}>
                      打开
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="space-y-3">
          <h3 className="text-[15px] font-medium">定义</h3>
          {!editable && <p className="text-[13px] text-text-3">内置端点只读。需要修改时，先复制为我的端点。</p>}
          <FieldRow label="名称" labelFor={`ep-name-${endpoint.key}`}>
            <input id={`ep-name-${endpoint.key}`} readOnly={!editable} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={cn(CONTROL, "w-full read-only:opacity-70")} />
          </FieldRow>
          <FieldRow label="请求路径" labelFor={`ep-path-${endpoint.key}`}>
            <input id={`ep-path-${endpoint.key}`} readOnly={!editable} value={draft.path} onChange={(e) => setDraft({ ...draft, path: e.target.value })} className={cn(CONTROL, "w-full font-mono read-only:opacity-70")} />
          </FieldRow>
          <div className="rounded-[10px] border border-dashed border-border px-4 py-8 text-center text-[13px] text-text-3">
            其余字段（请求体模板、响应提取、轮询、变量）沿用现有 EndpointForm，放在这里；本原型不重做。
          </div>
        </section>
      </div>
    </DetailPane>
  );
}
