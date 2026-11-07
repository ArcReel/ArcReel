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

// EndpointsSection tests split by behavior (main flow, ComfyUI, market integration, official-market sharing);
// shared fixtures/rendering/dialog drivers live here, leaving tests in individual files.

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
    requests: [],
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

/** Like settings pages, render only for section=endpoints, unmounting after other-section navigation. */
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

/** Click sidebar entries; regular/icon layouts both render in jsdom without container queries, so choose first. */
export async function clickRail(name: string | RegExp) {
  await userEvent.click((await screen.findAllByRole("link", { name }))[0]);
}

/** Open header More menu items. */
export async function chooseMenu(name: string) {
  await userEvent.click(await screen.findByRole("button", { name: "更多操作" }));
  await userEvent.click(await screen.findByRole("menuitem", { name }));
}

/**
 * Open import dialogs from New endpoint pages. Creation first validates blank definitions; await dispatch then clear calls,
 * isolating later assertions to import validation.
 */
async function openImport() {
  // stubEndpointsSection already fakes methods; spyOn returns those same spies.
  const validate = vi.spyOn(API, "validateCustomEndpoint");
  await clickRail("新建端点");
  await waitFor(() => expect(validate).toHaveBeenCalled());
  validate.mockClear();
  await userEvent.click(await screen.findByRole("button", { name: "导入定义" }));
}

/** Import-dialog file inputs, scoped because new-page endpoint tests also contain upload inputs. */
function importFileInput(): HTMLInputElement {
  const picker = screen.getByRole("dialog").querySelector<HTMLInputElement>('input[type="file"]');
  if (picker === null) throw new Error("no file input");
  return picker;
}

/** Select files in import dialogs; hidden inputs in jsdom require this driver. */
export async function pickFile(file: File) {
  await openImport();
  fireEvent.change(importFileInput(), { target: { files: [file] } });
}

/** Paste import payloads, sharing upload routing. */
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


/** Shared starting point: empty stores, catalogs containing MINE only, list/validation fakes installed. */
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
