import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import type { EndpointDefinition, TrialRunInfo } from "@/types";
import { EndpointTestSection } from "./EndpointTestSection";

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
  poll_responses: [],
  extractions: {},
  video_url: null,
  duration_seconds: null,
  error: null,
  has_artifact: false,
};

describe("EndpointTestSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uploads a required endpoint asset under its multipart source name", async () => {
    const createTrialRun = vi.spyOn(API, "createTrialRun").mockResolvedValue(RUNNING);
    render(<EndpointTestSection definition={DEFINITION} providers={[]} />);

    const modelInputs = screen.getAllByLabelText("模型");
    await userEvent.type(modelInputs[1], "video-1");
    const start = screen.getByRole("button", { name: "开始测试" });
    expect(start).toBeDisabled();

    const file = new File(["image"], "start.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText(/首帧/), file);
    expect(start).toBeEnabled();
    await userEvent.click(start);

    await waitFor(() => {
      expect(createTrialRun).toHaveBeenCalledWith(
        expect.objectContaining({ parameters: { model: "video-1", prompt: "" } }),
        { start_image: [file] },
      );
    });
  });

  it("plays a successful artifact in place and links its API call to the spend ledger", async () => {
    vi.spyOn(API, "createTrialRun").mockResolvedValue({
      ...RUNNING,
      status: "succeeded",
      finished_at: 2,
      api_call_id: 42,
      has_artifact: true,
    });
    render(
      <EndpointTestSection
        definition={{ ...DEFINITION, inputs: undefined }}
        providers={[]}
      />,
    );

    await userEvent.type(screen.getAllByLabelText("模型")[1], "video-1");
    await userEvent.click(screen.getByRole("button", { name: "开始测试" }));

    expect(await screen.findByLabelText("测试连接产物")).toHaveAttribute(
      "src",
      "/api/v1/custom-endpoints/trial-runs/run-1/artifact",
    );
    expect(screen.getByRole("link", { name: "费用账本 #42" })).toHaveAttribute(
      "href",
      "/app/settings?section=usage&call_id=42#usage-call-42",
    );
  });
});
