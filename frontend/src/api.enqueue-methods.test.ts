/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import { ENQUEUE_METHODS } from "../eslint.config.js";

// 入队类方法以 api.ts 上的 JSDoc `@enqueue` 标记识别（返回类型不统一，无法按类型推断）。
// 该集合必须与 eslint.config.js 的 ENQUEUE_METHODS 完全相同，否则漏登记的方法可被组件直调。
function taggedEnqueueMethods(): string[] {
  const path = resolve(process.cwd(), "src/api.ts");
  const sf = ts.createSourceFile("api.ts", readFileSync(path, "utf8"), ts.ScriptTarget.Latest, true);
  const names: string[] = [];
  sf.forEachChild(function visit(node) {
    if (ts.isMethodDeclaration(node) && ts.getJSDocTags(node).some((t) => t.tagName.text === "enqueue")) {
      names.push(node.name.getText(sf));
    }
    node.forEachChild(visit);
  });
  return names.sort();
}

describe("入队类 API 方法标记", () => {
  it("带 @enqueue 标记的方法与 ENQUEUE_METHODS 完全一致", () => {
    const tagged = taggedEnqueueMethods();
    expect(tagged.length).toBeGreaterThan(0);
    expect(tagged).toEqual([...ENQUEUE_METHODS].sort());
  });
});
