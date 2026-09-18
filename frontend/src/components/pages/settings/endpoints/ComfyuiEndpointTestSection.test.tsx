import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import type {
  ComfyuiEndpointDefinition,
  EndpointPreviewResponse,
  TrialRunInfo,
} from "@/types";
import { ComfyuiEndpointTestSection } from "./ComfyuiEndpointTestSection";

globalThis.URL.createObjectURL ??= vi.fn();
globalThis.URL.revokeObjectURL ??= vi.fn();

const WORKFLOW = {
  "3": { class_type: "KSampler", inputs: { seed: 1 } },
  "5": { class_type: "EmptyHunyuanLatentVideo", inputs: { width: 480, height: 848, length: 81 } },
  "6": { class_type: "CLIPTextEncode", inputs: { text: "" } },
  "8": { class_type: "LoadImage", inputs: { image: "" } },
  "9": { class_type: "SaveVideo", inputs: {} },
};

function definition(overrides: Partial<ComfyuiEndpointDefinition> = {}): ComfyuiEndpointDefinition {
  return {
    kind: "comfyui",
    schema_version: "1.0.0",
    meta: { name: "Wan 2.2 i2v", author: "unknown", version: "1.0.0" },
    media_type: "video",
    workflow: WORKFLOW,
    bindings: {
      prompt: [{ node: "6", input: "text", class_type: "CLIPTextEncode" }],
      output: [{ node: "9", class_type: "SaveVideo" }],
      start_image: [{ node: "8", input: "image", class_type: "LoadImage" }],
      width: [{ node: "5", input: "width", class_type: "EmptyHunyuanLatentVideo", step: 16 }],
      height: [{ node: "5", input: "height", class_type: "EmptyHunyuanLatentVideo", step: 16 }],
      frames: [{ node: "5", input: "length", class_type: "EmptyHunyuanLatentVideo", step: 4, fps: 16 }],
      seed: [{ node: "3", input: "seed", class_type: "KSampler", policy: "random" }],
    },
    ...overrides,
  };
}

const PREVIEW: EndpointPreviewResponse = {
  submit: {
    method: "POST",
    url: "http://comfy.test/prompt",
    headers: { Authorization: "Bearer ****abcd" },
    body: { prompt: WORKFLOW, client_id: "arcreel-{{ prompt_id }}" },
  },
  poll: {
    method: "GET",
    url: "http://comfy.test/history/{{ prompt_id }}",
    headers: { Authorization: "Bearer ****abcd" },
    body: null,
  },
  result: null,
  conversions: {
    workflow_sha256: "9f2c4b1a",
    aspect_ratio: "9:16",
    resolution: "720p",
    duration_seconds: 5,
    width: 720,
    height: 1280,
    frames: 81,
    seed: 4242,
    negative_prompt: "模糊",
    dropped_nodes: ["12"],
  },
};

const FINISHED: TrialRunInfo = {
  id: "run-1",
  status: "succeeded",
  provider: "comfy.test",
  model: "Wan 2.2 i2v",
  created_at: 1,
  finished_at: 2,
  api_call_id: null,
  provider_job_id: "p-1",
  stages: { submit: "done", poll: "done", result: "done", artifact: "done" },
  request: null,
  submit_response: null,
  poll_responses: [],
  result_response: null,
  extractions: {},
  video_url: null,
  duration_seconds: 42,
  error: null,
  error_code: null,
  error_action: null,
  has_artifact: true,
};

function renderSection(options: { definition?: ComfyuiEndpointDefinition; blocked?: boolean } = {}) {
  return render(
    <ComfyuiEndpointTestSection
      definition={options.definition ?? definition()}
      providers={[]}
      blocked={options.blocked ?? false}
    />,
  );
}

describe("ComfyuiEndpointTestSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:trial-artifact");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  });

  it("renders the masked request body next to how each value was derived", async () => {
    const preview = vi.spyOn(API, "previewEndpointRequest").mockResolvedValue(PREVIEW);
    renderSection();

    await userEvent.type(screen.getByLabelText("提示词"), "一只猫");
    await userEvent.selectOptions(screen.getByLabelText("分辨率"), "720p");
    await userEvent.click(screen.getByRole("button", { name: "渲染 /prompt 请求体" }));

    await waitFor(() =>
      expect(preview).toHaveBeenCalledWith(
        expect.objectContaining({
          parameters: expect.objectContaining({
            model: "Wan 2.2 i2v",
            prompt: "一只猫",
            aspect_ratio: "9:16",
            resolution: "720p",
            duration_seconds: 5,
          }),
        }),
        { assets: {} },
      ),
    );

    // 请求体照真发的样子摆出来，两节各带一份只剩尾四位的凭据。
    expect(await screen.findAllByText(/Bearer \*\*\*\*abcd/)).toHaveLength(2);
    expect(screen.getByText(/POST http:\/\/comfy\.test\/prompt/)).toBeInTheDocument();
    expect(screen.getByText(/GET http:\/\/comfy\.test\/history/)).toBeInTheDocument();

    const conversions = screen.getByText("换算说明").parentElement as HTMLElement;
    expect(within(conversions).getByText("720×1280")).toBeInTheDocument();
    expect(within(conversions).getByText("9:16 · 720p")).toBeInTheDocument();
    expect(within(conversions).getByText("81 帧")).toBeInTheDocument();
    // 种子那一行左边是绑定上的取值策略，不是又一次重复行名。
    expect(within(conversions).getByText("random")).toBeInTheDocument();
    expect(within(conversions).getByText("4242")).toBeInTheDocument();
    expect(within(conversions).getByText("12")).toBeInTheDocument();
    expect(within(conversions).getByText("9f2c4b1a")).toBeInTheDocument();
  });

  it("lets the duration box be cleared and sends the placeholder seconds while it is empty", async () => {
    const preview = vi.spyOn(API, "previewEndpointRequest").mockResolvedValue(PREVIEW);
    renderSection();

    const duration = screen.getByLabelText("时长（秒）") as HTMLInputElement;
    await userEvent.clear(duration);
    expect(duration.value).toBe("");
    expect(duration.placeholder).toBe("5");

    await userEvent.click(screen.getByRole("button", { name: "渲染 /prompt 请求体" }));
    await waitFor(() =>
      expect(preview).toHaveBeenCalledWith(
        expect.objectContaining({ parameters: expect.objectContaining({ duration_seconds: 5 }) }),
        { assets: {} },
      ),
    );

    // 清空之后打的秒数原样进请求，而不是接在一个被填回来的数字后面。
    await userEvent.type(duration, "8");
    expect(duration.value).toBe("8");
  });

  it("says outright which dimension does not drive this workflow", async () => {
    vi.spyOn(API, "previewEndpointRequest").mockResolvedValue({
      ...PREVIEW,
      conversions: { ...PREVIEW.conversions!, width: null, height: null, seed: null },
    });
    renderSection();

    await userEvent.click(screen.getByRole("button", { name: "渲染 /prompt 请求体" }));

    const unbound = await screen.findAllByText("未驱动这份 workflow，保留它自己的值");
    expect(unbound).toHaveLength(2);
  });

  it("uploads the assets the node bindings ask for", async () => {
    const preview = vi.spyOn(API, "previewEndpointRequest").mockResolvedValue(PREVIEW);
    renderSection();

    // 绑了首帧就有首帧格子；没绑参考图与尾帧，两个格子都不该出现。
    expect(screen.queryByLabelText("参考图")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("尾帧")).not.toBeInTheDocument();
    const file = new File(["image"], "start.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("首帧"), file);
    await userEvent.click(screen.getByRole("button", { name: "渲染 /prompt 请求体" }));

    await waitFor(() =>
      expect(preview).toHaveBeenCalledWith(expect.anything(), { assets: { start_image: [file] } }),
    );
  });

  it("walks the four stages, names the prompt_id and plays the artifact back", async () => {
    vi.spyOn(API, "createTrialRun").mockResolvedValue(FINISHED);
    vi.spyOn(API, "getTrialRunArtifact").mockResolvedValue(new Blob(["video"]));
    renderSection();

    await userEvent.click(screen.getByRole("button", { name: "真实提交一次" }));

    expect(await screen.findByLabelText("测试连接产物")).toHaveAttribute("src", "blob:trial-artifact");
    expect(screen.getByText("prompt_id p-1")).toBeInTheDocument();
    // run.duration_seconds 是成片秒数（VideoGenerationResult 抄的那一位），不是墙钟耗时。
    expect(screen.getByText("成片 42 秒")).toBeInTheDocument();
    const stages = screen.getByRole("list");
    for (const stage of ["提交", "轮询", "取得结果", "取回产物"]) {
      expect(within(stages).getByText(stage)).toBeInTheDocument();
    }
    expect(within(stages).getAllByText("已完成")).toHaveLength(4);
  });

  it("marks the stages still ahead as in progress while the run is mid-flight", async () => {
    const running: TrialRunInfo = {
      ...FINISHED,
      status: "running",
      stages: { submit: "done" },
      duration_seconds: null,
      has_artifact: false,
    };
    vi.spyOn(API, "createTrialRun").mockResolvedValue(running);
    vi.spyOn(API, "getTrialRun").mockResolvedValue(running);
    renderSection();

    await userEvent.click(screen.getByRole("button", { name: "真实提交一次" }));

    const stages = await screen.findByRole("list");
    expect(within(stages).getAllByText("已完成")).toHaveLength(1);
    // 还没到的三段在非终态上说「进行中」，不是「未到达」。
    expect(within(stages).getAllByText("进行中")).toHaveLength(3);
    expect(within(stages).queryByText("未到达")).not.toBeInTheDocument();
    // 未走完时取消入口在，重复提交挡住。
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "真实提交一次" })).toBeDisabled();
  });

  it("shows the failure code and where to go fix it", async () => {
    vi.spyOn(API, "createTrialRun").mockResolvedValue({
      ...FINISHED,
      status: "failed",
      stages: { submit: "done", poll: "skipped", result: "skipped", artifact: "skipped" },
      provider_job_id: null,
      has_artifact: false,
      error: "ComfyUI 拒收了这份 workflow",
      error_code: "comfyui_node_errors",
      error_action: "configure_provider",
    });
    renderSection();

    await userEvent.click(screen.getByRole("button", { name: "真实提交一次" }));

    const failure = await screen.findByRole("alert");
    expect(within(failure).getByText("comfyui_node_errors")).toBeInTheDocument();
    expect(within(failure).getByText("ComfyUI 拒收了这份 workflow")).toBeInTheDocument();
    expect(within(failure).getByText(/配置供应商/)).toBeInTheDocument();
    expect(within(screen.getByRole("list")).getAllByText("未到达")).toHaveLength(3);
  });

  it("tells a momentary failure to try again instead of sending it to the provider settings", async () => {
    // 八条 ComfyUI 失败码里有三条指向重试：服务端说哪个动作，卡上就说哪一句。
    vi.spyOn(API, "createTrialRun").mockResolvedValue({
      ...FINISHED,
      status: "failed",
      has_artifact: false,
      error: "ComfyUI 上找不到这一笔",
      error_code: "comfyui_job_lost",
      error_action: "retry",
    });
    renderSection();

    await userEvent.click(screen.getByRole("button", { name: "真实提交一次" }));

    const failure = await screen.findByRole("alert");
    expect(within(failure).getByText(/再试一次/)).toBeInTheDocument();
    expect(within(failure).queryByText(/配置供应商/)).not.toBeInTheDocument();
  });

  it("offers an image endpoint the preview card only", () => {
    renderSection({ definition: definition({ media_type: "image" }) });

    expect(screen.getByRole("button", { name: "渲染 /prompt 请求体" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "真实提交一次" })).not.toBeInTheDocument();
    expect(screen.queryByText("测试连接")).not.toBeInTheDocument();
    // 时长对图像端点无意义，分辨率档也换成图像那一组。
    expect(screen.queryByLabelText("时长（秒）")).not.toBeInTheDocument();
    expect(within(screen.getByLabelText("分辨率")).getByRole("option", { name: "1K" })).toBeInTheDocument();
  });

  it("holds both cards shut while the node bindings would be refused", () => {
    renderSection({ blocked: true });

    expect(screen.getByRole("button", { name: "渲染 /prompt 请求体" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "真实提交一次" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("先把节点绑定补齐");
  });
});
