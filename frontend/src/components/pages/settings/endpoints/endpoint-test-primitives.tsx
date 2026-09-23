import type { ReactNode } from "react";
import type { PreviewedRequest } from "@/types";
import { LABEL_CLS } from "./endpoint-form-primitives";

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
    <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 p-3.5">
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-medium text-text">{title}</span>
        {badge && <span className="text-[11px] text-warm-bright/90">{badge}</span>}
      </div>
      <p className="mb-2.5 mt-0.5 text-[12px] leading-[1.55] text-text-3">{desc}</p>
      {children}
    </div>
  );
}

/** 渲染并脱敏后的一节请求，按它真发出去的样子逐行摆开。 */
export function RequestPreview({ label, request }: { label: string; request: PreviewedRequest }) {
  return (
    <div>
      <span className={LABEL_CLS}>{label}</span>
      <pre className="max-h-96 overflow-auto rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 p-3 font-mono text-[11.5px] leading-[1.6] text-text-2">
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
