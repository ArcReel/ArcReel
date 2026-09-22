import { describe, expect, it } from "vitest";
import type {
  ComfyuiBindingCandidate,
  ComfyuiBindingTarget,
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
} from "@/types";
import {
  bindingKeysFor,
  bindingsFromInference,
  classTypeCounts,
  definitionFingerprint,
  isLinkInput,
  literalInputText,
  manualTargets,
  pruneBindings,
  rowOrigin,
  rowStatus,
  saveBlockers,
  statusTally,
  toggleListTarget,
  workflowNodes,
} from "./comfyui-bindings";

const WORKFLOW: Record<string, unknown> = {
  "10": { class_type: "CLIPTextEncode", inputs: { text: "a cat", clip: ["4", 0] }, _meta: { title: "Positive" } },
  "2": { class_type: "LoadImage", inputs: { image: "first.png" } },
  "9": { class_type: "SaveVideo", inputs: { images: ["8", 0], filenames: { __value__: ["a", "b"] } } },
  notes: "this is not a node",
};

function candidate(target: ComfyuiBindingTarget, overrides?: Partial<ComfyuiBindingCandidate>): ComfyuiBindingCandidate {
  return { target, score: 64, signals: [], selected: false, origin: "inferred", depth: null, ...overrides };
}

function inference(overrides?: Partial<ComfyuiInferResponse>): ComfyuiInferResponse {
  return {
    media_type: "video",
    savable: false,
    bindings: {},
    notes: [],
    import_shape: "comfyui_api_workflow",
    wrapped_definition: null,
    ...overrides,
  };
}

function definition(overrides?: Partial<ComfyuiEndpointDefinition>): ComfyuiEndpointDefinition {
  return {
    kind: "comfyui",
    schema_version: "1.0.0",
    meta: { name: "Wan 2.2 i2v", author: "unknown", version: "1.0.0" },
    media_type: "video",
    workflow: WORKFLOW,
    bindings: {},
    ...overrides,
  };
}

describe("workflowNodes", () => {
  it("orders nodes by their numeric id and skips whatever is not a node", () => {
    expect(workflowNodes(WORKFLOW).map((node) => node.id)).toEqual(["2", "9", "10"]);
  });

  it("reads the title out of _meta and defaults the inputs to an empty object", () => {
    const nodes = workflowNodes({ "1": { class_type: "KSampler" }, "2": { class_type: "X", _meta: { title: "T" } } });

    expect(nodes[0]).toEqual({ id: "1", classType: "KSampler", title: "", inputs: {} });
    expect(nodes[1].title).toBe("T");
  });
});

describe("isLinkInput", () => {
  it("treats a [node, slot] pair as a wire and everything else as a literal", () => {
    expect(isLinkInput(["4", 0])).toBe(true);
    expect(isLinkInput("a cat")).toBe(false);
    expect(isLinkInput([1, 2, 3])).toBe(false);
  });

  it("reads the __value__ wrapper as a literal whatever it wraps", () => {
    // 包装是作者刻意声明的「这是字面数组」，与服务端 workflow.is_link 同序：先看包装再判形状。
    // 反过来先解包，{"__value__": ["4", 0]} 会被判成连线，一个真能填值的输入就从手选里消失了。
    expect(isLinkInput({ __value__: ["a", "b"] })).toBe(false);
    expect(isLinkInput({ __value__: ["4", 0] })).toBe(false);
  });
});

describe("manualTargets", () => {
  it("offers every literal input and leaves the wires out", () => {
    const nodes = workflowNodes(WORKFLOW);

    expect(manualTargets(nodes, "prompt")).toEqual([
      { node: "2", input: "image", class_type: "LoadImage" },
      { node: "9", input: "filenames", class_type: "SaveVideo" },
      { node: "10", input: "text", class_type: "CLIPTextEncode", title: "Positive" },
    ]);
  });

  it("offers whole nodes for the output, which is node level and has no input", () => {
    expect(manualTargets(workflowNodes(WORKFLOW), "output").map((target) => target.node)).toEqual(["2", "9", "10"]);
    expect(manualTargets(workflowNodes(WORKFLOW), "output")[0].input).toBeUndefined();
  });

  it("marks a hand-picked frame rate as read-only, which its schema requires", () => {
    expect(manualTargets(workflowNodes(WORKFLOW), "fps")[0].direction).toBe("read");
  });
});

describe("literalInputText", () => {
  it("shows the current literal and truncates a long one", () => {
    const node = workflowNodes(WORKFLOW)[2];

    // 字符串按原文显示，不加一层 JSON 引号；其余类型走 JSON 写法。
    expect(literalInputText(node, "text")).toBe("a cat");
    expect(literalInputText(node, "clip")).toBe('["4",0]');
    expect(literalInputText({ ...node, inputs: { text: "x".repeat(60) } }, "text").endsWith("…")).toBe(true);
  });
});

describe("classTypeCounts", () => {
  it("counts each class_type and puts the most common first", () => {
    const nodes = workflowNodes({
      "1": { class_type: "CLIPTextEncode" },
      "2": { class_type: "CLIPTextEncode" },
      "3": { class_type: "SaveVideo" },
    });

    expect(classTypeCounts(nodes)).toEqual([
      { classType: "CLIPTextEncode", count: 2 },
      { classType: "SaveVideo", count: 1 },
    ]);
  });
});

describe("bindingKeysFor", () => {
  it("drops the two frames and the timeline pair for an image endpoint", () => {
    expect(bindingKeysFor("image")).not.toContain("start_image");
    expect(bindingKeysFor("image")).not.toContain("frames");
    expect(bindingKeysFor("image")).toHaveLength(7);
    expect(bindingKeysFor("video")).toHaveLength(11);
  });
});

describe("bindingsFromInference", () => {
  it("takes the selected targets of an auto-selected key and nothing from a tie", () => {
    const result = bindingsFromInference(
      inference({
        bindings: {
          prompt: {
            state: "auto_selected",
            candidates: [candidate({ node: "10", input: "text", class_type: "CLIPTextEncode" }, { selected: true })],
            notes: [],
          },
          seed: {
            state: "ambiguous",
            candidates: [candidate({ node: "3", input: "seed", class_type: "KSampler" })],
            notes: [],
          },
        },
      }),
      "video",
    );

    expect(result.prompt).toEqual([{ node: "10", input: "text", class_type: "CLIPTextEncode" }]);
    expect(result.seed).toBeUndefined();
  });

  it("keeps an explicitly unsupported key as an empty list, which is not the same as absent", () => {
    const result = bindingsFromInference(
      inference({ bindings: { end_image: { state: "unsupported", candidates: [], notes: [] } } }),
      "video",
    );

    expect(result.end_image).toEqual([]);
  });
});

describe("pruneBindings", () => {
  it("drops the keys the new media type has no place for", () => {
    const pruned = pruneBindings(
      { prompt: [{ node: "10", input: "text", class_type: "CLIPTextEncode" }], frames: [], start_image: [] },
      "image",
    );

    expect(Object.keys(pruned)).toEqual(["prompt"]);
  });
});

describe("toggleListTarget", () => {
  const first = { node: "2", input: "image", class_type: "LoadImage" };
  const second = { node: "5", input: "image", class_type: "LoadImage" };
  const candidates = [candidate(first), candidate(second)];

  it("orders picks by the candidate list rather than by when they were clicked", () => {
    const afterSecond = toggleListTarget(candidates, [], second);

    expect(toggleListTarget(candidates, afterSecond, first)).toEqual([first, second]);
  });

  it("removes a target that was already picked", () => {
    expect(toggleListTarget(candidates, [first, second], first)).toEqual([second]);
  });

  it("puts a hand-picked target that is not a candidate after the candidates", () => {
    const manual = { node: "7", input: "image", class_type: "LoadImage" };

    expect(toggleListTarget(candidates, [first], manual)).toEqual([first, manual]);
  });
});

describe("rowStatus", () => {
  const target = { node: "10", input: "text", class_type: "CLIPTextEncode" };

  it("calls an untouched inferred pick automatically recognized", () => {
    expect(
      rowStatus("prompt", "auto_selected", [target], { touched: false, candidates: [candidate(target)] }),
    ).toBe("auto");
  });

  it("calls a saved binding manual, because saving one is what confirming it means", () => {
    expect(
      rowStatus("prompt", "auto_selected", [target], {
        touched: false,
        candidates: [candidate(target, { origin: "kept" })],
      }),
    ).toBe("manual");
  });

  it("calls anything the user changed this round manual", () => {
    expect(rowStatus("seed", "not_found", [target], { touched: true, candidates: [] })).toBe("manual");
  });

  it("puts a tie ahead of the required marker: there are candidates, so the job is to pick one", () => {
    expect(rowStatus("prompt", "ambiguous", undefined, { touched: false, candidates: [] })).toBe("ambiguous");
    expect(rowStatus("prompt", "not_found", undefined, { touched: false, candidates: [] })).toBe("required_unbound");
    expect(rowStatus("seed", "not_found", undefined, { touched: false, candidates: [] })).toBe("not_found");
  });

  it("reads an empty list as explicitly unsupported", () => {
    expect(rowStatus("end_image", "unsupported", [], { touched: false, candidates: [] })).toBe("unsupported");
  });

  it("treats a pending confirmation as a tie, since both block saving until the user acts", () => {
    expect(rowStatus("seed", "needs_confirmation", undefined, { touched: false, candidates: [] })).toBe("ambiguous");
  });
});

describe("rowOrigin", () => {
  const target = { node: "10", input: "text", class_type: "CLIPTextEncode" };
  const other = { node: "2", input: "image", class_type: "LoadImage" };

  it("says nothing about a row this round's inference proposed itself", () => {
    expect(rowOrigin([candidate(target)], [target])).toBeNull();
  });

  it("calls a row kept only when every one of its targets was", () => {
    expect(rowOrigin([candidate(target, { origin: "kept" })], [target])).toBe("kept");
  });

  it("reports a rematch even when the rest of the row was kept, since that is what wants a look", () => {
    expect(
      rowOrigin(
        [candidate(target, { origin: "kept" }), candidate(other, { origin: "rematched" })],
        [target, other],
      ),
    ).toBe("rematched");
  });

  it("says nothing about a row with no targets, or one picked by hand off the candidate list", () => {
    expect(rowOrigin([candidate(target, { origin: "kept" })], undefined)).toBeNull();
    expect(rowOrigin([candidate(target, { origin: "kept" })], [other])).toBeNull();
  });
});

describe("statusTally", () => {
  it("counts a required unbound row under not found", () => {
    expect(statusTally(["auto", "manual", "ambiguous", "not_found", "required_unbound", "unsupported"])).toEqual({
      bound: 2,
      ambiguous: 1,
      notFound: 2,
      unsupported: 1,
    });
  });
});

describe("saveBlockers", () => {
  const prompt = { node: "10", input: "text", class_type: "CLIPTextEncode" };
  const output = { node: "9", class_type: "SaveVideo" };
  const bound = { prompt: [prompt], output: [output] };

  it("passes a named definition whose required keys are bound", () => {
    expect(saveBlockers(bound, inference(), definition(), "ComfyUI workflow")).toEqual([]);
  });

  it("holds the placeholder name back, so two unrelated workflows are not judged the same one", () => {
    const blockers = saveBlockers(bound, inference(), definition({ meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" } }), "ComfyUI workflow");

    expect(blockers).toEqual([{ code: "placeholder_name" }]);
  });

  it("names every required key that has no target", () => {
    expect(saveBlockers({ prompt: [prompt] }, inference(), definition(), "ComfyUI workflow")).toEqual([
      { code: "required_unbound", key: "output" },
    ]);
  });

  it("names a tie that nobody has resolved", () => {
    const blockers = saveBlockers(
      bound,
      inference({
        bindings: {
          seed: { state: "ambiguous", candidates: [candidate(prompt), candidate(output)], notes: [] },
        },
      }),
      definition(),
      "ComfyUI workflow",
    );

    expect(blockers).toEqual([{ code: "needs_choice", key: "seed" }]);
  });

  it("asks for a landing spot, not a choice, when a re-import left the key with no candidate at all", () => {
    const blockers = saveBlockers(
      bound,
      inference({ bindings: { seed: { state: "needs_confirmation", candidates: [], notes: [] } } }),
      definition(),
      "ComfyUI workflow",
    );

    expect(blockers).toEqual([{ code: "needs_binding", key: "seed" }]);
  });

  it("names two semantics that landed on the same field, which the server refuses outright", () => {
    const blockers = saveBlockers(
      { ...bound, negative_prompt: [prompt] },
      inference(),
      definition(),
      "ComfyUI workflow",
    );

    expect(blockers).toEqual([{ code: "target_taken", key: "negative_prompt", otherKey: "prompt" }]);
  });

  it("lets the read-only frame rate share a node with a written key, since it never writes a value", () => {
    const blockers = saveBlockers(
      { ...bound, fps: [{ ...prompt, direction: "read" }] },
      inference(),
      definition(),
      "ComfyUI workflow",
    );

    expect(blockers).toEqual([]);
  });

  it("ignores the keys the current media type has no place for", () => {
    const blockers = saveBlockers(
      { ...bound, frames: [{ node: "9", input: "length", class_type: "SaveVideo" }] },
      inference(),
      definition({ media_type: "image" }),
      "ComfyUI workflow",
    );

    expect(blockers).toEqual([]);
  });
});

describe("definitionFingerprint", () => {
  it("reads an empty title the same as no title at all", () => {
    // 手选进来的条目在无标题节点上不写 title，服务端重匹配回来的那一份写的是 ""。
    const picked = definition({ bindings: { prompt: [{ node: "10", input: "text", class_type: "CLIPTextEncode" }] } });
    const returned = definition({
      bindings: { prompt: [{ node: "10", input: "text", class_type: "CLIPTextEncode", title: "" }] },
    });

    expect(definitionFingerprint(picked)).toBe(definitionFingerprint(returned));
  });

  it("does not care which order the keys arrived in", () => {
    const one = definition();
    const reversed = Object.fromEntries(Object.entries(one).reverse());

    expect(definitionFingerprint(reversed)).toBe(definitionFingerprint(one));
  });

  it("still sees a real change", () => {
    const picked = definition({ bindings: { prompt: [{ node: "10", input: "text", class_type: "CLIPTextEncode" }] } });
    const moved = definition({
      bindings: { prompt: [{ node: "10", input: "text", class_type: "CLIPTextEncode", title: "Positive" }] },
    });

    expect(definitionFingerprint(picked)).not.toBe(definitionFingerprint(moved));
  });
});
