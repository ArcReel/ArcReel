import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type {
  ComfyuiBindingCandidate,
  ComfyuiBindingTarget,
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
  ComfyuiKeyInference,
  CustomEndpointInfo,
} from "@/types";
import { ComfyuiEndpointDetail } from "./ComfyuiEndpointDetail";

const WORKFLOW: Record<string, unknown> = {
  "6": { class_type: "CLIPTextEncode", inputs: { text: "a cat" }, _meta: { title: "Positive" } },
  "7": { class_type: "CLIPTextEncode", inputs: { text: "blurry" }, _meta: { title: "Negative" } },
  "50": { class_type: "WanImageToVideo", inputs: { width: 832, height: 480, length: 81 } },
  "61": { class_type: "SaveVideo", inputs: { images: ["50", 0] } },
};

const PROMPT: ComfyuiBindingTarget = { node: "6", input: "text", class_type: "CLIPTextEncode", title: "Positive" };
const NEGATIVE: ComfyuiBindingTarget = { node: "7", input: "text", class_type: "CLIPTextEncode", title: "Negative" };
const OUTPUT: ComfyuiBindingTarget = { node: "61", class_type: "SaveVideo" };

function candidate(
  target: ComfyuiBindingTarget,
  overrides?: Partial<ComfyuiBindingCandidate>,
): ComfyuiBindingCandidate {
  return {
    target,
    score: 229,
    signals: [{ signal: "alias_only", weight: 32, message: "字段名与这项语义的常见写法一致" }],
    selected: false,
    origin: "inferred",
    depth: null,
    ...overrides,
  };
}

function picked(target: ComfyuiBindingTarget, overrides?: Partial<ComfyuiBindingCandidate>): ComfyuiKeyInference {
  return { state: "auto_selected", candidates: [candidate(target, { selected: true, ...overrides })], notes: [] };
}

function inference(overrides?: Partial<ComfyuiInferResponse>): ComfyuiInferResponse {
  return {
    media_type: "video",
    savable: true,
    bindings: { prompt: picked(PROMPT), output: picked(OUTPUT) },
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

function savedRecord(defn: ComfyuiEndpointDefinition): CustomEndpointInfo {
  return {
    installation: null,
    id: 8,
    key: "ce-8",
    display_name: defn.meta.name,
    kind: "comfyui",
    schema_version: "1.0.0",
    media_type: defn.media_type,
    definition: defn,
    created_at: null,
    updated_at: null,
  };
}

function renderDetail(
  options: {
    record?: CustomEndpointInfo | null;
    definition?: ComfyuiEndpointDefinition;
    inference?: ComfyuiInferResponse | null;
    fileName?: string | null;
    onSaved?: (record: CustomEndpointInfo) => void;
    onReimport?: (current: ComfyuiEndpointDefinition) => void;
  } = {},
) {
  return render(
    <ComfyuiEndpointDetail
      record={options.record ?? null}
      definition={options.definition ?? definition()}
      sourceFileName={options.fileName ?? "wan22_i2v_api.json"}
      initialInference={options.inference === undefined ? inference() : options.inference}
      referenceCount={0}
      providers={[]}
      onSaved={options.onSaved ?? vi.fn()}
      onReimport={options.onReimport ?? vi.fn()}
      deleteButton={null}
    />,
  );
}

/** 一行的表头格：语义键的 mono id 与它的状态、目标、展开按钮同处一格，展开面板在格子外。 */
function row(key: string): HTMLElement {
  const cell = screen.getByText(key, { selector: "span.font-mono" }).closest("div.grid");
  if (cell === null) throw new Error(`no row for ${key}`);
  return cell as HTMLElement;
}

describe("ComfyuiEndpointDetail", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("heads the endpoint with its editable name, badges and where it came from", async () => {
    renderDetail({ record: savedRecord(definition()) });

    expect(await screen.findByLabelText("端点名称")).toHaveValue("Wan 2.2 i2v");
    expect(screen.getByText("comfyui")).toBeInTheDocument();
    expect(screen.getByText("ce-8")).toBeInTheDocument();
    expect(screen.getByText("来自 wan22_i2v_api.json")).toBeInTheDocument();
    expect(screen.getByText("4 个节点")).toBeInTheDocument();
  });

  it("gives each semantic its own row, with the six states each saying what to do next", () => {
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          negative_prompt: picked(NEGATIVE, { origin: "kept" }),
          seed: { state: "ambiguous", candidates: [candidate(PROMPT), candidate(NEGATIVE)], notes: [] },
          start_image: { state: "not_found", candidates: [], notes: [] },
          end_image: { state: "unsupported", candidates: [], notes: [] },
          output: { state: "not_found", candidates: [], notes: [] },
        },
      }),
    });

    expect(within(row("prompt")).getByText("自动识别")).toBeInTheDocument();
    expect(within(row("negative_prompt")).getByText("手动指定")).toBeInTheDocument();
    expect(within(row("seed")).getByText("需选择")).toBeInTheDocument();
    expect(within(row("start_image")).getByText("未找到")).toBeInTheDocument();
    expect(within(row("end_image")).getByText("不支持")).toBeInTheDocument();
    expect(within(row("output")).getByText("必填未绑定")).toBeInTheDocument();
    // 没被推断提到的语义键一并算未找到：reference_images、宽、高、帧数、帧率与 output 共 7 项。
    expect(screen.getByText("已绑定 2 · 需选择 1 · 未找到 7 · 不支持 1")).toBeInTheDocument();
  });

  it("opens the tied row on arrival and leaves the settled ones folded", () => {
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          output: picked(OUTPUT),
          seed: { state: "ambiguous", candidates: [candidate(PROMPT), candidate(NEGATIVE)], notes: [] },
        },
      }),
    });

    expect(within(row("seed")).getByRole("button", { name: "收起" })).toBeInTheDocument();
    expect(within(row("prompt")).getByRole("button", { name: "候选 1" })).toBeInTheDocument();
    expect(screen.getByText("2 个候选得分相同")).toBeInTheDocument();
  });

  it("leaves a row the user is working in open, so an ordered multi-pick can be finished in one go", async () => {
    // 参考图是有序多选：选中第一张之后面板合上的话，第二张根本点不到。
    const first: ComfyuiBindingTarget = { node: "10", input: "image", class_type: "LoadImage" };
    const second: ComfyuiBindingTarget = { node: "11", input: "image", class_type: "LoadImage" };
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          output: picked(OUTPUT),
          reference_images: {
            state: "ambiguous",
            candidates: [candidate(first), candidate(second)],
            notes: [],
          },
        },
      }),
    });

    const boxes = within(screen.getByRole("group", { name: "候选落点" })).getAllByRole("checkbox");
    await userEvent.click(boxes[0]);
    await userEvent.click(within(screen.getByRole("group", { name: "候选落点" })).getAllByRole("checkbox")[1]);

    expect(within(row("reference_images")).getByText("2 个入口")).toBeInTheDocument();
  });

  it("keeps the extras of a row reachable right after the pick that brings them into play", async () => {
    const width: ComfyuiBindingTarget = { node: "50", input: "width", class_type: "WanImageToVideo" };
    const height: ComfyuiBindingTarget = { node: "50", input: "height", class_type: "WanImageToVideo" };
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          output: picked(OUTPUT),
          width: { state: "ambiguous", candidates: [candidate(width), candidate(height)], notes: [] },
        },
      }),
    });

    // 宽高可以有多个落点，候选列表因此是多选组而不是单选组。
    await userEvent.click(within(screen.getByRole("group", { name: "候选落点" })).getAllByRole("checkbox")[0]);

    expect(screen.getByLabelText(/对齐步长/)).toBeInTheDocument();
  });

  it("refuses a step that is not a whole positive number", async () => {
    // `min={1}` 只拦得住上下箭头；负数与 `Infinity` 手打得进来，保存时又不再过一次表单校验。
    const width: ComfyuiBindingTarget = { node: "50", input: "width", class_type: "WanImageToVideo" };
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          output: picked(OUTPUT),
          width: { state: "ambiguous", candidates: [candidate(width)], notes: [] },
        },
      }),
    });
    await userEvent.click(within(screen.getByRole("group", { name: "候选落点" })).getAllByRole("checkbox")[0]);
    const step = screen.getByLabelText(/对齐步长/);

    await userEvent.type(step, "16");
    expect(step).toHaveValue(16);

    await userEvent.clear(step);
    await userEvent.type(step, "-4");
    expect(step).toHaveValue(null);

    // schema 把 step 声明为整数：小数存下去会被定义校验打回。
    await userEvent.clear(step);
    await userEvent.type(step, "1.5");
    expect(step).toHaveValue(null);
  });

  it("marks on the row itself what a re-import kept and what it moved, without opening anything", () => {
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT, { origin: "kept" }),
          output: picked(OUTPUT, { origin: "rematched" }),
        },
      }),
    });

    expect(within(row("prompt")).getByText("沿用")).toBeInTheDocument();
    expect(within(row("output")).getByText("已重匹配")).toBeInTheDocument();
  });

  it("asks for a landing spot when a re-import left a semantic with nothing to choose from", () => {
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          output: picked(OUTPUT),
          negative_prompt: { state: "needs_confirmation", candidates: [], notes: [] },
        },
      }),
    });

    expect(within(row("negative_prompt")).getByText("原来的绑定已失效，请重新指定")).toBeInTheDocument();
    expect(screen.getByText("「负向提示词」原来的绑定已失效，需要手选落点或标为不支持")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存端点" })).toBeDisabled();
  });

  it("says so when re-running recognition for one semantic fails", async () => {
    vi.spyOn(API, "inferComfyuiBindings").mockRejectedValue(new Error("推断服务不可用"));
    renderDetail();

    await userEvent.click(within(row("prompt")).getByRole("button", { name: "候选 1" }));
    await userEvent.click(screen.getAllByRole("button", { name: "重新识别" })[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent("推断服务不可用");
  });

  it("shows each candidate's score and why it was proposed, and takes the one the user picks", async () => {
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          output: picked(OUTPUT),
          negative_prompt: { state: "ambiguous", candidates: [candidate(NEGATIVE), candidate(PROMPT)], notes: [] },
        },
      }),
    });

    expect(screen.getAllByText("字段名与这项语义的常见写法一致").length).toBeGreaterThan(0);
    expect(screen.getAllByText("229").length).toBeGreaterThan(0);

    const group = screen.getByRole("radiogroup", { name: "候选落点" });
    await userEvent.click(within(group).getAllByRole("radio")[0]);

    expect(within(row("negative_prompt")).getByText("手动指定")).toBeInTheDocument();
  });

  it("keeps the other targets of a dimension when one candidate is toggled", async () => {
    // 宽高帧数帧率都写/读全部落点：点中一个候选只增删这一条，把同键其余落点顶掉会让那几路
    // 分支再也收不到尺寸与帧数。
    const widthA: ComfyuiBindingTarget = { node: "50", input: "width", class_type: "WanImageToVideo" };
    const widthB: ComfyuiBindingTarget = { node: "61", input: "width", class_type: "SaveVideo" };
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          output: picked(OUTPUT),
          width: { state: "ambiguous", candidates: [candidate(widthA), candidate(widthB)], notes: [] },
        },
      }),
    });
    const group = screen.getByRole("group", { name: "候选落点" });

    await userEvent.click(within(group).getAllByRole("checkbox")[0]);
    await userEvent.click(within(group).getAllByRole("checkbox")[1]);

    expect(within(group).getAllByRole("checkbox")[0]).toBeChecked();
    expect(within(group).getAllByRole("checkbox")[1]).toBeChecked();

    // 再点一次即取消，只去掉这一条。
    await userEvent.click(within(group).getAllByRole("checkbox")[0]);
    expect(within(group).getAllByRole("checkbox")[0]).not.toBeChecked();
    expect(within(group).getAllByRole("checkbox")[1]).toBeChecked();
  });

  it("lets the user reach any literal input in the workflow when nothing was recognized", async () => {
    renderDetail({
      inference: inference({
        bindings: { prompt: picked(PROMPT), output: { state: "not_found", candidates: [], notes: [] } },
      }),
    });

    await userEvent.click(within(row("output")).getByRole("button", { name: "手选" }));
    await userEvent.selectOptions(screen.getByLabelText("为 output 手选落点"), "61.");

    expect(within(row("output")).getByText("手动指定")).toBeInTheDocument();
    expect(within(row("output")).getByText(/#61/)).toBeInTheDocument();
  });

  it("offers to mark an optional semantic unsupported, but never a required one", async () => {
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT),
          output: picked(OUTPUT),
          seed: { state: "not_found", candidates: [], notes: [] },
        },
      }),
    });

    await userEvent.click(within(row("seed")).getByRole("button", { name: "手选" }));
    await userEvent.click(screen.getByRole("button", { name: "标为不支持" }));
    expect(within(row("seed")).getByText("不支持")).toBeInTheDocument();

    await userEvent.click(within(row("prompt")).getByRole("button", { name: "候选 1" }));
    expect(screen.queryByRole("button", { name: "标为不支持" })).not.toBeInTheDocument();
  });

  it("re-runs recognition for one semantic with that binding dropped from the payload", async () => {
    const infer = vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue(
      inference({ bindings: { prompt: picked(NEGATIVE), output: picked(OUTPUT) } }),
    );
    renderDetail();

    await userEvent.click(within(row("prompt")).getByRole("button", { name: "候选 1" }));
    await userEvent.click(screen.getAllByRole("button", { name: "重新识别" })[0]);

    await waitFor(() => expect(infer).toHaveBeenCalledOnce());
    // 已确认的绑定正是重匹配的输入，要重来的那一项因此必须先从载荷里摘掉。
    const payload = infer.mock.calls[0][0] as ComfyuiEndpointDefinition;
    expect(payload.bindings.prompt).toBeUndefined();
    expect(payload.bindings.output).toEqual([OUTPUT]);
    await waitFor(() => expect(within(row("prompt")).getByText(/#7/)).toBeInTheDocument());
  });

  it("leaves a pick made while recognition was still running in place", async () => {
    // 重新识别在途时用户自己定了同一项：他后按的那一下比在途那轮新，结果回来不该把它改掉。
    let release: (value: ComfyuiInferResponse) => void = () => {};
    const pending = new Promise<ComfyuiInferResponse>((resolve) => {
      release = resolve;
    });
    vi.spyOn(API, "inferComfyuiBindings").mockReturnValue(pending);
    renderDetail({
      inference: inference({
        bindings: {
          output: picked(OUTPUT),
          prompt: { state: "ambiguous", candidates: [candidate(PROMPT), candidate(NEGATIVE)], notes: [] },
        },
      }),
    });

    await userEvent.click(screen.getAllByRole("button", { name: "重新识别" })[0]);
    const group = screen.getByRole("radiogroup", { name: "候选落点" });
    await userEvent.click(within(group).getAllByRole("radio")[1]);
    release(inference({ bindings: { prompt: picked(PROMPT), output: picked(OUTPUT) } }));
    await act(async () => {
      await pending;
    });

    expect(within(row("prompt")).getByText("手动指定")).toBeInTheDocument();
    expect(within(row("prompt")).getByText(/#7/)).toBeInTheDocument();
  });

  it("holds the save back and says why, one line per reason", () => {
    renderDetail({
      definition: definition({ meta: { name: "ComfyUI workflow", author: "unknown", version: "1.0.0" } }),
      inference: inference({
        bindings: {
          prompt: { state: "not_found", candidates: [], notes: [] },
          seed: { state: "ambiguous", candidates: [candidate(PROMPT)], notes: [] },
          output: picked(OUTPUT),
        },
      }),
    });

    expect(screen.getByRole("button", { name: "保存端点" })).toBeDisabled();
    expect(screen.getByText(/先给这份 workflow 起个名字/)).toBeInTheDocument();
    expect(screen.getByText("「正向提示词」是必填项，尚未绑定")).toBeInTheDocument();
    expect(screen.getByText("「种子」有并列候选，需要选一个或标为不支持")).toBeInTheDocument();
  });

  it("saves the definition with the confirmed bindings, then settles into a saved button", async () => {
    const created = savedRecord(definition({ bindings: { prompt: [PROMPT], output: [OUTPUT] } }));
    const create = vi.spyOn(API, "createCustomEndpoint").mockResolvedValue(created);
    const onSaved = vi.fn();
    renderDetail({ onSaved });

    await userEvent.click(screen.getByRole("button", { name: "保存端点" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(created));
    expect(create.mock.calls[0][0]).toMatchObject({ bindings: { prompt: [PROMPT], output: [OUTPUT] } });
    expect(await screen.findByRole("button", { name: "已保存" })).toBeDisabled();
  });

  it("opens an already saved endpoint on the saved button", async () => {
    // 服务端重匹配对无标题节点一律回填 title: ""，落盘的那一份省略这个键：同一份绑定，两种写法。
    const defn = definition({ bindings: { prompt: [PROMPT], output: [OUTPUT] } });
    renderDetail({
      record: savedRecord(defn),
      definition: defn,
      inference: inference({
        bindings: { prompt: picked(PROMPT), output: picked({ ...OUTPUT, title: "" }) },
      }),
    });

    expect(screen.getByRole("button", { name: "已保存" })).toBeDisabled();
  });

  it("goes back to an enabled save as soon as anything changes again", async () => {
    const defn = definition({ bindings: { prompt: [PROMPT], output: [OUTPUT] } });
    renderDetail({ record: savedRecord(defn), definition: defn });

    expect(screen.getByRole("button", { name: "已保存" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("端点名称"), "!");

    expect(screen.getByRole("button", { name: "保存端点" })).toBeEnabled();
  });

  it("shows an image endpoint only the seven semantics it can use", () => {
    renderDetail({ definition: definition({ media_type: "image" }), inference: inference({ media_type: "image" }) });

    expect(screen.getByText("prompt", { selector: "span.font-mono" })).toBeInTheDocument();
    expect(screen.queryByText("start_image", { selector: "span.font-mono" })).not.toBeInTheDocument();
    expect(screen.queryByText("frames", { selector: "span.font-mono" })).not.toBeInTheDocument();
  });

  it("re-runs recognition under the other rule set when the media type changes", async () => {
    const infer = vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue(inference({ media_type: "image" }));
    renderDetail();

    await userEvent.click(screen.getByRole("button", { name: "图片" }));

    await waitFor(() => expect(infer).toHaveBeenCalledOnce());
    expect((infer.mock.calls[0][0] as ComfyuiEndpointDefinition).media_type).toBe("image");
    expect(await screen.findByText("prompt", { selector: "span.font-mono" })).toBeInTheDocument();
  });

  it("passes a whole-graph note through instead of swallowing it", () => {
    renderDetail({
      inference: inference({ notes: [{ code: "batch_size_above_one", message: "这份 workflow 一次出多张，只取第一张" }] }),
    });

    expect(screen.getByText("这份 workflow 一次出多张，只取第一张")).toBeInTheDocument();
  });

  it("marks what a re-import kept, what it rematched and what needs another look", async () => {
    renderDetail({
      inference: inference({
        bindings: {
          prompt: picked(PROMPT, { origin: "kept" }),
          output: picked(OUTPUT, { origin: "rematched" }),
          negative_prompt: { state: "needs_confirmation", candidates: [candidate(NEGATIVE)], notes: [] },
        },
      }),
    });

    await userEvent.click(within(row("prompt")).getByRole("button", { name: "候选 1" }));
    expect(screen.getAllByText("沿用").length).toBe(2);
    await userEvent.click(within(row("output")).getByRole("button", { name: "候选 1" }));
    expect(screen.getAllByText("已重匹配").length).toBe(2);
    expect(screen.getByText(/需确认/)).toBeInTheDocument();
  });

  it("hands the current draft to the re-import so the new workflow lands on this definition", async () => {
    const onReimport = vi.fn();
    renderDetail({ onReimport });

    await userEvent.click(screen.getByRole("button", { name: "重新导入" }));

    expect(onReimport).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "comfyui", bindings: { prompt: [PROMPT], output: [OUTPUT] } }),
    );
  });

  it("asks the server once on arrival when it has no recognition result in hand", async () => {
    const infer = vi.spyOn(API, "inferComfyuiBindings").mockResolvedValue(inference());
    const defn = definition({ bindings: { prompt: [PROMPT] } });
    renderDetail({ record: savedRecord(defn), definition: defn, inference: null });

    await waitFor(() => expect(infer).toHaveBeenCalledOnce());
    expect(infer.mock.calls[0][0]).toEqual(defn);
    expect((await screen.findAllByText("自动识别")).length).toBe(2);
  });

  it("shows the credentials this endpoint actually sends, query ones included", async () => {
    // 只在 query 里配了凭据的端点实发时照样把它拼进 URL；只认 headers 的话，展示的是一句
    // 它根本不用的 Authorization。
    renderDetail({ definition: definition({ auth: { query: { token: "{{ api_key }}" } } }) });

    expect(await screen.findByText(/query:/)).toBeInTheDocument();
    expect(screen.getByText(/token: \{\{ api_key \}\}/)).toBeInTheDocument();
    expect(screen.queryByText(/Authorization/)).not.toBeInTheDocument();
  });

  it("falls back to the template when the definition declares no credentials at all", async () => {
    renderDetail({ definition: definition() });

    expect(await screen.findByText(/Authorization: Bearer \{\{ api_key \}\}/)).toBeInTheDocument();
  });

  it("puts both endpoint-test cards within reach at the end of the detail", async () => {
    renderDetail();

    expect(await screen.findByText("端点测试")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "渲染 /prompt 请求体" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "真实提交一次" })).toBeEnabled();
  });

  it("holds the endpoint test shut while a required binding has nowhere to land", async () => {
    // 服务端校验器会按 comfyui_binding_required 拒收这份定义，两卡先在界面上挡住。
    renderDetail({ inference: inference({ bindings: { prompt: picked(PROMPT) } }) });

    expect(await screen.findByRole("button", { name: "渲染 /prompt 请求体" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "真实提交一次" })).toBeDisabled();
  });
});
