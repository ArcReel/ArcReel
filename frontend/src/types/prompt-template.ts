/** Template trigger; `name` is a tool name, user action name, or task type from the backend's closed declarations. */
export interface PromptTemplateTrigger {
  kind: "agent_tool" | "user_action" | "generation_task";
  name: string;
}

/** Prompt template metadata (frontmatter), shared by list and detail responses. */
export interface PromptTemplateMeta {
  id: string;
  category: string;
  title: string;
  description: string;
  /** Production stage. */
  stage: string;
  invoked_by: PromptTemplateTrigger;
  /** Axis name → applicable axis values; variant families expand over these values. */
  applies_to: Record<string, string[]>;
  /** Slot name → description. */
  slots: Record<string, string>;
  protected: boolean;
  /** `module:Class`; null when there is no structured output. */
  output_schema: string | null;
}

export interface PromptTemplateListResponse {
  templates: PromptTemplateMeta[];
}

export interface PromptTemplatePartial {
  name: string;
  source: string;
  /** Protected: no editing entry point is provided. */
  protected: boolean;
  /** IDs of templates referencing this partial, in registration order. */
  referenced_by: string[];
}

/** JSON schema node declaring only fields read when the settings page expands the output structure. */
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
  /** In first-reference order; variant families expand to all axis values. */
  partials: PromptTemplatePartial[];
  output_schema?: JsonSchemaNode;
}
