// PROTOTYPE — throwaway code for issue #1276（#1272 决议 7 并入：能力覆盖设置界面）。
// 自定义供应商模型行内的能力覆盖三态控件，?capproto=a|b 切换：
//   a — segmented 三段按钮
//   b — select 下拉
// 三态语义：跟随系统判定（内联显示判定值）/ 强制开启 / 强制关闭。首批仅 last_frame。
import { useState, type ReactNode } from "react";
import { PrototypeBar, PROTOTYPE_ENABLED, usePrototypeVariant } from "./PrototypeBar";

type OverrideState = "follow" | "on" | "off";

/** mock 系统判定：按 key 交替，让列表里同时能看到「判定支持 / 判定不支持」两种内联呈现 */
function mockDetected(modelKey: string): boolean {
  let h = 0;
  for (const c of modelKey) h = (h * 31 + c.charCodeAt(0)) | 0;
  return (h & 1) === 0;
}

const OVERRIDE_BADGE = (
  <span
    className="num rounded px-1.5 py-0.5 text-[9px] font-bold uppercase"
    style={{
      letterSpacing: "0.5px",
      color: "oklch(0.85 0.13 75)",
      background: "oklch(0.30 0.10 75 / 0.30)",
      border: "1px solid oklch(0.45 0.13 75 / 0.40)",
    }}
  >
    已覆盖
  </span>
);

interface CapabilityOverridePrototype {
  /** 在 video 模型行（durations 行下方）渲染的能力覆盖行 */
  renderRow: (modelKey: string) => ReactNode;
  /** 浮动切换条，渲染一次 */
  chrome: ReactNode;
}

export function useCapabilityOverridePrototype(): CapabilityOverridePrototype {
  const variant = usePrototypeVariant("capproto", ["a", "b"]);
  const [overrides, setOverrides] = useState<Record<string, OverrideState>>({});

  if (!PROTOTYPE_ENABLED) return { renderRow: () => null, chrome: null };

  const chrome = (
    <PrototypeBar
      param="capproto"
      variants={["a", "b"]}
      labels={{ a: "segmented 三段", b: "select 下拉" }}
    />
  );

  const renderRow = (modelKey: string) => {
    const detected = mockDetected(modelKey);
    const state = overrides[modelKey] ?? "follow";
    const set = (s: OverrideState) => setOverrides((prev) => ({ ...prev, [modelKey]: s }));
    const detectedLabel = detected ? "支持" : "不支持";
    const overridden = state !== "follow";

    const segBtn = (s: OverrideState, label: ReactNode, title?: string) => {
      const active = state === s;
      return (
        <button
          key={s}
          type="button"
          onClick={() => set(s)}
          aria-pressed={active}
          title={title}
          className="focus-ring px-2 py-1 text-[10.5px] font-semibold transition-colors first:rounded-l-[6px] last:rounded-r-[6px]"
          style={{
            color: active ? "var(--color-accent-2)" : "var(--color-text-4)",
            background: active ? "var(--color-accent-dim)" : "var(--color-bg-grad-a)",
            border: `1px solid ${active ? "var(--color-accent-soft)" : "var(--color-hairline)"}`,
            marginLeft: -1,
          }}
        >
          {label}
        </button>
      );
    };

    return (
      <div className="mt-2 flex flex-wrap items-center gap-2 pl-6">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-3 whitespace-nowrap">
          尾帧 last_frame
        </span>

        {variant === "a" ? (
          <div className="flex items-center">
            {segBtn(
              "follow",
              <span>
                跟随判定
                <span className="ml-1 opacity-70">·{detectedLabel}</span>
              </span>,
              `系统按 model id 匹配判定该模型${detectedLabel}尾帧；留空跟随判定`,
            )}
            {segBtn("on", "强制开", "无视系统判定，视为支持尾帧")}
            {segBtn("off", "强制关", "无视系统判定，视为不支持尾帧")}
          </div>
        ) : (
          <select
            value={state}
            onChange={(e) => set(e.target.value as OverrideState)}
            aria-label="尾帧能力覆盖"
            className="rounded-[5px] border border-hairline bg-bg-grad-a/55 px-1.5 py-0.5 text-[11px] text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <option value="follow">跟随系统判定（当前：{detectedLabel}）</option>
            <option value="on">强制开启</option>
            <option value="off">强制关闭</option>
          </select>
        )}

        {overridden && OVERRIDE_BADGE}
        {overridden && (
          <span className="text-[10px]" style={{ color: "var(--color-text-4)" }}>
            生效值：{state === "on" ? "支持" : "不支持"}（判定：{detectedLabel}）
          </span>
        )}
      </div>
    );
  };

  return { renderRow, chrome };
}
