import { useState } from "react";
import { PromptTemplateDetailView } from "./PromptTemplateDetailView";
import { PromptTemplateList } from "./PromptTemplateList";

/** 系统设置 › 提示词模版：只读浏览随版本内置的模版。 */
export function PromptTemplatesSection() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (selectedId !== null) {
    return (
      <PromptTemplateDetailView
        key={selectedId}
        templateId={selectedId}
        onBack={() => setSelectedId(null)}
      />
    );
  }
  return <PromptTemplateList onSelect={setSelectedId} />;
}
