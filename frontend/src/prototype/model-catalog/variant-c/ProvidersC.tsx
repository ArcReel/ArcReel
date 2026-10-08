// PROTOTYPE — #3091 变体 C 的「供应商」分区：只管连接（密钥、base_url、供应商倍率），不列供应商模型。
import { useCallback, useMemo } from "react";
import { ArrowRight, KeyRound, Plus } from "lucide-react";
import { Link } from "wouter";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SecondaryRail, type SecondaryRailGroup } from "@/components/shared/master-detail/SecondaryRail";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { ProviderIcon } from "@/components/shared/ProviderIcon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MEDIA_LABEL, type Provider } from "../model";
import { useProto } from "../store";
import { ProviderGlyph, SimpleSelect, useParams } from "./shared";

const href = (params: Record<string, string>) => `/app/settings?${new URLSearchParams({ variant: "C", ...params }).toString()}`;

export function ProvidersC() {
  const providers = useProto((s) => s.providers);
  const [params] = useParams();
  const selected = providers.find((p) => p.id === params.get("p")) ?? providers[0];

  const groups = useMemo<SecondaryRailGroup[]>(
    () => [
      {
        id: "builtin",
        label: "预置",
        items: providers
          .filter((p) => p.kind === "builtin")
          .map((p) => ({
            id: p.id,
            label: p.displayName,
            description: p.credentialCount > 0 ? `${p.credentialCount} 个密钥` : "未配置",
            icon: <ProviderIcon providerId={p.id} className="size-4" />,
            href: href({ section: "providers", p: p.id }),
          })),
      },
      {
        id: "custom",
        label: "自定义",
        items: providers
          .filter((p) => p.kind === "custom")
          .map((p) => ({
            id: p.id,
            label: p.displayName,
            description: p.baseUrl?.replace(/^https?:\/\//, "") ?? "",
            icon: <ProviderGlyph provider={p} className="size-4" />,
            href: href({ section: "providers", p: p.id }),
          })),
        action: { id: "custom:new", label: "添加自定义供应商", icon: <Plus className="size-4" />, href: href({ section: "providers", p: selected.id }) },
      },
    ],
    [providers, selected.id],
  );

  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      <SecondaryRail label="供应商列表" groups={groups} activeId={selected.id} replace />
      <ProviderConnection key={selected.id} provider={selected} />
    </div>
  );
}

interface ConnValue {
  displayName: string;
  baseUrl: string;
  multiplier: string;
  discovery: string;
}

function ProviderConnection({ provider }: { provider: Provider }) {
  const updateProvider = useProto((s) => s.updateProvider);
  const source = useMemo<ConnValue>(
    () => ({ displayName: provider.displayName, baseUrl: provider.baseUrl ?? "", multiplier: String(provider.multiplier), discovery: "openai" }),
    [provider.displayName, provider.baseUrl, provider.multiplier],
  );
  const save = useCallback(
    async (v: ConnValue) => {
      updateProvider(provider.id, { displayName: v.displayName, baseUrl: v.baseUrl || undefined, multiplier: Number(v.multiplier) || 0 });
    },
    [updateProvider, provider.id],
  );
  const unit = useEditUnit<ConnValue>({ source, save });
  const enabled = provider.models.filter((m) => m.enabled).length;
  const isCustom = provider.kind === "custom";

  return (
    <DetailPane
      header={
        <div className="flex items-start gap-3">
          {isCustom ? <ProviderGlyph provider={provider} className="mt-1 size-6 text-xs" /> : <ProviderIcon providerId={provider.id} className="mt-0.5 size-7 shrink-0" />}
          <div className="flex min-w-0 flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-medium">{provider.displayName}</h2>
              <Badge variant={provider.credentialCount > 0 ? "secondary" : "outline"}>{provider.credentialCount > 0 ? "已就绪" : "未配置"}</Badge>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {provider.mediaTypes.map((m) => (
                <Badge key={m} variant="outline">
                  {MEDIA_LABEL[m]}
                </Badge>
              ))}
            </div>
          </div>
        </div>
      }
      footer={<SaveBar unit={unit} className="max-w-178" />}
    >
      <div className="flex max-w-190 flex-col gap-8 px-6 py-6">
        <Link
          href={href({ section: "models", p: provider.id })}
          className="flex items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3 text-sm hover:bg-muted/60"
        >
          <span className="min-w-0 flex-1">
            这个供应商提供 {provider.models.length} 个模型，已启用 {enabled} 个。启用、设为默认和渠道设置都在「模型」里管理。
          </span>
          <span className="flex shrink-0 items-center gap-1 text-primary">
            在「模型」里查看
            <ArrowRight aria-hidden className="size-4" />
          </span>
        </Link>

        {isCustom && (
          <section className="flex flex-col gap-3">
            <h3 className="text-base font-medium">连接</h3>
            <Field label="名称" id="c-name">
              <Input id="c-name" value={unit.value.displayName} onChange={(e) => unit.setValue((v) => ({ ...v, displayName: e.target.value }))} />
            </Field>
            <Field label="base_url" id="c-base" hint="路径覆盖都相对于这里的原样值。">
              <Input id="c-base" className="font-mono" value={unit.value.baseUrl} onChange={(e) => unit.setValue((v) => ({ ...v, baseUrl: e.target.value }))} />
            </Field>
            <Field label="发现格式" id="c-disc" hint="「获取模型」用哪种列表接口。">
              <SimpleSelect<string>
                id="c-disc"
                className="w-60"
                value={unit.value.discovery}
                onChange={(d) => unit.setValue((v) => ({ ...v, discovery: d }))}
                items={[
                  { value: "openai", label: "OpenAI /v1/models" },
                  { value: "google", label: "Google models.list" },
                ]}
              />
            </Field>
          </section>
        )}

        <section className="flex flex-col gap-3">
          <h3 className="text-base font-medium">密钥</h3>
          <div className="flex items-center gap-3 rounded-lg border border-border px-4 py-3 text-sm">
            <KeyRound aria-hidden className="size-4 text-muted-foreground" />
            <span className="flex-1">{provider.credentialCount > 0 ? `已配置 ${provider.credentialCount} 个密钥` : "还没有密钥"}</span>
            <Button variant="outline" size="sm" disabled title="原型里只示意">
              添加密钥
            </Button>
          </div>
        </section>

        <section className="flex flex-col gap-3">
          <h3 className="text-base font-medium">计价</h3>
          <Field label="供应商倍率" id="c-mul" hint={provider.id === "ark-agent-plan" ? "订阅制，默认 0：调用费用记 0，不查价。" : "乘在这个供应商的全部参考费用上，例如中转站按 8 折计费填 0.8。"}>
            <Input id="c-mul" type="number" step="0.05" className="w-28 tabular-nums" value={unit.value.multiplier} onChange={(e) => unit.setValue((v) => ({ ...v, multiplier: e.target.value }))} />
          </Field>
        </section>
      </div>
    </DetailPane>
  );
}

function Field({ label, id, hint, children }: { label: string; id: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
