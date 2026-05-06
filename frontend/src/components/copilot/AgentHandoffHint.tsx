import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkles, X } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { UI_LAYERS } from "@/utils/ui-layers";

const AUTO_DISMISS_MS = 6500;

// 助手面板宽度（与 StudioLayout 中保持一致）
const ASSISTANT_PANEL_WIDTH = 505;

interface AgentHandoffHintProps {
  /** 当 overview 在当前会话内首次从 null → 有值，外部把这个 key 递增触发一次引导。 */
  triggerKey: number;
  /** 用 sessionStorage 防 reload 后重复触发；按项目隔离。 */
  storageScope: string;
}

export function AgentHandoffHint({ triggerKey, storageScope }: AgentHandoffHintProps) {
  const { t } = useTranslation("dashboard");
  // 不订阅 assistantPanelOpen —— 避免触发时调用 setAssistantPanelOpen(true) 让 effect cleanup 清掉计时器
  const assistantPanelOpen = useAppStore((s) => s.assistantPanelOpen);

  const [visible, setVisible] = useState(false);
  const [leaving, setLeaving] = useState(false);
  // 用 "<scope>:<triggerKey>" 复合 key 去重，避免切项目后新 triggerKey 撞到上个项目最后值导致不显示
  const lastSeenComposite = useRef<string>("");
  const dismissTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fadeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const sessionKey = `arc:agent-handoff:${storageScope}`;

  const clearTimers = useCallback(() => {
    if (dismissTimer.current) {
      clearTimeout(dismissTimer.current);
      dismissTimer.current = null;
    }
    if (fadeTimer.current) {
      clearTimeout(fadeTimer.current);
      fadeTimer.current = null;
    }
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (triggerKey <= 0) return;
    const composite = `${sessionKey}:${triggerKey}`;
    if (composite === lastSeenComposite.current) return;
    lastSeenComposite.current = composite;
    try {
      if (sessionStorage.getItem(sessionKey) === "1") return;
      sessionStorage.setItem(sessionKey, "1");
    } catch {
      // sessionStorage 不可用时仍允许触发一次
    }
    setLeaving(false);
    setVisible(true);
    // 通过 getState() 直接读 + 写，不订阅，避免重新触发 effect
    const store = useAppStore.getState();
    if (!store.assistantPanelOpen) {
      store.setAssistantPanelOpen(true);
    }
    clearTimers();
    dismissTimer.current = setTimeout(() => {
      setLeaving(true);
      fadeTimer.current = setTimeout(() => setVisible(false), 320);
    }, AUTO_DISMISS_MS);
    return clearTimers;
  }, [triggerKey, sessionKey, clearTimers]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleDismiss = useCallback(() => {
    clearTimers();
    setLeaving(true);
    fadeTimer.current = setTimeout(() => setVisible(false), 280);
  }, [clearTimers]);

  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, handleDismiss]);

  if (!visible) return null;

  // 紧贴 agent 面板左边沿；面板收起时（理论上不会，触发时强制打开了）退到右上角
  const cardRight = assistantPanelOpen ? ASSISTANT_PANEL_WIDTH + 12 : 80;

  return (
    <div
      className={`pointer-events-none fixed inset-0 ${UI_LAYERS.workspaceFloating}`}
      aria-live="polite"
      role="status"
    >
      <div
        className="agent-handoff-card pointer-events-auto absolute"
        data-leaving={leaving}
        style={{
          top: 72,
          right: cardRight,
          width: 340,
          borderRadius: 14,
          padding: "16px 18px 14px",
          border: "1px solid var(--color-hairline)",
          background:
            "linear-gradient(180deg, oklch(0.22 0.014 285 / 0.96), oklch(0.18 0.011 270 / 0.94))",
          boxShadow:
            "0 28px 64px -24px oklch(0 0 0 / 0.85), 0 0 0 1px oklch(1 0 0 / 0.04), inset 0 1px 0 oklch(1 0 0 / 0.06)",
          backdropFilter: "blur(16px) saturate(1.1)",
          WebkitBackdropFilter: "blur(16px) saturate(1.1)",
          overflow: "visible",
        }}
      >
        {/* 顶部紫色发光线 */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent 6%, var(--color-accent-2) 50%, transparent 94%)",
            opacity: 0.7,
            borderTopLeftRadius: 14,
            borderTopRightRadius: 14,
          }}
        />

        {/* 内部扫光弧（不溢出） */}
        <span
          aria-hidden
          className="agent-handoff-arc pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse 60% 100% at 50% 0%, oklch(0.76 0.09 295 / 0.22) 0%, transparent 65%)",
            borderRadius: 14,
            mixBlendMode: "screen",
            overflow: "hidden",
          }}
        />

        {/* 尾巴：指向右侧 agent 面板的小三角 + 流光线 */}
        <span
          aria-hidden
          className="agent-handoff-tail-line pointer-events-none absolute"
          style={{
            top: 38,
            right: -28,
            width: 28,
            height: 1.5,
            borderRadius: 999,
            background:
              "linear-gradient(90deg, var(--color-accent-2) 0%, oklch(0.76 0.09 295 / 0.4) 100%)",
            boxShadow: "0 0 8px var(--color-accent-glow)",
          }}
        />
        <span
          aria-hidden
          className="agent-handoff-tail-arrow pointer-events-none absolute"
          style={{
            top: 32,
            right: -30,
            width: 0,
            height: 0,
            borderTop: "6px solid transparent",
            borderBottom: "6px solid transparent",
            borderLeft: "8px solid var(--color-accent-2)",
            filter: "drop-shadow(0 0 6px var(--color-accent-glow))",
          }}
        />

        <div className="relative flex items-start gap-3.5">
          {/* sparkle 球 */}
          <div className="agent-handoff-orb shrink-0">
            <span
              aria-hidden
              className="grid h-9 w-9 place-items-center rounded-xl"
              style={{
                background:
                  "linear-gradient(135deg, oklch(0.85 0.08 295), oklch(0.70 0.12 280))",
                color: "oklch(0.14 0 0)",
                boxShadow:
                  "0 6px 18px -6px var(--color-accent-glow), inset 0 1px 0 oklch(1 0 0 / 0.4)",
              }}
            >
              <Sparkles className="h-[18px] w-[18px]" strokeWidth={2.2} />
            </span>
          </div>

          <div className="min-w-0 flex-1">
            <p
              className="agent-handoff-headline display-serif text-[18px] font-semibold leading-tight tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {t("agent_handoff_headline")}
            </p>
            <p
              className="agent-handoff-subtitle mt-1 text-[12.5px] leading-relaxed"
              style={{ color: "var(--color-text-2)" }}
            >
              {t("agent_handoff_subtitle")}
            </p>
          </div>

          {/* 关闭按钮 — 改为 icon-only X，避免与下方"知道了"重复 */}
          <button
            type="button"
            onClick={handleDismiss}
            aria-label={t("agent_handoff_dismiss")}
            title={t("agent_handoff_dismiss")}
            className="focus-ring pointer-events-auto -mr-1.5 -mt-1.5 shrink-0 inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.06)]"
            style={{ color: "var(--color-text-3)" }}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
