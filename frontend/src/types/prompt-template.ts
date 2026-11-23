export interface PromptTemplateTrigger {
  kind: "agent_tool" | "user_action" | "generation_task";
  name: string;
}

export interface PromptTemplateMeta {
  id: string;
  category: string;
  title: string;
  description: string;
  stage: string;
  invoked_by: PromptTemplateTrigger;
  applies_to: Record<string, string[]>;
  slots: Record<string, string>;
  protected: boolean;
  output_schema: string | null;
}

export interface PromptTemplateListResponse {
  templates: PromptTemplateMeta[];
}

export interface PromptTemplatePartial {
  name: string;
  source: string;
  protected: boolean;
  referenced_by: string[];
}

export interface JsonSchemaNode {
  type?: string;
  description?: string;
  enum?: unknown[];
  properties?: Record<string, JsonSchemaNode>;
  items?: JsonSchemaNode;
  anyOf?: JsonSchemaNode[];
  $ref?: string;
  $defs?: Record<string, JsonSchemaNode>;
}

export interface PromptTemplateDetail {
  template: PromptTemplateMeta;
  source: string;
  partials: PromptTemplatePartial[];
  output_schema?: JsonSchemaNode;
}
