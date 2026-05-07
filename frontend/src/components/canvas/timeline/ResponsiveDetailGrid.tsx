import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface ResponsiveDetailGridProps {
  left: React.ReactNode;
  mid: React.ReactNode;
  right: React.ReactNode;
}

/**
 * 三栏分屏容器，按容器宽度自适应：
 *   - >= 980px：经典三栏（left | mid | right）
 *   - 640-979px：mid + right 同屏；left 通过抽屉式 toggle 显示
 *   - < 640px：单栏 + 顶部 tab 选择
 */
export function ResponsiveDetailGrid({ left, mid, right }: ResponsiveDetailGridProps) {
  const { t } = useTranslation("dashboard");
  const [width, setWidth] = useState(0);
  const observerRef = useRef<ResizeObserver | null>(null);

  const setRef = useCallback((el: HTMLDivElement | null) => {
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width);
    });
    ro.observe(el);
    observerRef.current = ro;
  }, []);

  const narrow = width > 0 && width < 980;
  const tight = width > 0 && width < 640;

  const [activeTab, setActiveTab] = useState<"left" | "mid" | "right">("mid");

  if (!narrow) {
    return (
      <div
        ref={setRef}
        className="grid min-h-0 flex-1 overflow-hidden"
        style={{ gridTemplateColumns: "220px minmax(280px, 1fr) 340px" }}
      >
        <div
          className="min-h-0 overflow-hidden"
          style={{ borderRight: "1px solid var(--color-hairline-soft)" }}
        >
          {left}
        </div>
        <div className="min-h-0 overflow-hidden">{mid}</div>
        <div
          className="min-h-0 overflow-hidden"
          style={{ borderLeft: "1px solid var(--color-hairline-soft)" }}
        >
          {right}
        </div>
      </div>
    );
  }

  if (tight) {
    const tabs: Array<{ k: "left" | "mid" | "right"; label: string }> = [
      { k: "left", label: t("detail_tab_left") },
      { k: "mid", label: t("detail_tab_mid") },
      { k: "right", label: t("detail_tab_right") },
    ];
    return (
      <div ref={setRef} className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div
          className="flex gap-0.5 px-3 py-2"
          style={{
            borderBottom: "1px solid var(--color-hairline-soft)",
            background: "oklch(0.20 0.011 265 / 0.3)",
          }}
        >
          {tabs.map((x) => {
            const active = activeTab === x.k;
            return (
              <button
                key={x.k}
                type="button"
                onClick={() => setActiveTab(x.k)}
                className="rounded-md px-3 py-1.5 text-[11.5px] font-medium transition-colors focus-ring"
                style={{
                  color: active ? "var(--color-accent-2)" : "var(--color-text-3)",
                  background: active ? "var(--color-accent-dim)" : "transparent",
                  border: "1px solid " + (active ? "var(--color-accent-soft)" : "transparent"),
                }}
              >
                {x.label}
              </button>
            );
          })}
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {activeTab === "left" && left}
          {activeTab === "mid" && mid}
          {activeTab === "right" && right}
        </div>
      </div>
    );
  }

  // narrow (640-979)
  const leftOpen = activeTab === "left";
  return (
    <div ref={setRef} className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div
        className="flex items-center gap-1.5 px-3 py-1.5"
        style={{
          borderBottom: "1px solid var(--color-hairline-soft)",
          background: "oklch(0.20 0.011 265 / 0.3)",
        }}
      >
        <button
          type="button"
          onClick={() => setActiveTab(leftOpen ? "mid" : "left")}
          className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] font-medium focus-ring"
          style={{
            color: leftOpen ? "var(--color-accent-2)" : "var(--color-text-3)",
            background: leftOpen ? "var(--color-accent-dim)" : "oklch(0.22 0.011 265 / 0.5)",
            border: "1px solid " + (leftOpen ? "var(--color-accent-soft)" : "var(--color-hairline-soft)"),
          }}
        >
          <span aria-hidden="true">{leftOpen ? "×" : "☰"}</span>
          <span>{t("detail_drawer_left_label")}</span>
        </button>
      </div>
      <div
        className="grid min-h-0 flex-1 overflow-hidden"
        style={{
          gridTemplateColumns: leftOpen ? "260px minmax(0,1fr)" : "minmax(0,1fr) 300px",
        }}
      >
        {leftOpen ? (
          <>
            <div
              className="min-h-0 overflow-hidden"
              style={{ borderRight: "1px solid var(--color-hairline-soft)" }}
            >
              {left}
            </div>
            <div className="min-h-0 overflow-hidden">{mid}</div>
          </>
        ) : (
          <>
            <div className="min-h-0 overflow-hidden">{mid}</div>
            <div
              className="min-h-0 overflow-hidden"
              style={{ borderLeft: "1px solid var(--color-hairline-soft)" }}
            >
              {right}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
