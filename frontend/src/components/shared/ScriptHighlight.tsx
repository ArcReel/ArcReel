import { Fragment, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { assetColor } from "@/components/canvas/reference/asset-colors";
import {
  toScriptLines,
  type MentionLookup,
  type ScriptLine,
  type Token,
} from "@/hooks/useUnitPromptHighlight";

/**
 * Read-only rendering of a unit body, laid out the way the parser reads it.
 *
 * Description lines sit flush left; the lines the parser recognized as utterances
 * carry a tinted rule in the speaker's asset color. A writer can therefore see at a
 * glance which of their lines became dialogue and which stayed plain description.
 *
 * It takes only text plus the asset lookup: strictness lives upstream in whoever
 * parses and validates the body, never in how it is drawn, so any caller holding a
 * body and an asset table can render it.
 */
export interface ScriptHighlightProps {
  text: string;
  /** Asset name → kind, for mention coloring. Memoize to keep tokenization stable. */
  lookup: MentionLookup;
  className?: string;
  /**
   * Optional per-line annotation slot (e.g. inline violation callouts). Called once per
   * raw source line, after its rendered `ScriptLine`. Stays domain-agnostic: this
   * component knows nothing about "violations", only where source lines end.
   */
  renderAfterLine?: (sourceLine: number) => ReactNode;
}

function SpeechToken({ token }: { token: Extract<Token, { kind: "speech" }> }) {
  const { t } = useTranslation("dashboard");
  // Voiceover has no speaker: `speakerKind` is always unknown, sharing the unregistered-speaker color tier.
  const palette = assetColor(token.speakerKind);
  // Preserve marker text verbatim, including `@[名称]` and colons; add only backgrounds and speaker labels so preview matches
  // the author's line. Rewriting would make it hard to verify whether the fragment was recognized as dialogue.
  return (
    <span
      className={`rounded-sm bg-foreground/5 ${token.speaker ? palette.textClass : "text-foreground"}`}
      title={token.speaker || t("script_highlight_voiceover")}
    >
      {token.text}
    </span>
  );
}

function renderTokens(tokens: Token[], keyPrefix: string) {
  return tokens.map((tk, i) => {
    if (tk.kind === "speech") {
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- Markers are ordered read-only fragments of one text line; position is identity and fragments carry no state
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
    return <span key={`${keyPrefix}-${i}`}>{tk.text}</span>;
  });
}

function LineRow({ line, index }: { line: ScriptLine; index: number }) {
  const { t } = useTranslation("dashboard");

  if (line.kind === "dialogue") {
    const palette = assetColor(line.speakerKind);
    return (
      <div
        className={`flex items-baseline gap-2 border-l-2 py-0.5 pl-2.5 ${palette.borderClass} bg-foreground/3`}
      >
        <span
          translate="no"
          className={`shrink-0 rounded-sm px-1 ${palette.textClass} ${palette.bgClass}`}
        >
          {line.speaker}
        </span>
        <span className="min-w-0 flex-1 break-words text-foreground">{line.text}</span>
      </div>
    );
  }

  if (line.kind === "voiceover") {
    return (
      <div className="flex items-baseline gap-2 border-l-2 border-border bg-foreground/3 py-0.5 pl-2.5">
        <span className="shrink-0 rounded-sm bg-foreground/5 px-1 text-muted-foreground">
          {t("script_highlight_voiceover")}
        </span>
        <span className="min-w-0 flex-1 break-words text-foreground">{line.text}</span>
      </div>
    );
  }

  return (
    <div className="break-words text-subtle-foreground">
      {line.tokens.length > 0 ? renderTokens(line.tokens, `t${index}`) : " "}
    </div>
  );
}

export function ScriptHighlight({ text, lookup, className, renderAfterLine }: ScriptHighlightProps) {
  const lines = useMemo(() => toScriptLines(text, lookup), [text, lookup]);

  return (
    <div className={cn("font-mono text-xs leading-6", className)}>
      {lines.map((line) => (
        <Fragment key={line.sourceLine}>
          <LineRow line={line} index={line.sourceLine} />
          {renderAfterLine?.(line.sourceLine)}
        </Fragment>
      ))}
    </div>
  );
}
