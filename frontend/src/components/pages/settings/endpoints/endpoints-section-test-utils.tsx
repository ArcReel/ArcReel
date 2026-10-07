import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, vi } from "vitest";
import { Router, useSearch } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  CustomEndpointInfo,
  EndpointDefinition,
  EndpointDescriptor,
  EndpointValidateResponse,
} from "@/types";
import { EndpointsSection } from "./EndpointsSection";

// EndpointsSection 的测试按行为域拆成多个文件（主流程、ComfyUI、市场集成、分享到官方市场），
// 样本数据、渲染入口与对话框驱动都在这里，各文件只写用例。

export function makeDefinition(overrides?: Partial<EndpointDefinition>): EndpointDefinition {
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

export const MINE: CustomEndpointInfo = {
  installation: null,
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

export const COMFYUI_MINE: CustomEndpointInfo = {
  installation: null,
  id: 8,
  key: "ce-8",
  display_name: "我的 ComfyUI",
  kind: "comfyui",
  schema_version: "1.0.0",
  media_type: "video",
  definition: {
    kind: "comfyui",
    schema_version: "1.0.0",
    meta: { name: "我的 ComfyUI", author: "Ada", version: "1.0.0" },
    media_type: "video",
    workflow: { "9": { class_type: "SaveVideo", inputs: {} } },
    bindings: { prompt: [{ node: "6", input: "text", class_type: "CLIPTextEncode" }] },
  },
  created_at: null,
  updated_at: null,
};

export function descriptor(overrides: Partial<EndpointDescriptor>): EndpointDescriptor {
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
    size_fixed: false,
    duration_fixed: false,
    duration_frame_rate_missing: false,
    duration_tier_empty: false,
    native_resolution: null,
    ...overrides,
  };
}

export const CATALOG: EndpointDescriptor[] = [
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
    kind: "python",
    media_type: "image",
    source: "builtin",
    display_name: "OpenAI Image",
  }),
];

export function validation(overrides?: Partial<EndpointValidateResponse>): EndpointValidateResponse {
  return {
    errors: [],
    warnings: [],
    duplicates: [],
    hints: null,
    schema_version: { file: "1.0.0", current: "1.0.0", level: "direct" },
    min_app_version: null,
    import_shape: "endpoint_definition",
    wrapped_definition: null,
    ...overrides,
  };
}

/** 与设置页一样，只在 section=endpoints 时渲染本分区；跳去别的分区后它随之卸载。 */
function SectionHost() {
  const search = useSearch();
  return new URLSearchParams(search).get("section") === "endpoints" ? <EndpointsSection /> : null;
}

export function renderSection(search = "section=endpoints", { guarded = false } = {}) {
  const location = memoryLocation({ path: "/app/settings", searchPath: search, record: true });
  const host = guarded ? (
    <LeaveGuardProvider>
      <SectionHost />
    </LeaveGuardProvider>
  ) : (
    <SectionHost />
  );
  return { ...render(<Router hook={location.hook}>{host}</Router>), location };
}

/** 点二级栏里的条目。标准档与图标栏各渲染一份（jsdom 不跑容器查询），取第一份。 */
export async function clickRail(name: string | RegExp) {
  await userEvent.click((await screen.findAllByRole("link", { name }))[0]);
}

/** 打开页头「更多操作」菜单里的一项。 */
export async function chooseMenu(name: string) {
  await userEvent.click(await screen.findByRole("button", { name: "更多操作" }));
  await userEvent.click(await screen.findByRole("menuitem", { name }));
}

/**
 * 从「新建端点」页打开导入对话框。新建页会先校验一次空白定义，等它发出后清掉调用记录，
 * 后面的断言只看导入这一路的校验。
 */
async function openImport() {
  // 方法已在 stubEndpointsSection 里替身化，这里再 spyOn 拿到的是同一个 spy。
  const validate = vi.spyOn(API, "validateCustomEndpoint");
  await clickRail("新建端点");
  await waitFor(() => expect(validate).toHaveBeenCalled());
  validate.mockClear();
  await userEvent.click(await screen.findByRole("button", { name: "导入定义" }));
}

/** 导入对话框里的文件选择框；新建页的端点测试区也有上传框，只在对话框里找。 */
function importFileInput(): HTMLInputElement {
  const picker = screen.getByRole("dialog").querySelector<HTMLInputElement>('input[type="file"]');
  if (picker === null) throw new Error("no file input");
  return picker;
}

/** 在导入弹窗里选一份文件。隐藏的 file input 在 jsdom 里只能这样驱动。 */
export async function pickFile(file: File) {
  await openImport();
  fireEvent.change(importFileInput(), { target: { files: [file] } });
}

/** 在导入弹窗里粘贴一份载荷，与上传走同一条分流。 */
export async function pasteSource(text: string) {
  await openImport();
  await userEvent.click(screen.getByLabelText("粘贴端点定义或 workflow"));
  await userEvent.paste(text);
  await userEvent.click(screen.getByRole("button", { name: "识别" }));
}

export function captureDownloads() {
  const downloads: { name: string; blob: Blob }[] = [];
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn((blob: Blob) => {
      downloads.push({ name: "", blob });
      return "blob:definition";
    }),
  });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    downloads[downloads.length - 1].name = this.download;
  });
  return downloads;
}


/** 各拆分文件共用的起点：空 store、目录里只有 MINE、列表与校验接口的替身就位。 */
export function stubEndpointsSection() {
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
}
