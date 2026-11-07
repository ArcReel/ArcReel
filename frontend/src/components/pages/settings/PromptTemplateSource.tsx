import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { PromptTemplateMeta, PromptTemplatePartial } from "@/types";
import { LockBadge, SOURCE_BLOCK_CLS } from "./promptTemplateShared";

type SourceProps = {
  text: string;
  template: PromptTemplateMeta;
  partials: PromptTemplatePartial[];
  /** Click expanded fragment names to open fragment details. */
  onOpenPartial: (name: string) => void;
};

const MARKER_PATTERN = /(\{#[\s\S]*?#\}|\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\})/;
const SLOT_PATTERN = /^[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*$/;
// ponytail: expand direct fragment calls only; preserve filtered expressions as source, introducing parsers after syntax extensions.
const PARTIAL_PATTERN =
  /^\{\{-?\s*(?<kind>partial|variant)\s*\(\s*(["'])(?<name>[^"']+)\2\s*(?:,\s*(?<axis>[a-zA-Z_]\w*))?[^()]*\)\s*-?\}\}$/;

/** Display template syntax/fragment sources without executing Jinja or filling project data. */
export function PromptTemplateSource(props: SourceProps) {
  const { t } = useTranslation("dashboard");
  return (
    // Bodies may lack fragment buttons; code blocks must focus for keyboard horizontal scrolling of unwrappable strings.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Read-only scroll regions require keyboard focus for scrolling
    <div tabIndex={0} role="region" aria-label={t("prompt_templates_source")} className={SOURCE_BLOCK_CLS}>
      <SourceContent {...props} />
    </div>
  );
}

type SourceNode =
  | string
  | {
      opening: string;
      condition: string;
      children: SourceNode[];
      /** `else` / `elif` markers and following source before `endif` are outside Only when label coverage. */
      alternate: SourceNode[];
      closing: string;
    };

function sourceNodes(text: string): SourceNode[] {
  const root: SourceNode[] = [];
  const stack: Exclude<SourceNode, string>[] = [];
  for (const token of text.split(MARKER_PATTERN)) {
    const current = stack.at(-1);
    const children = current ? (current.alternate.length ? current.alternate : current.children) : root;
    const opening = /^\{%-?\s*if\s+([\s\S]*?)\s*-?%\}$/.exec(token);
    if (opening) {
      const node = { opening: token, condition: opening[1], children: [], alternate: [], closing: "" };
      children.push(node);
      stack.push(node);
    } else if (current && !current.alternate.length && /^\{%-?\s*(?:else|elif\b)[\s\S]*%\}$/.test(token)) {
      current.alternate.push(token);
    } else if (/^\{%-?\s*endif\s*-?%\}$/.test(token) && stack.length) {
      stack.pop()!.closing = token;
    } else {
      children.push(token);
    }
  }
  return root;
}

function SourceContent({ text, ...props }: SourceProps) {
  return <SourceNodes nodes={sourceNodes(text)} {...props} />;
}

function SourceNodes({ nodes, ...props }: Omit<SourceProps, "text"> & { nodes: SourceNode[] }) {
  const { t } = useTranslation("dashboard");
  return nodes.map((part, index) => {
    if (typeof part !== "string") {
      const label = SLOT_PATTERN.test(part.condition)
        ? t("prompt_templates_optional_slot", { slot: part.condition })
        : t("prompt_templates_optional_condition", { condition: part.condition });
      return (
        // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- nodes parse readonly source once; template changes fully remount, without reorder/add/remove, so positions are identities
        <span key={index} className="my-2 block border-l-2 border-border pl-3">
          <span role="group" aria-label={label}>
            <span className="mb-1 block font-sans text-xs text-muted-foreground">{label}</span>
            <mark className="bg-muted text-muted-foreground">{part.opening}</mark>
            <SourceNodes nodes={part.children} {...props} />
          </span>
          <SourceNodes nodes={part.alternate} {...props} />
          <mark className="bg-muted text-muted-foreground">{part.closing}</mark>
        </span>
      );
    }
    const reference = PARTIAL_PATTERN.exec(part)?.groups;
    if (reference) {
      return (
        // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- nodes parse readonly source once; template changes fully remount, without reorder/add/remove, so positions are identities
        <PartialReference
          key={index}
          marker={part}
          name={reference.name}
          axis={reference.kind === "variant" ? reference.axis : undefined}
          {...props}
        />
      );
    }
    if (part.startsWith("{{")) {
      const slot = part.slice(2, -2).trim();
      const description = SLOT_PATTERN.test(slot)
        ? (props.template.slots[slot] ?? props.template.slots[slot.split(".")[0]])
        : undefined;
      if (description !== undefined) {
        return (
          <span
            key={index}
            title={description}
            className="rounded-sm bg-primary/12 px-1 text-primary"
          >
            {slot}
          </span>
        );
      }
    }
    return /^\{[{%#]/.test(part) ? (
      // react-doctor-disable-next-line react-doctor/no-array-index-as-key -- nodes parse readonly source once; template changes fully remount, without reorder/add/remove, so positions are identities
      <mark key={index} className="rounded-sm bg-muted px-0.5 text-muted-foreground">
        {part}
      </mark>
    ) : (
      part
    );
  });
}

function PartialReference({
  marker,
  name,
  axis,
  ...props
}: Omit<SourceProps, "text"> & {
  marker: string;
  name: string;
  axis?: string;
}) {
  const { t } = useTranslation("dashboard");
  const [expanded, setExpanded] = useState(false);
  const { template, partials, onOpenPartial } = props;
  const [value, setValue] = useState(axis ? template.applies_to[axis]?.[0] : undefined);
  const partialName = axis ? `${name}/${value}` : name;
  const partial = partials.find((item) => item.name === partialName);
  const axisLabel = axis ? t(`prompt_templates_axis_${axis}`, { defaultValue: axis }) : "";

  if (!partial) return <mark className="bg-warn/15 text-warn">{marker}</mark>;

  return (
    <span>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="rounded-sm bg-warn/15 px-1 text-left text-warn hover:underline focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        {marker}
      </button>
      {expanded && (
        <span className="my-2 block rounded-sm border-l-2 border-warn/30 bg-warn/5 py-2 pl-3 pr-2">
          <span className="mb-1 flex flex-wrap items-center gap-2 font-sans text-xs whitespace-normal text-warn">
            <button
              type="button"
              title={t("prompt_templates_open_partial")}
              onClick={() => onOpenPartial(partialName)}
              className="rounded-xs underline decoration-warn/30 underline-offset-2 hover:decoration-warn focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              {partialName}
            </button>
            {partial.protected && <LockBadge />}
            {partialName.startsWith("shared/") && (
              <span className="text-muted-foreground">
                {t("prompt_templates_referenced_count", { count: partial.referenced_by.length })}
              </span>
            )}
            {axis && (
              <span className="inline-flex items-center gap-2">
                {axisLabel}
                <Select value={value ?? null} onValueChange={(next) => next && setValue(next)}>
                  <SelectTrigger size="sm" aria-label={axisLabel}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {template.applies_to[axis].map((option) => (
                      <SelectItem key={option} value={option}>
                        {option}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </span>
            )}
          </span>
          {partial.source.trim() ? (
            <SourceContent key={partialName} text={partial.source} {...props} />
          ) : (
            <span className="italic text-muted-foreground">{t("prompt_templates_partial_blank")}</span>
          )}
        </span>
      )}
    </span>
  );
}
