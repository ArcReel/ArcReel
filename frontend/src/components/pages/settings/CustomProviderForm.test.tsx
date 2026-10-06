import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { CustomProviderInfo, CustomProviderModelInfo, EndpointDescriptor } from "@/types";
import { CustomProviderForm } from "./CustomProviderForm";

const CHAT_ENDPOINT: EndpointDescriptor = {
  key: "openai-chat",
  media_type: "text",
  family: "openai",
  kind: "python",
  source: "builtin",
  display_name_key: "endpoint_openai_chat",
  display_name: null,
  request_method: "POST",
  request_path_template: "/v1/chat/completions",
  image_capabilities: null,
  end_image_capable: false,
  size_fixed: false,
  duration_fixed: false,
  duration_frame_rate_missing: false,
  duration_tier_empty: false,
  native_resolution: null,
};

const IMAGE_ENDPOINT: EndpointDescriptor = {
  ...CHAT_ENDPOINT,
  key: "openai-images",
  media_type: "image",
  display_name: "图片生成",
  request_path_template: "/v1/images/generations",
  image_capabilities: ["text_to_image"],
};

const SETTINGS_PATH = "/app/settings?section=providers&custom=3";

function model(id: number, modelId: string, endpoint = "openai-chat"): CustomProviderModelInfo {
  return {
    id,
    model_id: modelId,
    display_name: modelId,
    endpoint,
    is_default: false,
    is_enabled: true,
    price_unit: null,
    price_input: null,
    price_output: null,
    currency: null,
    supported_durations: null,
    resolution: null,
    max_output_tokens: null,
    capability_overrides: null,
    system_capabilities: null,
    global_bucket_refs: [],
  };
}

function provider(models: CustomProviderModelInfo[]): CustomProviderInfo {
  return {
    id: 3,
    display_name: "我的中转站",
    discovery_format: "openai",
    base_url: "https://api.example.invalid",
    api_key_masked: "sk-***",
    models,
    created_at: "2026-01-01T00:00:00Z",
    image_max_workers: null,
    video_max_workers: null,
    audio_max_workers: null,
  };
}

function renderForm(props: Partial<Parameters<typeof CustomProviderForm>[0]> = {}) {
  const location = memoryLocation({ path: SETTINGS_PATH, record: true });
  const onSaved = props.onSaved ?? vi.fn();
  render(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <LeaveGuardProvider>
        <CustomProviderForm {...props} onSaved={onSaved} />
      </LeaveGuardProvider>
    </Router>,
  );
  return { onSaved, location };
}

function SavedStateProbe() {
  const [saved, setSaved] = useState(false);
  return (
    <>
      <CustomProviderForm onSaved={() => setSaved(true)} />
      {saved && <p>宿主已收到保存通知</p>}
    </>
  );
}

/** 填满新建表单的全部必填项：名称、接口地址、密钥，以及一行启用的模型。 */
function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "我的中转站" } });
  fireEvent.change(screen.getByLabelText("接口地址"), { target: { value: "https://api.example.invalid" } });
  fireEvent.change(screen.getByLabelText("密钥"), { target: { value: "sk-live" } });
  fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
  fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "gpt-4o" } });
}

describe("CustomProviderForm", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    // jsdom 不实现滚动；深链定位模型时会把展开的行滚进视野
    Element.prototype.scrollIntoView = vi.fn();
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [CHAT_ENDPOINT, IMAGE_ENDPOINT] });
    vi.spyOn(API, "createCustomProvider").mockRejectedValue(new Error("unexpected create"));
  });

  it("blocks the save and names the missing field in the save bar when the provider name is empty", async () => {
    const { onSaved } = renderForm();
    fillRequiredFields();
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "" } });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请填写供应商名称");
    expect(API.createCustomProvider).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("blocks the save when no model is enabled", async () => {
    renderForm();
    fillRequiredFields();
    fireEvent.click(screen.getByRole("checkbox", { name: "启用 gpt-4o" }));

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("至少启用一个模型");
    expect(API.createCustomProvider).not.toHaveBeenCalled();
  });

  it("creates the provider and notifies the host once the save succeeds", async () => {
    vi.mocked(API.createCustomProvider).mockResolvedValue(provider([]));
    render(<SavedStateProbe />);

    fillRequiredFields();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("宿主已收到保存通知")).toBeInTheDocument();
    expect(API.createCustomProvider).toHaveBeenCalledWith(
      expect.objectContaining({
        display_name: "我的中转站",
        discovery_format: "openai",
        base_url: "https://api.example.invalid",
        api_key: "sk-live",
        models: [expect.objectContaining({ model_id: "gpt-4o", endpoint: "openai-chat", is_enabled: true })],
      }),
    );
  });

  it("keeps the edits and shows the failure in the save bar when the save fails", async () => {
    vi.mocked(API.createCustomProvider).mockRejectedValue(new Error("网关拒绝"));
    const { onSaved } = renderForm();

    fillRequiredFields();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("网关拒绝");
    // 保存失败不能通知宿主：宿主会切走表单并当作已落库，用户的输入随之丢失
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "模型 ID" })).toHaveValue("gpt-4o");
  });

  it("expands and edits the model named by the deep link", async () => {
    renderForm({ existing: provider([model(1, "gpt-4o"), model(2, "gpt-4o-mini")]), focusModelId: "gpt-4o-mini" });

    expect(await screen.findByRole("textbox", { name: "模型 ID" })).toHaveValue("gpt-4o-mini");
    expect(screen.getByRole("button", { name: "编辑 gpt-4o-mini" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "编辑 gpt-4o" })).toHaveAttribute("aria-expanded", "false");
  });

  it("offers search and a type filter once there are more than five models", async () => {
    const user = userEvent.setup();
    const many = [
      model(1, "gpt-4o"),
      model(2, "gpt-4o-mini"),
      model(3, "claude-sonnet"),
      model(4, "deepseek-chat"),
      model(5, "qwen-max"),
      model(6, "gpt-image-1", "openai-images"),
    ];
    renderForm({ existing: provider(many) });
    await waitFor(() => expect(useEndpointCatalogStore.getState().initialized).toBe(true));
    const rowNames = () => screen.getAllByRole("button", { name: /^编辑 / }).map((b) => b.getAttribute("aria-label"));

    await user.type(screen.getByRole("searchbox", { name: "搜索模型…" }), "gpt");
    expect(rowNames()).toEqual(["编辑 gpt-4o", "编辑 gpt-4o-mini", "编辑 gpt-image-1"]);

    await user.click(within(screen.getByRole("group", { name: "按类型筛选" })).getByRole("button", { name: "图片" }));
    expect(rowNames()).toEqual(["编辑 gpt-image-1"]);
  });

  it("shows no search or type filter for five models or fewer", () => {
    renderForm({ existing: provider([model(1, "gpt-4o"), model(2, "gpt-4o-mini")]) });

    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "按类型筛选" })).not.toBeInTheDocument();
  });

  it("opens the model's endpoint with a way back to this provider", async () => {
    const user = userEvent.setup();
    const { location } = renderForm({ existing: provider([model(1, "gpt-4o")]), focusModelId: "gpt-4o" });

    await user.click(await screen.findByRole("link", { name: "在调用端点中打开" }));

    expect(location.history.at(-1)).toBe("/app/settings?section=endpoints&endpoint=openai-chat&from=3");
  });

  it("opens the market filtered to the media type of the model's endpoint", async () => {
    const user = userEvent.setup();
    const { location } = renderForm({
      existing: provider([model(1, "gpt-image-1", "openai-images")]),
      focusModelId: "gpt-image-1",
    });

    await user.click(await screen.findByRole("link", { name: "没有合适的端点？从市场获取" }));

    expect(location.history.at(-1)).toBe("/app/settings?section=market&media=image");
  });

  it("opens the market unfiltered for a text model, since the market has no text filter", async () => {
    const user = userEvent.setup();
    const { location } = renderForm({ existing: provider([model(1, "gpt-4o")]), focusModelId: "gpt-4o" });
    await screen.findByRole("link", { name: "在调用端点中打开" });

    await user.click(screen.getByRole("link", { name: "没有合适的端点？从市场获取" }));

    expect(location.history.at(-1)).toBe("/app/settings?section=market");
  });

  it("asks before opening the endpoint while the provider has unsaved edits", async () => {
    const user = userEvent.setup();
    const { location } = renderForm({ existing: provider([model(1, "gpt-4o")]), focusModelId: "gpt-4o" });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "改过的名字" } });

    await user.click(screen.getByRole("link", { name: "在调用端点中打开" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "继续编辑" }));

    expect(location.history.at(-1)).toBe(SETTINGS_PATH);
    expect(screen.getByLabelText("名称")).toHaveValue("改过的名字");
  });

  it("states the model count and that deletion is permanent before deleting the provider", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "deleteCustomProvider").mockResolvedValue();
    const onDeleted = vi.fn();
    renderForm({ existing: provider([model(1, "gpt-4o"), model(2, "gpt-4o-mini")]), onDeleted });

    await user.click(screen.getByRole("button", { name: "更多操作" }));
    await user.click(await screen.findByRole("menuitem", { name: "删除供应商" }));
    const dialog = await screen.findByRole("alertdialog", { name: "删除供应商「我的中转站」？" });
    expect(dialog).toHaveTextContent("它的 2 个模型会一起删除，删除后无法恢复。");
    await user.click(within(dialog).getByRole("button", { name: "删除供应商" }));

    await waitFor(() => expect(onDeleted).toHaveBeenCalled());
    expect(API.deleteCustomProvider).toHaveBeenCalledWith(3);
  });

  it("sets the key and model IDs in monospace, and leaves the base URL proportional", () => {
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));

    expect(screen.getByLabelText("密钥")).toHaveClass("font-mono");
    expect(screen.getByRole("textbox", { name: "模型 ID" })).toHaveClass("font-mono");
    expect(screen.getByLabelText("接口地址").closest(".font-mono")).toBeNull();
  });
});
