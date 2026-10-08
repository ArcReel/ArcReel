// PROTOTYPE — #3091 变体 C：条目详情最上面的渠道表，以及「添加渠道」。
// 行 = 所有引用此条目的供应商模型。启用与默认是用户偏好，立即生效；渠道事实在编辑 Dialog 里改。
import { useMemo, useState } from "react";
import { Plus, Star } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "cn";
import {
  ENDPOINTS,
  effectiveMultiplier,
  type EndpointDef,
  type EndpointKey,
  type EntryData,
  type Provider,
  type ProviderModel,
} from "../model";
import { referencesOf, useProto } from "../store";
import { ProviderModelDialog } from "./ProviderModelDialog";
import {
  ProviderGlyph,
  SectionHeading,
  SimpleSelect,
  channelPrice,
} from "./shared";

export function ChannelsSection({
  entryId,
  data,
}: {
  entryId: string;
  data: EntryData;
}) {
  const providers = useProto((s) => s.providers);
  const updatePm = useProto((s) => s.updateProviderModel);
  const setDefault = useProto((s) => s.setDefault);
  const refs = useMemo(
    () => referencesOf(providers, entryId),
    [providers, entryId],
  );
  const [editing, setEditing] = useState<{
    provider: Provider;
    model: ProviderModel;
  } | null>(null);
  const [adding, setAdding] = useState(false);

  return (
    <section className="flex flex-col gap-3">
      <SectionHeading
        title="渠道"
        hint="经哪些供应商调用这个模型。倍率 = 供应商倍率 × 供应商模型倍率，参考单价按条目的官方价乘倍率算出。"
        action={
          <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
            <Plus aria-hidden />
            添加渠道
          </Button>
        }
      />
      {refs.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-4 py-5 text-sm text-muted-foreground">
          还没有供应商提供这个模型。添加一条渠道后才能在生成里选用。
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-32">供应商</TableHead>
                <TableHead>API 模型名 · 端点</TableHead>
                <TableHead className="w-24">启用 · 默认</TableHead>
                <TableHead className="w-28 text-right">参考单价</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {refs.map(({ provider, model }) => {
                const mul = effectiveMultiplier(provider, model);
                const noKey = provider.credentialCount === 0;
                return (
                  <TableRow key={model.key} className="hover:bg-transparent">
                    <TableCell>
                      <span className="flex items-center gap-2">
                        <ProviderGlyph provider={provider} className="size-4" />
                        <span className="flex min-w-0 flex-col">
                          <span className="truncate">
                            {provider.displayName}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {model.responsibility === "official"
                              ? "官方收录"
                              : provider.kind === "builtin"
                                ? "你添加的"
                                : "自定义供应商"}
                            {noKey && (
                              <span className="text-warn"> · 未配置密钥</span>
                            )}
                          </span>
                        </span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="flex min-w-0 flex-col">
                        <span
                          className="truncate font-mono text-xs"
                          title={model.apiModelName}
                        >
                          {model.apiModelName}
                        </span>
                        <span className="truncate text-xs text-muted-foreground">
                          {model.endpoint
                            ? ENDPOINTS[model.endpoint].label
                            : "内置通道"}
                          {Object.values(model.pathOverrides ?? {}).some(
                            Boolean,
                          ) && (
                            <span className="text-primary"> · 路径已覆盖</span>
                          )}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center gap-2">
                        <Switch
                          size="sm"
                          aria-label="启用"
                          checked={model.enabled}
                          onCheckedChange={(v: boolean) =>
                            updatePm(provider.id, model.key, {
                              enabled: v,
                              ...(v ? {} : { isDefault: false }),
                            })
                          }
                        />
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={model.isDefault ? "已是默认" : "设为默认"}
                          title={
                            model.isDefault
                              ? `${provider.displayName} 的默认${data.mediaType === "text" ? "文本" : ""}模型`
                              : "设为这个供应商的默认"
                          }
                          onClick={() => setDefault(provider.id, model.key)}
                        >
                          <Star
                            aria-hidden
                            className={cn(
                              model.isDefault
                                ? "fill-primary text-primary"
                                : "text-muted-foreground",
                            )}
                          />
                        </Button>
                      </span>
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">
                      <span className="flex flex-col items-end">
                        <span className="text-sm">
                          {channelPrice(data, mul)}
                        </span>
                        <span
                          className={cn(
                            "text-xs",
                            mul === 1
                              ? "text-muted-foreground"
                              : "text-subtle-foreground",
                          )}
                        >
                          倍率 ×{mul}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell className="text-right">
                      {model.responsibility === "user" ? (
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => setEditing({ provider, model })}
                        >
                          编辑
                        </Button>
                      ) : (
                        <span
                          className="text-xs text-muted-foreground"
                          title="官方收录的供应商模型全部跟随官方数据，只能启用、停用和设为默认"
                        >
                          只读
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
      {editing && (
        <ProviderModelDialog
          open
          onOpenChange={(v) => !v && setEditing(null)}
          provider={providers.find((p) => p.id === editing.provider.id)!}
          model={providers
            .find((p) => p.id === editing.provider.id)!
            .models.find((m) => m.key === editing.model.key)!}
          entry={data}
        />
      )}
      <AddChannelDialog
        open={adding}
        onOpenChange={setAdding}
        entryId={entryId}
        data={data}
      />
    </section>
  );
}

function AddChannelDialog({
  open,
  onOpenChange,
  entryId,
  data,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  entryId: string;
  data: EntryData;
}) {
  const providers = useProto((s) => s.providers);
  const addPm = useProto((s) => s.addProviderModel);
  const [providerId, setProviderId] = useState<string | null>(null);
  const [apiName, setApiName] = useState(data.name);
  const [endpoint, setEndpoint] = useState<EndpointKey | null>(null);

  const provider = providers.find((p) => p.id === providerId) ?? null;
  const candidates = providers.filter(
    (p) => p.mediaTypes.includes(data.mediaType) || p.kind === "custom",
  );
  const discovered =
    provider?.kind === "builtin"
      ? (provider.discovered ?? []).filter(
          (id) => !provider.models.some((m) => m.apiModelName === id),
        )
      : [];
  const endpoints = Object.values(ENDPOINTS).filter(
    (e) => e.mediaType === data.mediaType,
  );
  const canAdd =
    provider && apiName.trim() && (provider.kind === "builtin" || endpoint);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>添加渠道 · {data.displayName}</DialogTitle>
          <DialogDescription>
            选一个供应商，说明它用什么 API 模型名提供这个模型。新渠道由你负责。
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="grid grid-cols-[7rem_1fr] items-start gap-x-4 gap-y-4">
          <span className="pt-1.5 text-sm text-subtle-foreground">供应商</span>
          <SimpleSelect<string>
            value={providerId}
            onChange={(id) => {
              setProviderId(id);
              setEndpoint(null);
            }}
            placeholder="选择供应商"
            items={candidates.map((p) => ({
              value: p.id,
              label: p.displayName,
              hint:
                p.kind === "custom"
                  ? "自定义"
                  : p.credentialCount
                    ? undefined
                    : "未配置密钥",
            }))}
          />
          <span className="pt-1.5 text-sm text-subtle-foreground">
            API 模型名
          </span>
          <div className="flex flex-col gap-2">
            <Input
              className="font-mono"
              value={apiName}
              onChange={(e) => setApiName(e.target.value)}
            />
            {provider?.kind === "builtin" &&
              (provider.discoverable ? (
                discovered.length > 0 && (
                  <div className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      「获取模型」返回、官方未收录的 id：
                    </span>
                    <div className="flex flex-wrap gap-1">
                      {discovered.map((id) => (
                        <button
                          key={id}
                          type="button"
                          onClick={() => setApiName(id)}
                          className={cn(
                            "rounded-md border border-border px-2 py-0.5 font-mono text-xs hover:bg-muted",
                            apiName === id && "border-primary text-primary",
                          )}
                        >
                          {id}
                        </button>
                      ))}
                    </div>
                  </div>
                )
              ) : (
                <span className="text-xs text-muted-foreground">
                  {provider.displayName} 没有模型列表接口，只能手动填写 id。
                </span>
              ))}
          </div>
          {provider?.kind === "custom" && (
            <>
              <span className="pt-1.5 text-sm text-subtle-foreground">
                调用端点
              </span>
              <SimpleSelect<EndpointKey>
                value={endpoint}
                onChange={setEndpoint}
                placeholder="选择调用端点"
                items={endpoints.map((e) => ({
                  value: e.key,
                  label: e.label,
                  hint: e.kind === "declarative" ? "声明式" : undefined,
                }))}
              />
            </>
          )}
          {provider && (
            <p className="col-span-2 rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              {provider.kind === "builtin"
                ? "内置供应商下你添加的供应商模型由你负责；日后官方收录了同一 API 模型名，会自动换成官方版本。"
                : "路径覆盖与能力覆盖可在添加后点「编辑」设置。"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={!canAdd}
            onClick={() => {
              if (!provider) return;
              addPm(provider.id, {
                key: `${provider.id}:${apiName.trim()}:${Date.now()}`,
                entryId,
                apiModelName: apiName.trim(),
                enabled: true,
                isDefault: false,
                responsibility: "user",
                endpoint:
                  provider.kind === "custom"
                    ? (endpoint ?? undefined)
                    : undefined,
                multiplier: 1,
              });
              onOpenChange(false);
              setProviderId(null);
            }}
          >
            添加
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ResponsibilityBadge({ official }: { official: boolean }) {
  return (
    <Badge variant={official ? "secondary" : "outline"}>
      {official ? "官方收录" : "由你负责"}
    </Badge>
  );
}
