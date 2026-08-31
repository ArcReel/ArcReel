import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import type { EndpointDefinition, TrialRunInfo } from "@/types";
import { EndpointTestSection } from "./EndpointTestSection";

globalThis.URL.createObjectURL ??= vi.fn();
globalThis.URL.revokeObjectURL ??= vi.fn();

const DEFINITION: EndpointDefinition = {
  kind: "declarative",
  schema_version: "1.0.0",
  meta: { name: "Image Video", author: "ArcReel", version: "1.0.0" },
  auth: { headers: { Authorization: "Bearer {{ api_key }}" } },
  inputs: {
    first_frame: { source: "start_image", encoding: "data_uri", required: true },
  },
  submit: {
    method: "POST",
    url: "{{ base_url }}/videos",
    body: { model: "{{ model }}", image: "{{ inputs.first_frame }}" },
    extract: { task_id: ["$.id"] },
  },
  poll: {
    method: "GET",
    url: "{{ base_url }}/videos/{{ task_id }}",
    extract: { status: ["$.status"], video_url: ["$.video_url"] },
  },
  status_map: { succeeded: "succeeded" },
};

const RUNNING: TrialRunInfo = {
  id: "run-1",
  status: "running",
  provider: "example.test",
  model: "video-1",
  created_at: 1,
  finished_at: null,
  api_call_id: null,
  request: null,
  submit_response: null,
  result_response: null,
  poll_responses: [],
  extractions: {},
  video_url: null,
  duration_seconds: null,
  error: null,
  has_artifact: false,
};

const FINISHED: TrialRunInfo = {
  ...RUNNING,
  status: "succeeded",
  finished_at: 2,
};

describe("EndpointTestSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:trial-artifact");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  });

  it("uses only the definition's current assets for previews and trial runs", async () => {
    const previewEndpointRequest = vi.spyOn(API, "previewEndpointRequest").mockResolvedValue({
      submit: { method: "POST", url: "https://example.test/videos", headers: {}, body: {} },
      poll: { method: "GET", url: "https://example.test/videos/task", headers: {}, body: null },
      result: null,
    });
    const createTrialRun = vi.spyOn(API, "createTrialRun").mockResolvedValue(FINISHED);
    const { rerender } = render(<EndpointTestSection definition={DEFINITION} providers={[]} />);

    const modelInputs = screen.getAllByLabelText("模型");
    await userEvent.type(modelInputs[1], "video-1");
    const start = screen.getByRole("button", { name: "开始测试" });
    expect(start).toBeDisabled();

    const file = new File(["image"], "start.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText(/首帧/), file);
    expect(start).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "预览" }));

    await waitFor(() => {
      expect(previewEndpointRequest).toHaveBeenCalledWith(
        expect.objectContaining({ parameters: { model: "video-1", prompt: "" } }),
        { assets: { start_image: [file] } },
      );
    });
    await userEvent.click(start);

    await waitFor(() => {
      expect(createTrialRun).toHaveBeenCalledWith(
        expect.objectContaining({ parameters: { model: "video-1", prompt: "" } }),
        { start_image: [file] },
      );
    });

    rerender(
      <EndpointTestSection
        definition={{
          ...DEFINITION,
          inputs: undefined,
          submit: { ...DEFINITION.submit, body: { model: "{{ model }}" } },
        }}
        providers={[]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "开始测试" }));

    await waitFor(() => expect(createTrialRun).toHaveBeenLastCalledWith(
      expect.objectContaining({ parameters: { model: "video-1", prompt: "" } }),
      {},
    ));
  });

  it("plays a successful artifact in place and links its API call to the spend ledger", async () => {
    vi.spyOn(API, "getTrialRunArtifact").mockResolvedValue(new Blob(["video"]));
    vi.spyOn(API, "createTrialRun").mockResolvedValue({
      ...RUNNING,
      status: "succeeded",
      finished_at: 2,
      api_call_id: 42,
      has_artifact: true,
    });
    const { unmount } = render(
      <EndpointTestSection
        definition={{ ...DEFINITION, inputs: undefined }}
        providers={[]}
      />,
    );

    await userEvent.type(screen.getAllByLabelText("模型")[1], "video-1");
    await userEvent.click(screen.getByRole("button", { name: "开始测试" }));

    expect(await screen.findByLabelText("测试连接产物")).toHaveAttribute(
      "src",
      "blob:trial-artifact",
    );
    expect(API.getTrialRunArtifact).toHaveBeenCalledWith("run-1", { signal: expect.any(AbortSignal) });
    expect(screen.getByRole("link", { name: "费用账本 #42" })).toHaveAttribute(
      "href",
      "/app/settings?section=usage&call_id=42#usage-call-42",
    );
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:trial-artifact");
  });
});
