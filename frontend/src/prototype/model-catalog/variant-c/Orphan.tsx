// PROTOTYPE — #3091 变体 C：没有对应到条目的供应商模型，以及新建条目。
import { useMemo, useState } from "react";
import { CircleDashed, Search } from "lucide-react";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { cn } from "cn";
import {
  ENDPOINTS,
  MAKER_LABEL,
  MEDIA_LABEL,
  MEDIA_TYPES,
  PRICE_PRESET_LABEL,
  effectiveOf,
  makerLabel,
  priceFromPreset,
  type Currency,
  type EntryData,
  type MediaType,
  type PricePreset,
  type Provider,
  type ProviderModel,
} from "../model";
import { useProto } from "../store";
import { ProviderGlyph, SimpleSelect } from "./shared";

export function OrphanDetail({ provider, model, onLinked }: { provider: Provider; model: ProviderModel; onLinked: (entryId: string) => void }) {
  const entries = useProto((s) => s.entries);
  const update = useProto((s) => s.updateProviderModel);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const media = model.endpoint ? ENDPOINTS[model.endpoint].mediaType : null;
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries
      .map((e) => ({ e, d: effectiveOf(e) }))
      .filter(({ e, d }) => (!media || d.mediaType === media) && (!q || `${d.displayName} ${e.id}`.toLowerCase().includes(q)))
      .slice(0, 30);
  }, [entries, query, media]);

  const link = (entryId: string) => {
    update(provider.id, model.key, { entryId });
    onLinked(entryId);
  };

  return (
    <DetailPane
      header={
        <div className="flex items-start gap-3">
          <ProviderGlyph provider={provider} className="mt-1 size-5" />
          <div className="flex min-w-0 flex-col gap-1">
            <h2 className="truncate font-mono text-lg font-medium">{model.apiModelName}</h2>
            <p className="text-sm text-muted-foreground">
              {provider.displayName} 下的供应商模型{model.endpoint && `，经 ${ENDPOINTS[model.endpoint].label}`}
            </p>
          </div>
        </div>
      }
    >
      <div className="flex max-w-190 flex-col gap-6 px-6 py-6">
        <div role="status" className="flex items-start gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3 text-sm">
          <CircleDashed aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <p>这个供应商模型没有对应的模型条目，所以没有价格，经它的调用都记为未定价。为它选择一个已有条目，或新建一个。</p>
        </div>
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-base font-medium">选择已有条目</h3>
            <Button variant="outline" size="sm" onClick={() => setCreating(true)}>
              新建条目
            </Button>
          </div>
          <InputGroup>
            <InputGroupAddon>
              <Search aria-hidden />
            </InputGroupAddon>
            <InputGroupInput aria-label="搜索条目" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索模型名或 id" />
          </InputGroup>
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {matches.length === 0 && <li className="px-3 py-4 text-sm text-muted-foreground">没有匹配的条目。</li>}
            {matches.map(({ e, d }) => (
              <li key={e.id} className="flex items-center gap-3 px-3 py-2">
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-sm">{d.displayName}</span>
                  <span className="truncate font-mono text-xs text-muted-foreground">{e.id}</span>
                </span>
                <span className="text-xs text-muted-foreground">{makerLabel(d.maker)}</span>
                <Button size="xs" variant="outline" onClick={() => link(e.id)}>
                  对应到这里
                </Button>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <CreateEntryDialog
        open={creating}
        onOpenChange={setCreating}
        initialName={model.apiModelName}
        initialMedia={media ?? "text"}
        onCreated={(id) => {
          link(id);
        }}
      />
    </DetailPane>
  );
}

export function CreateEntryDialog({
  open,
  onOpenChange,
  initialName = "",
  initialMedia = "text",
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  initialName?: string;
  initialMedia?: MediaType;
  onCreated: (id: string) => void;
}) {
  const create = useProto((s) => s.createEntry);
  const entries = useProto((s) => s.entries);
  const [maker, setMaker] = useState("");
  const [name, setName] = useState(initialName);
  const [displayName, setDisplayName] = useState(initialName);
  const [media, setMedia] = useState<MediaType>(initialMedia);
  const [preset, setPreset] = useState<PricePreset>("token");
  const [currency, setCurrency] = useState<Currency>("USD");
  const id = `${maker.trim() || "?"}/${name.trim()}`;
  const exists = entries.some((e) => e.id === id);
  const ok = maker.trim() && name.trim() && !exists;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>新建模型条目</DialogTitle>
          <DialogDescription>目录里没有的模型由你新建，新建的条目由你负责。日后官方收录了同一身份，可以改为跟随官方。</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid grid-cols-[7rem_1fr] items-center gap-x-4 gap-y-3">
          <span className="text-sm text-subtle-foreground">模型厂商</span>
          <div className="flex flex-col gap-1">
            <Input list="c-makers" value={maker} onChange={(e) => setMaker(e.target.value)} placeholder="如 bfl、google" />
            <datalist id="c-makers">
              {Object.entries(MAKER_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </datalist>
          </div>
          <span className="text-sm text-subtle-foreground">官方模型名</span>
          <Input className="font-mono" value={name} onChange={(e) => setName(e.target.value)} />
          <span className="text-sm text-subtle-foreground">条目 id</span>
          <span className={cn("font-mono text-xs", exists ? "text-destructive" : "text-muted-foreground")}>{exists ? `${id} 已存在` : id}</span>
          <span className="text-sm text-subtle-foreground">显示名</span>
          <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          <span className="text-sm text-subtle-foreground">媒体类型</span>
          <SimpleSelect<MediaType> value={media} onChange={setMedia} items={MEDIA_TYPES.map((m) => ({ value: m, label: MEDIA_LABEL[m] }))} />
          <span className="text-sm text-subtle-foreground">计价方式</span>
          <div className="flex gap-2">
            <SimpleSelect<PricePreset> className="flex-1" value={preset} onChange={setPreset} items={(Object.keys(PRICE_PRESET_LABEL) as PricePreset[]).map((p) => ({ value: p, label: PRICE_PRESET_LABEL[p] }))} />
            <SimpleSelect<Currency>
              className="w-32"
              value={currency}
              onChange={setCurrency}
              items={[
                { value: "USD", label: "USD" },
                { value: "CNY", label: "CNY" },
              ]}
            />
          </div>
          <span />
          <span className="text-xs text-muted-foreground">按预设生成价目表，单价先为 0，建好后在「编辑价格」里填写。</span>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            disabled={!ok}
            onClick={() => {
              const data: EntryData = {
                id,
                maker: maker.trim(),
                name: name.trim(),
                displayName: displayName.trim() || name.trim(),
                mediaType: media,
                capabilities: media === "text" ? ["text_generation"] : media === "image" ? ["text_to_image"] : media === "audio" ? ["text_to_speech"] : [],
                durations: [],
                resolutions: [],
                durationConstraints: {},
                maxOutputTokens: null,
                price: priceFromPreset(preset, currency),
                version: "user",
              };
              create(data);
              onCreated(id);
              onOpenChange(false);
            }}
          >
            新建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
