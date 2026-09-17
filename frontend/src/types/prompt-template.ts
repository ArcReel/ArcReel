/** 提示词模版元数据（frontmatter），列表与详情共用。 */
export interface PromptTemplateMeta {
  id: string;
  category: string;
  title: string;
  description: string;
  /** 轴名 → 适用的轴值；变体族按这里的轴值展开。 */
  applies_to: Record<string, string[]>;
  /** 槽位名 → 说明。 */
  slots: Record<string, string>;
  protected: boolean;
  /** `module:Class`，无结构化输出时为 null。 */
  output_schema: string | null;
}

export interface PromptTemplateListResponse {
  templates: PromptTemplateMeta[];
}

export interface PromptTemplatePartial {
  name: string;
  source: string;
}

/** JSON schema 节点，只声明设置页展开输出结构时读取的字段。 */
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
  /** 按首次引用顺序；变体族展开为全部轴值。 */
  partials: PromptTemplatePartial[];
  output_schema?: JsonSchemaNode;
}
