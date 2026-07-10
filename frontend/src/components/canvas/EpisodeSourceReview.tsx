import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Anchor, ChevronDown, Sparkles } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import type { EpisodeMeta } from "@/types";

/**
 * 已选集但剧本未生成时的画布视图：呈现分集拆分结果供审阅——
 * 源文切片全文 + 拆分元信息（边界、节拍、尾钩子），CTA 唤起智能体起草剧本。
 * 适用 narration/drama 全部生成路径；ad 恒单集无源文切片，由 StudioCanvasRouter 排除。
 */

// ---------------------------------------------------------------------------
// 标题区：E 徽标 + 标题 + 状态 chip + 源文元信息 + CTA
// ---------------------------------------------------------------------------

function EpisodeHeader({
  episode,
  meta,
  onStart,
}: {
  episode: number;
  meta: EpisodeMeta | undefined;
  onStart: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const r = meta?.source_range;
  const chars = r?.start != null && r?.end != null ? r.end - r.start : null;
  const sourceName = r?.source_file?.replace(/^source\//, "");
  return (
    <header className="flex items-start gap-3.5">
      <div
        className="num grid h-11 w-11 shrink-0 place-items-center rounded-lg text-[13px] font-bold"
        style={{
          background: "linear-gradient(135deg, var(--color-accent) 0%, oklch(0.45 0.12 285) 100%)",
          color: "oklch(0.14 0 0)",
          boxShadow:
            "inset 0 1px 0 oklch(1 0 0 / 0.25), 0 0 0 1px oklch(1 0 0 / 0.12), 0 4px 12px -4px var(--color-accent-glow)",
        }}
      >
        E{episode}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2.5">
          <h2 className="truncate text-[17px] font-semibold leading-tight" style={{ color: "var(--color-text)" }}>
            {meta?.title ?? ""}
          </h2>
          <span
            className="shrink-0 rounded-full px-2.5 py-0.5 text-[10.5px]"
            style={{
              color: "var(--color-warm)",
              background: "var(--color-warm-soft)",
              border: "1px solid var(--color-warm-ring)",
            }}
          >
            {t("episode_workspace_script_pending")}
          </span>
        </div>
        <div className="mt-1 flex items-center gap-1.5 text-[11px]" style={{ color: "var(--color-text-4)" }}>
          {sourceName ? <span className="truncate">{sourceName}</span> : null}
          {r?.start != null && r?.end != null ? (
            <>
              <span aria-hidden>·</span>
              <span className="num shrink-0">
                {r.start.toLocaleString()}–{r.end.toLocaleString()}
              </span>
            </>
          ) : null}
          {chars != null ? (
            <>
              <span aria-hidden>·</span>
              <span className="num shrink-0">
                {t("episode_workspace_chars_approx", { count: chars.toLocaleString() })}
              </span>
            </>
          ) : null}
        </div>
      </div>
      <button
        type="button"
        onClick={onStart}
        className="arc-btn-primary focus-ring mt-0.5 inline-flex shrink-0 items-center gap-1.5 rounded-lg px-4 py-2 text-[12.5px] font-semibold"
      >
        <Sparkles className="h-3.5 w-3.5" aria-hidden />
        {t("episode_workspace_start_cta", { episode })}
      </button>
    </header>
  );
}

// ---------------------------------------------------------------------------
// 可折叠导览区：节拍横排卡 + 尾钩子条
// ---------------------------------------------------------------------------

function GuideSection({ meta }: { meta: EpisodeMeta | undefined }) {
  const { t } = useTranslation("dashboard");
  const [collapsed, setCollapsed] = useState(false);
  const beats = meta?.outline?.story_beats ?? [];
  const hook = meta?.hook;
  if (beats.length === 0 && !hook) return null;

  const summary = [
    beats.length > 0 ? t("episode_workspace_guide_beats", { count: beats.length }) : null,
    hook ? t("episode_workspace_guide_hook") : null,
  ]
    .filter(Boolean)
    .join(" · ");

  // 开关做成容器卡的 header 行：折叠时整卡收成一行，展开时内容都在同一个框内，
  // 「开关控制的是这个框」的对应关系可见
  return (
    <section
      className="mt-4 overflow-hidden rounded-xl"
      style={{ background: "oklch(0.21 0.012 265 / 0.35)", border: "1px solid var(--color-hairline)" }}
    >
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
        className="focus-ring flex w-full items-center gap-2 px-4 py-2.5 text-left text-[11.5px] font-semibold tracking-wide transition-colors hover:bg-[oklch(1_0_0_/_0.03)]"
        style={{
          color: "var(--color-text-3)",
          borderBottom: collapsed ? "none" : "1px solid var(--color-hairline-soft)",
        }}
      >
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${collapsed ? "-rotate-90" : ""}`}
          aria-hidden
        />
        {t("episode_workspace_guide_title")}
        <span className="font-normal" style={{ color: "var(--color-text-4)" }}>
          {summary}
        </span>
        <span className="ml-auto shrink-0 font-normal" style={{ color: "var(--color-text-4)" }}>
          {collapsed ? t("episode_workspace_guide_expand") : t("episode_workspace_guide_collapse")}
        </span>
      </button>

      {!collapsed && (
        <div className="space-y-2.5 px-4 pb-4 pt-3">
          {beats.length > 0 ? (
            <div
              className="grid gap-2.5"
              style={{ gridTemplateColumns: `repeat(${Math.min(beats.length, 4)}, 1fr)` }}
            >
              {beats.map((b, i) => (
                <div
                  key={i}
                  className="rounded-lg px-3.5 py-3"
                  style={{ background: "oklch(0.24 0.012 265 / 0.55)", border: "1px solid var(--color-hairline-soft)" }}
                >
                  <span className="num text-[15px] font-bold" style={{ color: "var(--color-accent-2)" }}>
                    {i + 1}
                  </span>
                  <p className="mt-1 text-[12px] leading-[1.6]" style={{ color: "var(--color-text-2)" }}>
                    {b}
                  </p>
                </div>
              ))}
            </div>
          ) : null}

          {hook ? (
            <div
              className="flex items-start gap-2.5 rounded-lg px-3.5 py-3"
              style={{ background: "var(--color-accent-dim)", border: "1px solid var(--color-accent-soft)" }}
            >
              <Anchor className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: "var(--color-accent-2)" }} aria-hidden />
              <p className="text-[12.5px] leading-[1.7]" style={{ color: "var(--color-text-2)" }}>
                <span className="mr-2 font-semibold" style={{ color: "var(--color-accent-2)" }}>
                  {t("episode_workspace_guide_hook")}
                </span>
                {hook}
              </p>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 入口：顶栏导览（可折叠） + 全宽居中阅读列
// ---------------------------------------------------------------------------

export function EpisodeSourceReview({
  projectName,
  episode,
  episodes,
}: {
  projectName: string;
  episode: number;
  episodes: EpisodeMeta[];
}) {
  const { t } = useTranslation("dashboard");
  // 取到的切片带上归属 key，loading 由 key 是否匹配派生（避免 effect 内同步 setState）
  const [fetched, setFetched] = useState<{ key: string; text: string | null } | null>(null);

  const meta = episodes.find((e) => e.episode === episode);

  const fetchKey = `${projectName}::${episode}`;
  useEffect(() => {
    let disposed = false;
    void API.getSourceContent(projectName, `episode_${episode}.txt`)
      .catch(() => null)
      .then((text) => {
        if (disposed) return;
        setFetched({ key: `${projectName}::${episode}`, text });
      });
    return () => {
      disposed = true;
    };
  }, [projectName, episode]);

  const loading = fetched?.key !== fetchKey;
  const text = loading ? null : fetched.text;

  const handleStart = useCallback(() => {
    // 经 store.input 投递一次性预填文本，AgentCopilot 消费后写入输入框；
    // 只填不发送，已有会话时不切换、不新建
    useAssistantStore.getState().setInput(t("episode_workspace_prefill_script", { episode }));
    useAppStore.getState().setAssistantPanelOpen(true);
  }, [episode, t]);

  return (
    <div className="flex h-full flex-col p-6">
      <div className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col">
        <EpisodeHeader episode={episode} meta={meta} onStart={handleStart} />
        <GuideSection key={episode} meta={meta} />

        <div className="mt-4 flex min-h-0 flex-1 flex-col">
          <div
            className="min-h-0 flex-1 overflow-y-auto rounded-2xl px-12 py-9"
            style={{
              background: "linear-gradient(180deg, oklch(0.215 0.011 265 / 0.75), oklch(0.195 0.010 265 / 0.75))",
              border: "1px solid var(--color-hairline)",
              boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04)",
            }}
          >
            {loading ? (
              <p className="text-center text-[13px]" style={{ color: "var(--color-text-4)" }}>
                {t("episode_workspace_source_loading")}
              </p>
            ) : text ? (
              <p
                className="mx-auto max-w-[66ch] whitespace-pre-wrap pb-10 text-[14px] leading-[2]"
                style={{ color: "var(--color-text-2)", textAlign: "justify" }}
              >
                {text}
              </p>
            ) : (
              <p className="text-center text-[13px]" style={{ color: "var(--color-text-4)" }}>
                {t("episode_workspace_source_missing", { episode })}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
