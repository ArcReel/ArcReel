import type { JsonSchemaNode } from "@/types";

/** 输出结构的一行：字段路径 / 类型或枚举 / 说明。 */
export interface OutputSchemaRow {
  /** 点分路径，数组元素写作 `field[]`。 */
  path: string;
  /** `string`、`list[object]`、`enum` 一类的类型提示。 */
  type: string;
  enumValues: string[];
  nullable: boolean;
  description: string;
}

interface Resolved {
  node: JsonSchemaNode;
  type: string;
  enumValues: string[];
  nullable: boolean;
  /** 解开 $ref 时经过的定义名，用于截断自引用。 */
  refs: string[];
}

/** 把 pydantic 导出的 JSON schema 摊平成按字段路径排列的行。 */
export function flattenOutputSchema(schema: JsonSchemaNode): OutputSchemaRow[] {
  const defs = schema.$defs ?? {};
  const rows: OutputSchemaRow[] = [];

  const walk = (node: JsonSchemaNode, prefix: string, seen: ReadonlySet<string>) => {
    for (const [name, prop] of Object.entries(node.properties ?? {})) {
      const resolved = resolve(prop, defs);
      let path = `${prefix}${name}`;
      rows.push({
        path,
        type: resolved.type,
        enumValues: resolved.enumValues,
        nullable: resolved.nullable,
        description: normalizeSpace(prop.description ?? resolved.node.description ?? ""),
      });
      let child: Resolved | null = resolved.node.properties ? resolved : null;
      if (!child && resolved.node.type === "array" && resolved.node.items) {
        const items = resolve(resolved.node.items, defs);
        if (items.node.properties) {
          child = { ...items, refs: [...resolved.refs, ...items.refs] };
          path += "[]";
        }
      }
      if (child && !child.refs.some((ref) => seen.has(ref))) {
        walk(child.node, `${path}.`, new Set([...seen, ...child.refs]));
      }
    }
  };

  walk(schema, "", new Set());
  return rows;
}

function resolve(prop: JsonSchemaNode, defs: Record<string, JsonSchemaNode>): Resolved {
  if (prop.$ref) {
    const name = prop.$ref.split("/").pop() ?? prop.$ref;
    const target = defs[name] ?? {};
    const inner = resolve(target, defs);
    return { ...inner, refs: [name, ...inner.refs] };
  }
  if (prop.anyOf) {
    const options = prop.anyOf.map((option) => resolve(option, defs));
    const nonNull = options.filter((option) => option.node.type !== "null");
    const branches = nonNull.length > 0 ? nonNull : options;
    const nullable = nonNull.length < options.length || branches.some((option) => option.nullable);
    if (branches.length === 1) return { ...branches[0], nullable };
    // 多个分支合并为一行且不向下展开：展开任一分支的字段都会把其余分支的形状藏起来。
    const types = [...new Set(branches.map((option) => option.type))];
    return {
      node: prop,
      type: types.length > 0 ? types.join(" | ") : "object",
      enumValues: [...new Set(branches.flatMap((option) => option.enumValues))],
      nullable,
      refs: [],
    };
  }
  if (prop.type === "array") {
    const items = resolve(prop.items ?? {}, defs);
    return {
      node: prop,
      type: `list[${items.type}]`,
      enumValues: items.enumValues,
      nullable: false,
      refs: [],
    };
  }
  if (prop.enum) {
    return { node: prop, type: "enum", enumValues: prop.enum.map(String), nullable: false, refs: [] };
  }
  return { node: prop, type: prop.type ?? "object", enumValues: [], nullable: false, refs: [] };
}

function normalizeSpace(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ");
}
