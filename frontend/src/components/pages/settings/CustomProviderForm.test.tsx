import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { EndpointDescriptor } from "@/types";
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
};

function renderForm(onSaved = vi.fn()) {
  render(<CustomProviderForm onSaved={onSaved} onCancel={vi.fn()} />);
  return onSaved;
}

function SavedStateProbe() {
  const [saved, setSaved] = useState(false);
  return (
    <>
      <CustomProviderForm onSaved={() => setSaved(true)} onCancel={() => undefined} />
      {saved && <p>宿主已收到保存通知</p>}
    </>
  );
}

/** 填满新建表单的全部必填项：名称、Base URL、API Key，以及一行启用的模型。 */
function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "我的中转站" } });
  fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: "https://api.example.invalid" } });
  fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: "sk-live" } });
  fireEvent.click(screen.getByRole("button", { name: "手动添加模型" }));
  fireEvent.change(screen.getByRole("textbox", { name: "模型 ID" }), { target: { value: "gpt-4o" } });
}

describe("CustomProviderForm", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [CHAT_ENDPOINT] });
    vi.spyOn(API, "createCustomProvider").mockRejectedValue(new Error("unexpected create"));
  });

  it("blocks the save and names the missing field when the provider name is empty", async () => {
    const onSaved = renderForm();

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({ text: "请填写供应商名称", tone: "error" }),
    );
    expect(API.createCustomProvider).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("blocks the save when no model is enabled", async () => {
    const onSaved = renderForm();

    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "我的中转站" } });
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: "https://api.example.invalid" } });
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: "sk-live" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({ text: "至少启用一个模型", tone: "error" }),
    );
    expect(API.createCustomProvider).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("blocks the save and names the missing field when the base URL is empty", async () => {
    const onSaved = renderForm();
    fillRequiredFields();
    fireEvent.change(screen.getByLabelText(/Base URL/), { target: { value: "" } });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({ text: "请填写 Base URL", tone: "error" }),
    );
    expect(API.createCustomProvider).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("blocks the save and names the missing field when the API key is empty", async () => {
    const onSaved = renderForm();
    fillRequiredFields();
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: "" } });

    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({ text: "请填写 API Key", tone: "error" }),
    );
    expect(API.createCustomProvider).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("creates the provider and notifies the host once the save succeeds", async () => {
    vi.mocked(API.createCustomProvider).mockResolvedValue({
      id: 7,
      display_name: "我的中转站",
      discovery_format: "openai",
      base_url: "https://api.example.invalid",
      api_key_masked: "sk-***",
      models: [],
      created_at: "2026-01-01T00:00:00Z",
      image_max_workers: null,
      video_max_workers: null,
      audio_max_workers: null,
    });
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

  it("surfaces the failure and keeps the host on the form when the save fails", async () => {
    vi.mocked(API.createCustomProvider).mockRejectedValue(new Error("网关拒绝"));
    const onSaved = renderForm();

    fillRequiredFields();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast).toMatchObject({ text: "保存失败: 网关拒绝", tone: "error" }),
    );
    // 保存失败不能通知宿主：宿主会收起表单并当作已落库，用户的输入随之丢失
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "保存" })).toBeInTheDocument();
  });
});
