import { Fragment, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { assetColor } from "@/components/canvas/reference/asset-colors";
import {
  toScriptLines,
  type MentionLookup,
  type ScriptLine,
  type Token,
} from "@/hooks/useShotPromptHighlight";

/**
 * Read-only rendering of a shot script, laid out the way the parser reads it.
 *
 * Indentation is information, not decoration: shot headers sit flush left, every
 * line that belongs to a shot is indented under it, and the lines the parser
 * recognized as utterances carry a tinted rule in the speaker's asset color.
 * A writer can therefore see at a glance which of their lines became dialogue and
 * which stayed plain description.
 *
 * It takes only text plus the asset lookup: strictness lives upstream in whoever
 * parses and validates the script, never in how the script is drawn, so any caller
 * holding a script and an asset table can render it.
 */
export interface ScriptHighlightProps {
  text: string;
  /** Asset name → kind, for mention coloring. Memoize to keep tokenization stable. */
  lookup: MentionLookup;
  className?: string;
  /**
   * Optional per-line annotation slot (e.g. inline violation callouts). Called once per
   * raw source line, after the last rendered `ScriptLine` sharing that `sourceLine` — a
   * shot-header-plus-dialogue physical line yields two `ScriptLine`s, so the callback
   * fires after the second, not the first, to avoid splitting one physical line in two.
   * Stays domain-agnostic: this component knows nothing about "violations", only where
   * source lines end.
   */
  renderAfterLine?: (sourceLine: number) => ReactNode;
}

function SpeechToken({ token }: { token: Extract<Token, { kind: "speech" }> }) {
  const { t } = useTranslation("dashboard");
  // 画外音没有说话人，`speakerKind` 恒为 unknown，配色因此与未登记说话人同档。
  const palette = assetColor(token.speakerKind);
  // 记号原文逐字保留（含 `@[名称]` 与冒号），只加底色与说话人标签：预览要和作者写的
  // 那一行对得上，改写会让「这段被认成台词了吗」难以核对。
  return (
    <span
      className={`rounded-sm bg-[oklch(1_0_0_/_0.06)] ${token.speaker ? palette.textClass : "text-[var(--color-text)]"}`}
      title={token.speaker || t("script_highlight_voiceover")}
    >
      {token.text}
    </span>
  );
}

function renderTokens(tokens: Token[], keyPrefix: string) {
  return tokens.map((tk, i) => {
    if (tk.kind === "speech") {
      return <SpeechToken key={`${keyPrefix}-${i}`} token={tk} />;
    }
    if (tk.kind === "mention") {
      const palette = assetColor(tk.assetKind);
      return (
        <span key={`${keyPrefix}-${i}`} className={`rounded-sm ${palette.textClass} ${palette.bgClass}`}>
          {tk.text}
        </span>
      );
    }
    if (tk.kind === "shot_header") {
      return (
        <span key={`${keyPrefix}-${i}`} className="font-semibold text-indigo-300">
          {tk.text}
        </span>
      );
    }
    return <span key={`${keyPrefix}-${i}`}>{tk.text}</span>;
  });
}

function LineRow({ line, index }: { line: ScriptLine; index: number }) {
  const { t } = useTranslation("dashboard");

  if (line.kind === "shot_header") {
    return (
      <div className="mt-2.5 flex items-baseline gap-2 first:mt-0">
        <span
          translate="no"
          className="shrink-0 font-semibold text-indigo-300"
        >
          {line.header}
        </span>
        <span className="min-w-0 flex-1 break-words">{renderTokens(line.tokens, `h${index}`)}</span>
      </div>
    );
  }

  if (line.kind === "dialogue") {
    const palette = assetColor(line.speakerKind);
    return (
      <div
        className={`ml-4 flex items-baseline gap-2 border-l-2 py-0.5 pl-2.5 ${palette.borderClass} bg-[oklch(1_0_0_/_0.03)]`}
      >
        <span
          translate="no"
          className={`shrink-0 rounded-sm px-1 ${palette.textClass} ${palette.bgClass}`}
        >
          {line.speaker}
        </span>
        <span className="min-w-0 flex-1 break-words text-[var(--color-text)]">{line.text}</span>
      </div>
    );
  }

  if (line.kind === "voiceover") {
    return (
      <div className="ml-4 flex items-baseline gap-2 border-l-2 border-[var(--color-hairline)] bg-[oklch(1_0_0_/_0.03)] py-0.5 pl-2.5">
        <span className="shrink-0 rounded-sm bg-[oklch(1_0_0_/_0.06)] px-1 text-[var(--color-text-3)]">
          {t("script_highlight_voiceover")}
        </span>
        <span className="min-w-0 flex-1 break-words text-[var(--color-text)]">{line.text}</span>
      </div>
    );
  }

  return (
    <div className="ml-4 break-words pl-2.5 text-[var(--color-text-2)]">
      {line.tokens.length > 0 ? renderTokens(line.tokens, `t${index}`) : " "}
    </div>
  );
}

export function ScriptHighlight({ text, lookup, className, renderAfterLine }: ScriptHighlightProps) {
  const lines = useMemo(() => toScriptLines(text, lookup), [text, lookup]);

  return (
    <div className={`font-mono text-[12.5px] leading-6 ${className ?? ""}`}>
      {lines.map((line, i) => {
        const isLastOfSourceLine = i === lines.length - 1 || lines[i + 1].sourceLine !== line.sourceLine;
        return (
          <Fragment key={i}>
            <LineRow line={line} index={i} />
            {isLastOfSourceLine ? renderAfterLine?.(line.sourceLine) : null}
          </Fragment>
        );
      })}
    </div>
  );
}
