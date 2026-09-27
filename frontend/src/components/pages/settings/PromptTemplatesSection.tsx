import { useLocation, useSearch } from "wouter";
import { PromptPartialDetailView } from "./PromptPartialDetailView";
import { PromptTemplateDetailView } from "./PromptTemplateDetailView";
import { PromptTemplateList } from "./PromptTemplateList";

/**
 * 系统设置 › 提示词模版：只读浏览随版本内置的模版。
 *
 * 当前视图由查询参数决定，与 `section` 并列：`partial` 优先于 `template`，都缺省时显示列表。
 * 每次打开或返回都压入一条历史，浏览器前进后退与刷新都停在对应详情。
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
