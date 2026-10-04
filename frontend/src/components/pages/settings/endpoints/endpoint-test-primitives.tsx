import { useId, type ReactNode } from "react";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { PreviewedRequest } from "@/types";

/** 端点测试的一张卡。`badge` 用于「产生一次调用费用」「占用 GPU」这类代价提示。 */
export function TestCard({
  title,
  badge,
  desc,
  children,
}: {
  title: string;
  badge?: string;
  desc: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-sm font-medium">{title}</h4>
          {badge && <Badge variant="outline">{badge}</Badge>}
        </div>
        <p className="max-w-[40em] text-xs text-muted-foreground">{desc}</p>
      </div>
      {children}
    </section>
  );
}

/** 端点测试的一个字段：标签在上，控件在下。`htmlFor` 指向控件的 id。 */
export function TestField({
  label,
  htmlFor,
  className,
  children,
}: {
  label: ReactNode;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

export interface TestSelectItem {
  value: string;
  label: string;
  disabled?: boolean;
}

/** 端点测试的下拉选择：标签与触发器以 `aria-labelledby` 关联，读屏与测试都按标签找到它。 */
export function TestSelect({
  label,
  value,
  items,
  onValueChange,
  className,
}: {
  label: string;
  value: string;
  items: TestSelectItem[];
  onValueChange: (value: string) => void;
  className?: string;
}) {
  const labelId = useId();
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <span id={labelId} className="text-sm font-medium">
        {label}
      </span>
      <Select
        items={items}
        value={value}
        onValueChange={(next) => {
          if (typeof next === "string") onValueChange(next);
        }}
      >
        <SelectTrigger aria-labelledby={labelId} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((item) => (
            <SelectItem key={item.value} value={item.value} disabled={item.disabled}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** 渲染并脱敏后的一节请求，按它真发出去的样子逐行摆开。长地址与请求体在自身框内滚动，不撑宽页面。 */
export function RequestPreview({ label, request }: { label: string; request: PreviewedRequest }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-sm font-medium">{label}</span>
      <pre className="relative max-h-96 overflow-auto rounded-lg border border-border bg-muted/30 p-3 font-mono text-xs text-subtle-foreground">
        {`${request.method} ${request.url}\n`}
        {Object.entries(request.headers)
          .map(([k, v]) => `${k}: ${v}`)
          .join("\n")}
        {request.body === null || request.body === undefined
          ? ""
          : `\n\n${JSON.stringify(request.body, null, 2)}`}
      </pre>
    </div>
  );
}
