import { useLocation, useSearch } from "wouter";
import { PromptPartialDetailView } from "./PromptPartialDetailView";
import { PromptTemplateDetailView } from "./PromptTemplateDetailView";
import { PromptTemplateList } from "./PromptTemplateList";

/**
 * System settings › Prompt templates: read-only browsing of templates bundled with the release.
 *
 * Query parameters alongside `section` determine the view: `partial` takes precedence over `template`; show the list if both are absent.
 * Push a history entry on every opening or return so browser back/forward and refresh retain the corresponding detail.
 */
export function PromptTemplatesSection() {
  const [location, navigate] = useLocation();
  const search = useSearch();
  const params = new URLSearchParams(search);
  const templateId = params.get("template");
  const partialName = params.get("partial");

  const open = (changes: { template?: string | null; partial?: string | null }) => {
    const next = new URLSearchParams(search);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    navigate(`${location}?${next.toString()}`);
  };

  if (partialName) {
    return (
      <PromptPartialDetailView
        key={partialName}
        name={partialName}
        backTo={templateId ? "template" : "list"}
        onBack={() => open({ partial: null })}
        onOpenTemplate={(id) => open({ template: id, partial: null })}
      />
    );
  }
  if (templateId) {
    return (
      <PromptTemplateDetailView
        key={templateId}
        templateId={templateId}
        onBack={() => open({ template: null })}
        onOpenPartial={(name) => open({ partial: name })}
      />
    );
  }
  return <PromptTemplateList onSelect={(id) => open({ template: id })} />;
}
