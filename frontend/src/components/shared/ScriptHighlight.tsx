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

export interface ScriptHighlightProps {
  text: string;
  lookup: MentionLookup;
  className?: string;
  renderAfterLine?: (sourceLine: number) => ReactNode;
}

function SpeechToken({ token }: { token: Extract<Token, { kind: "speech" }> }) {
  const { t } = useTranslation("dashboard");
  const palette = assetColor(token.speakerKind);
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
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- 记号是一行文本按顺序切出的只读片段，位置就是它的身份，片段不带状态
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
