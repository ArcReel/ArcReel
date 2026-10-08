// PROTOTYPE — #3091 变体 B 的路径覆盖：每条请求一条拆段的 URL 拼接条。
// base_url 弱化不可改；未覆盖时自动补的 /v1 用虚线框；路径段可点击就地编辑，已覆盖的高亮并可恢复默认。

import { useState } from "react";
import { cn } from "cn";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ENDPOINTS, previewUrl, type EndpointKey } from "../model";

interface Props {
  baseUrl: string;
  endpoint: EndpointKey;
  apiModelName: string;
  overrides: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  readOnly?: boolean;
}

export function PathBars({ baseUrl, endpoint, apiModelName, overrides, onChange, readOnly }: Props) {
  const def = ENDPOINTS[endpoint];
  return (
    <ul className="flex flex-col gap-2">
      {def.requests.map((req) => {
        const override = overrides[req.name];
        const preview = previewUrl(baseUrl, def, req, override, apiModelName);
        const base = baseUrl.replace(/\/+$/, "");
        const path = preview.url.slice(base.length + (preview.autoAdded?.length ?? 0));
        return (
          <li key={req.name} className="flex flex-col gap-1">
            <div className="flex items-center gap-2 text-xs">
              <span className="rounded bg-muted px-1.5 py-0.5 font-medium tabular-nums">{req.method}</span>
              <span className="text-subtle-foreground">{req.label}</span>
              <span className="text-muted-foreground">请求名 {req.name}</span>
              {preview.overridden && !readOnly && (
                <button
                  type="button"
                  className="ml-auto text-primary hover:underline"
                  onClick={() => {
                    const next = { ...overrides };
                    delete next[req.name];
                    onChange(next);
                  }}
                >
                  恢复默认
                </button>
              )}
            </div>
            <div className="flex min-w-0 items-stretch overflow-hidden rounded-md border border-border bg-muted/30 font-mono text-xs">
              <span className="flex shrink items-center truncate px-2 py-1.5 text-muted-foreground" title={base}>
                {base}
              </span>
              {preview.autoAdded && (
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <span className="my-1 flex shrink-0 items-center rounded border border-dashed border-muted-foreground/50 px-1 text-muted-foreground" />
                    }
                  >
                    {preview.autoAdded}
                  </TooltipTrigger>
                  <TooltipContent>未覆盖时自动补上；设置路径覆盖后不再补</TooltipContent>
                </Tooltip>
              )}
              <PathSegment
                path={path}
                overridden={preview.overridden}
                rawOverride={override}
                defaultPath={req.defaultPath}
                readOnly={readOnly}
                onCommit={(v) => onChange({ ...overrides, [req.name]: v })}
              />
            </div>
            {preview.overridden && (
              <p className="text-xs text-muted-foreground">
                默认路径 <span className="font-mono">{req.defaultPath}</span>；覆盖相对于 base_url 原样值
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function PathSegment({
  path,
  overridden,
  rawOverride,
  defaultPath,
  readOnly,
  onCommit,
}: {
  path: string;
  overridden: boolean;
  rawOverride: string | undefined;
  defaultPath: string;
  readOnly?: boolean;
  onCommit: (v: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  if (editing !== null) {
    const commit = () => {
      const v = editing.trim();
      setEditing(null);
      // 没覆盖过且原样提交默认路径：不算覆盖
      if (!v || (!rawOverride && v === defaultPath) || v === rawOverride) return;
      onCommit(v.startsWith("/") ? v : `/${v}`);
    };
    return (
      <input
        autoFocus
        onFocus={(e) => e.target.select()}
        aria-label="路径"
        className="min-w-0 flex-1 bg-background px-2 py-1.5 text-foreground outline-none ring-2 ring-primary/60 ring-inset"
        value={editing}
        onChange={(e) => setEditing(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setEditing(null);
        }}
      />
    );
  }
  return (
    <button
      type="button"
      disabled={readOnly}
      title={readOnly ? undefined : "点击修改路径"}
      onClick={() => setEditing(rawOverride ?? defaultPath)}
      className={cn(
        "min-w-0 flex-1 truncate px-1 py-1.5 text-left",
        overridden ? "bg-primary/15 text-foreground" : "text-foreground",
        !readOnly && "hover:bg-primary/10 hover:underline hover:decoration-dotted",
      )}
    >
      {path}
    </button>
  );
}
