import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import "@/i18n";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  CustomEndpointInfo,
  EndpointDefinition,
  EndpointDescriptor,
  EndpointValidateResponse,
} from "@/types";
import { EndpointsSection } from "./EndpointsSection";

function makeDefinition(overrides?: Partial<EndpointDefinition>): EndpointDefinition {
  return {
    kind: "declarative",
    schema_version: "1.0.0",
    meta: { name: "Example Video API", author: "Ada", version: "1.0.0" },
    auth: { headers: { Authorization: "Bearer {{ api_key }}" } },
    submit: {
      method: "POST",
      url: "{{ base_url }}/v1/videos",
      body: { model: "{{ model }}" },
      extract: { task_id: ["$.id"] },
    },
    poll: {
      method: "GET",
      url: "{{ base_url }}/v1/videos/{{ task_id }}",
      extract: { status: ["$.status"], video_url: ["$.data.video_url"] },
    },
    status_map: { succeeded: "succeeded" },
    ...overrides,
  };
}

const MINE: CustomEndpointInfo = {
  id: 7,
  key: "ce-7",
  display_name: "Example Video API",
  kind: "declarative",
  schema_version: "1.0.0",
  media_type: "video",
  definition: makeDefinition(),
  created_at: null,
  updated_at: null,
};

function descriptor(overrides: Partial<EndpointDescriptor>): EndpointDescriptor {
  return {
    key: "ce-7",
    media_type: "video",
    family: "custom",
    kind: "declarative",
    source: "custom",
    display_name_key: "",
    display_name: "Example Video API",
    request_method: "POST",
    request_path_template: "/v1/videos",
    image_capabilities: null,
    end_image_capable: false,
    ...overrides,
  };
}

const CATALOG: EndpointDescriptor[] = [
  descriptor({}),
  descriptor({
    key: "newapi-video",
    family: "newapi",
    source: "builtin",
    display_name: "NewAPI Video",
  }),
  descriptor({
    key: "openai_video",
    family: "openai",
    kind: "python",
    source: "builtin",
    display_name: null,
    display_name_key: "endpoint_openai_video_display",
  }),
  descriptor({
    key: "openai-image",
    media_type: "image",
    display_name: "OpenAI Image",
  }),
];

function validation(overrides?: Partial<EndpointValidateResponse>): EndpointValidateResponse {
  return {
    errors: [],
    warnings: [],
    duplicates: [],
    hints: null,
    schema_version: { file: "1.0.0", current: "1.0.0", level: "direct" },
    ...overrides,
  };
}

function renderSection(search = "section=endpoints") {
  const location = memoryLocation({ path: "/app/settings", searchPath: search, record: true });
  return {
    ...render(
      <Router hook={location.hook}>
        <EndpointsSection />
      </Router>,
    ),
    location,
  };
}

describe("EndpointsSection", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useEndpointCatalogStore.setState({
      endpoints: CATALOG,
      loading: false,
      initialized: true,
    });
    vi.restoreAllMocks();
    vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({ endpoints: [MINE] });
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(useEndpointCatalogStore.getState(), "refresh").mockResolvedValue(undefined);
    vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(validation());
  });

  it("groups endpoints by whether they are mine, built-in, or implemented in code", async () => {
    renderSection();
    const list = await screen.findByRole("navigation");
    expect(within(list).getByText("我的端点")).toBeInTheDocument();
    expect(within(list).getByText("内置")).toBeInTheDocument();
    expect(within(list).getByText("内置 · Python")).toBeInTheDocument();
  });

  it("lists only video endpoints", async () => {
    renderSection();
    const list = await screen.findByRole("navigation");
    expect(within(list).queryByText("OpenAI Image")).not.toBeInTheDocument();
  });

  it("shows an editable lifecycle form for one of my endpoints", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    expect(await screen.findByDisplayValue("Example Video API")).toBeEnabled();
    expect(screen.getByRole("button", { name: "保存更改" })).toBeInTheDocument();
    expect(screen.getByText("提交生成任务")).toBeInTheDocument();
  });

  it("surfaces validation errors on the diagnostics card and blocks saving", async () => {
    vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(
      validation({
        errors: [
          {
            path: "poll.extract.video_url[0]",
            code: "jsonpath_recursive_descent",
            message: "不支持递归下降语法",
          },
        ],
      }),
    );
    renderSection("section=endpoints&endpoint=ce-7");
    expect(await screen.findByText("不支持递归下降语法")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "保存更改" })).toBeDisabled(),
    );
  });

  it("saves an edited definition through the update endpoint", async () => {
    const update = vi
      .spyOn(API, "updateCustomEndpoint")
      .mockResolvedValue({ ...MINE, display_name: "Renamed" });
    renderSection("section=endpoints&endpoint=ce-7");
    const nameField = await screen.findByDisplayValue("Example Video API");
    await userEvent.type(nameField, "!");
    const save = screen.getByRole("button", { name: "保存更改" });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await waitFor(() => expect(update).toHaveBeenCalledOnce());
    expect(update.mock.calls[0][0]).toBe(7);
    expect((update.mock.calls[0][1] as EndpointDefinition).meta.name).toBe(
      "Example Video API!",
    );
  });

  it("shows server references when deletion conflicts and offers a model-row jump", async () => {
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({
      providers: [
        {
          id: 1,
          display_name: "Relay",
          discovery_format: "openai",
          base_url: "https://api.example.com",
          api_key_masked: "sk-***",
          created_at: "2026-08-01T00:00:00Z",
          image_max_workers: null,
          video_max_workers: null,
          audio_max_workers: null,
          models: [
            {
              id: 11,
              model_id: "example-video",
              display_name: "example-video",
              endpoint: "ce-7",
              is_default: true,
              is_enabled: true,
              price_unit: null,
              price_input: null,
              price_output: null,
              currency: null,
              supported_durations: null,
              resolution: null,
              system_capabilities: null,
              capability_overrides: null,
              global_bucket_refs: null,
            },
          ],
        },
      ],
    });
    vi.spyOn(API, "deleteCustomEndpoint").mockRejectedValue(
      new ApiRequestError(
        "Models are using this endpoint.",
        {
          references: [
            {
              provider_id: 1,
              provider_display_name: "Relay",
              model_id: "example-video",
              model_display_name: "Example Video",
            },
          ],
        },
        409,
      ),
    );
    const { location } = renderSection("section=endpoints&endpoint=ce-7");
    expect(await screen.findByText("1 个模型正在使用")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    await userEvent.click(screen.getAllByRole("button", { name: "删除" }).at(-1)!);
    const jump = await screen.findByRole("button", { name: "Relay · Example Video — 前往模型行" });
    await userEvent.click(jump);
    expect(location.history.at(-1)).toBe(
      "/app/settings?section=providers&custom=1&model=example-video",
    );
  });

  it("offers a copy of a built-in declarative endpoint instead of editing it", async () => {
    vi.spyOn(API, "getBuiltinEndpointDefinition").mockResolvedValue(
      makeDefinition({ meta: { name: "NewAPI Video", author: "ArcReel", version: "1.0.0" } }),
    );
    const create = vi.spyOn(API, "createCustomEndpoint").mockResolvedValue(MINE);
    renderSection("section=endpoints&endpoint=newapi-video");

    expect(await screen.findByDisplayValue("NewAPI Video")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "保存更改" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "复制为我的" }));
    await waitFor(() => expect(create).toHaveBeenCalledOnce());
  });

  it("keeps focus in a key field while its name is being typed", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    const nameField = await screen.findByLabelText("请求头名称");
    await userEvent.clear(nameField);
    await userEvent.type(nameField, "X-Token");
    expect(screen.getByLabelText("请求头名称")).toHaveFocus();
    expect(screen.getByLabelText("请求头名称")).toHaveValue("X-Token");
  });

  it("rejects a duplicate key with a toast without overwriting either row", async () => {
    vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({
      endpoints: [
        {
          ...MINE,
          definition: makeDefinition({
            auth: {
              headers: {
                Authorization: "Bearer {{ api_key }}",
                "X-Token": "abc",
              },
            },
          }),
        },
      ],
    });
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    renderSection("section=endpoints&endpoint=ce-7");
    const [nameField] = await screen.findAllByLabelText("请求头名称");

    fireEvent.change(nameField, { target: { value: "X-Token" } });

    expect(screen.getAllByLabelText("请求头名称").map((field) => field.getAttribute("value"))).toEqual([
      "Authorization",
      "X-Token",
    ]);
    expect(screen.getAllByLabelText("请求头内容").map((field) => field.getAttribute("value"))).toEqual([
      "Bearer {{ api_key }}",
      "abc",
    ]);
    expect(pushToast).toHaveBeenCalledWith("该名称已被使用，请换一个名称。", "error");
  });

  it("asks for the new row to be named before another one can be added", async () => {
    renderSection("section=endpoints&endpoint=ce-7");
    const add = await screen.findByRole("button", { name: "添加请求头" });
    await userEvent.click(add);
    expect(screen.getAllByLabelText("请求头名称")).toHaveLength(2);
    expect(add).toBeDisabled();
    expect(screen.getByText("先为新增的这一行填写名称，再添加下一行。")).toBeInTheDocument();
  });

  it("shows only the request details for an endpoint implemented in code", async () => {
    renderSection("section=endpoints&endpoint=openai_video");
    expect(await screen.findByText("该端点由代码实现，仅展示接口信息。")).toBeInTheDocument();
    expect(screen.getByText("/v1/videos")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制为我的" })).not.toBeInTheDocument();
  });
});
