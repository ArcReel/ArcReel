import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { PromptTemplateMeta, PromptTemplatePartial } from "@/types";

type SourceProps = {
  text: string;
  template: PromptTemplateMeta;
  partials: PromptTemplatePartial[];
};

const MARKER_PATTERN = /(\{#[\s\S]*?#\}|\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\})/;
const SLOT_PATTERN = /^[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*$/;
// ponytail: 只展开直接片段调用，带过滤器的表达式保留原文；语法扩展后再接解析器。
const PARTIAL_PATTERN =
  /^\{\{-?\s*(?<kind>partial|variant)\s*\(\s*(["'])(?<name>[^"']+)\2\s*(?:,\s*(?<axis>[a-zA-Z_]\w*))?[^()]*\)\s*-?\}\}$/;

/** 展示模版语法与片段原文，不执行 Jinja 或填充项目数据。 */
export function PromptTemplateSource(props: SourceProps) {
  return (
    <div className="overflow-x-auto whitespace-pre-wrap break-words rounded-[8px] border border-hairline-soft bg-bg-grad-b/60 px-3.5 py-3 font-mono text-[12px] leading-[1.7] text-text-2">
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
      /** `else` / `elif` 标记及其后到 `endif` 前的原文，不属于「仅当」标签覆盖的范围。 */
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

function SourceNodes({ nodes, template, partials }: Omit<SourceProps, "text"> & { nodes: SourceNode[] }) {
  const { t } = useTranslation("dashboard");
  return nodes.map((part, index) => {
    if (typeof part !== "string") {
      const label = SLOT_PATTERN.test(part.condition)
        ? t("prompt_templates_optional_slot", { slot: part.condition })
        : t("prompt_templates_optional_condition", { condition: part.condition });
      return (
        <span key={index} className="my-2 block border-l-2 border-hairline pl-3">
          <span role="group" aria-label={label}>
            <span className="mb-1 block font-sans text-[11px] text-text-3">{label}</span>
            <mark className="bg-bg-grad-a/70 text-text-4">{part.opening}</mark>
            <SourceNodes nodes={part.children} template={template} partials={partials} />
          </span>
          <SourceNodes nodes={part.alternate} template={template} partials={partials} />
          <mark className="bg-bg-grad-a/70 text-text-4">{part.closing}</mark>
        </span>
      );
    }
    const reference = PARTIAL_PATTERN.exec(part)?.groups;
    if (reference) {
      return (
        <PartialReference
          key={index}
          marker={part}
          name={reference.name}
          axis={reference.kind === "variant" ? reference.axis : undefined}
          template={template}
          partials={partials}
        />
      );
    }
    if (part.startsWith("{{")) {
      const slot = part.slice(2, -2).trim();
      const description = SLOT_PATTERN.test(slot)
        ? (template.slots[slot] ?? template.slots[slot.split(".")[0]])
        : undefined;
      if (description !== undefined) {
        return (
          <span
            key={index}
            title={description}
            className="rounded-[4px] bg-accent-dim px-1 text-accent-2"
          >
            {slot}
          </span>
        );
      }
    }
    return /^\{[{%#]/.test(part) ? (
      <mark key={index} className="rounded-[4px] bg-bg-grad-a/70 px-0.5 text-text-4">
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
  template,
  partials,
}: Omit<SourceProps, "text"> & {
  marker: string;
  name: string;
  axis?: string;
}) {
  const { t } = useTranslation("dashboard");
  const [expanded, setExpanded] = useState(false);
  const [value, setValue] = useState(axis ? template.applies_to[axis]?.[0] : undefined);
  const partialName = axis ? `${name}/${value}` : name;
  const partial = partials.find((item) => item.name === partialName);

  if (!partial) return <mark className="bg-warm-tint text-warm-bright">{marker}</mark>;

  return (
    <span>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="rounded-[4px] bg-warm-tint px-1 text-left text-warm-bright hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {marker}
      </button>
      {expanded && (
        <span className="my-2 block rounded-[6px] border-l-2 border-warm-ring bg-warm-tint/30 py-2 pl-3 pr-2">
          <span className="mb-1 flex flex-wrap items-center gap-2 whitespace-normal text-[11px] text-warm-bright">
            <span>{partialName}</span>
            {axis && (
              <label className="inline-flex items-center gap-2">
                {t(`prompt_templates_axis_${axis}`, { defaultValue: axis })}
                <select
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  className="rounded border border-hairline bg-bg px-1 py-0.5 text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {template.applies_to[axis].map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </label>
            )}
          </span>
          {partial.source.trim() ? (
            <SourceContent
              key={partialName}
              text={partial.source}
              template={template}
              partials={partials}
            />
          ) : (
            <span className="italic text-text-4">{t("prompt_templates_partial_blank")}</span>
          )}
        </span>
      )}
    </span>
  );
}
