/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import { ENQUEUE_METHODS } from "../eslint.config.js";

// Identify enqueue methods through api.ts JSDoc `@enqueue`, since heterogeneous returns prevent type inference.
// Sets must exactly match eslint.config.js ENQUEUE_METHODS, or unregistered methods become directly callable by components.
function taggedEnqueueMethods(): string[] {
  const path = resolve(process.cwd(), "src/api.ts");
  const sf = ts.createSourceFile("api.ts", readFileSync(path, "utf8"), ts.ScriptTarget.Latest, true);
  const names: string[] = [];
  sf.forEachChild(function visit(node) {
    // Arrow properties (`static foo = async () => …`) also count as methods, preventing silent omissions after syntax changes.
    const isMethod = ts.isMethodDeclaration(node) || ts.isPropertyDeclaration(node);
    if (isMethod && ts.getJSDocTags(node).some((t) => t.tagName.text === "enqueue")) {
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
