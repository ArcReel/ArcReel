// PROTOTYPE — #3091 变体 B 的能力覆盖（界面上叫「这个供应商支持的参数」）：每个维度一行「原值」+「修改」，修改后可编辑并高亮。
// 原值来自模型条目或调用端点；来源对用户没有意义，不显示。

import { useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { INPUT_MODE_LABEL, channelVideoCaps, type CapabilityOverride, type EndpointKey, type EntryData } from "../model";

type Dim = keyof CapabilityOverride;
const DIM_TEXT: Record<Dim, string> = {
  durations: "可选时长",
  resolutions: "分辨率",
  inputModes: "生成方式",
  maxReferenceImages: "参考图数量上限",
};

function show(dim: Dim, v: CapabilityOverride[Dim]): string {
  if (v === undefined) return "无";
  if (dim === "durations") return (v as number[]).length ? `${(v as number[]).join("、")} 秒` : "无";
  if (dim === "resolutions") return (v as string[]).join("、") || "无";
  if (dim === "inputModes") return (v as (keyof typeof INPUT_MODE_LABEL)[]).map((m) => INPUT_MODE_LABEL[m]).join("、");
  return `${v as number} 张`;
}

interface Props {
  entry: EntryData | null;
  endpoint: EndpointKey | undefined;
  value: CapabilityOverride | undefined;
  onChange: (next: CapabilityOverride) => void;
}

export function CapabilityOverrides({ entry, endpoint, value, onChange }: Props) {
  const media = entry?.mediaType;
  if (media !== "video" && media !== "image") {
    return <p className="text-xs text-muted-foreground">{media === "text" ? "文本" : "这个"}模型没有可覆盖的能力维度。</p>;
  }
  const channel = channelVideoCaps(endpoint);
  const dims: { dim: Dim; inherited: CapabilityOverride[Dim] }[] =
    media === "video"
      ? [
          { dim: "durations", inherited: entry?.durations ?? [] },
          { dim: "resolutions", inherited: entry?.resolutions ?? [] },
          { dim: "inputModes", inherited: channel.inputModes },
          { dim: "maxReferenceImages", inherited: channel.maxReferenceImages },
        ]
      : [{ dim: "resolutions", inherited: entry?.resolutions ?? [] }];
  const ov = value ?? {};
  const set = (dim: Dim, v: CapabilityOverride[Dim] | undefined) => {
    const next = { ...ov } as Record<string, unknown>;
    if (v === undefined) delete next[dim];
    else next[dim] = v;
    onChange(next);
  };

  return (
    <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border">
      {dims.map(({ dim, inherited }) => {
        const overridden = ov[dim] !== undefined;
        return (
          <li key={dim} className={cn("flex flex-col gap-1.5 px-3 py-2", overridden && "bg-primary/[0.07]")}>
            <div className="flex items-center gap-2">
              <span className="w-28 shrink-0 text-sm">{DIM_TEXT[dim]}</span>
              <span className={cn("min-w-0 flex-1 truncate text-sm", overridden ? "text-muted-foreground line-through" : "text-subtle-foreground")}>
                {show(dim, inherited)}
              </span>
              {overridden ? (
                <Button variant="ghost" size="xs" onClick={() => set(dim, undefined)}>
                  恢复
                </Button>
              ) : (
                <Button variant="outline" size="xs" onClick={() => set(dim, structuredClone(inherited))}>
                  修改
                </Button>
              )}
            </div>
            {overridden && <OverrideEditor dim={dim} value={ov[dim]} onChange={(v) => set(dim, v)} />}
          </li>
        );
      })}
    </ul>
  );
}

function OverrideEditor({ dim, value, onChange }: { dim: Dim; value: CapabilityOverride[Dim]; onChange: (v: CapabilityOverride[Dim]) => void }) {
  const [text, setText] = useState(() => (Array.isArray(value) ? value.join(", ") : String(value ?? "")));
  if (dim === "inputModes") {
    const modes = (value ?? []) as (keyof typeof INPUT_MODE_LABEL)[];
    return (
      <div className="flex gap-1.5 pl-30">
        {(Object.keys(INPUT_MODE_LABEL) as (keyof typeof INPUT_MODE_LABEL)[]).map((m) => {
          const on = modes.includes(m);
          return (
            <button
              key={m}
              type="button"
              aria-pressed={on}
              onClick={() => onChange(on ? modes.filter((x) => x !== m) : [...modes, m])}
              className={cn("h-7 rounded-md border px-2 text-xs", on ? "border-primary/50 bg-primary/15" : "border-border text-subtle-foreground")}
            >
              {INPUT_MODE_LABEL[m]}
            </button>
          );
        })}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 pl-30">
      <Input
        aria-label={`修改${DIM_TEXT[dim]}`}
        className="h-7 w-56"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const parts = e.target.value.split(/[,，\s]+/).filter(Boolean);
          if (dim === "durations") onChange(parts.map(Number).filter((n) => n > 0));
          else if (dim === "resolutions") onChange(parts);
          else onChange(Number(e.target.value) || 0);
        }}
      />
      <span className="text-xs text-muted-foreground">{dim === "maxReferenceImages" ? "张" : "多个值用逗号分隔"}</span>
    </div>
  );
}
