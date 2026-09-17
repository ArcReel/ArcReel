import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API, ApiRequestError } from "@/api";
import type {
  CustomEndpointInfo,
  EndpointValidateResponse,
  MarketEntry,
  MarketEntryDetail,
  MarketEntryInstallation,
} from "@/types";
import { newEndpointDefinition } from "../endpoints/endpoint-definition-draft";
import { MarketInstallDialog } from "./MarketInstallDialog";

const definition = newEndpointDefinition("Demo");
definition.meta = {
  name: "Demo",
  author: "Author",
  version: "1.0.0",
  hints: {
    base_url: "https://api.example.com",
    suggested_models: [{ id: "video", label: "Video model" }],
  },
};
definition.auth = { headers: { Authorization: "Bearer {{ api_key }}" } };
definition.submit.url = "https://api.example.com/submit";
definition.poll.url = "https://api.example.com/poll/{{ task_id }}";
const installation: MarketEntryInstallation = {
  endpoint_id: 7,
  endpoint_key: "ce-7",
  endpoint_display_name: "Demo",
  installed_version: "1.0.0",
  state: "current",
  modified: false,
};
const entry: MarketEntry = {
  source_id: 1,
  source_display_name: "Team",
  type: "endpoint",
  slug: "demo",
  path: "endpoints/demo/definition.json",
  name: "Demo",
  author: "Author",
  version: "1.0.0",
  media_type: "video",
  description: "Video endpoint",
  homepage: "https://example.com",
  icon: null,
  min_app_version: null,
  min_app_version_satisfied: true,
  installation: null,
};
const detail: MarketEntryDetail = {
  entry,
  source: {
    id: 1,
    kind: "custom",
    display_name: "Team",
    canonical_key: "url:team",
    is_enabled: true,
    status: "ok",
    fetched_at: null,
    index: null,
  },
  app_version: "0.30.0",
};
const endpoint: CustomEndpointInfo = {
  id: 7,
  key: "ce-7",
  display_name: "Demo",
  kind: "declarative",
  schema_version: "1.1.0",
  media_type: "video",
  definition,
  created_at: null,
  updated_at: null,
  installation: null,
};
const validation: EndpointValidateResponse = {
  errors: [],
  warnings: [],
  duplicates: [],
  hints: definition.meta.hints!,
  schema_version: { file: "1.1.0", current: "1.1.0", level: "direct" },
  min_app_version: null,
};

function show(selected = entry) {
  const onClose = vi.fn();
  const onInstallationChange = vi.fn();
  const location = memoryLocation({ path: "/settings?section=market", record: true });
  render(
    <Router hook={location.hook}>
      <MarketInstallDialog entry={selected} onClose={onClose} onInstallationChange={onInstallationChange} />
    </Router>,
  );
  return { onClose, onInstallationChange, location };
}

describe("MarketInstallDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "getMarketEntry").mockResolvedValue(detail);
    vi.spyOn(API, "getMarketEntryDefinition").mockResolvedValue({
      definition,
      entry_matches_definition: true,
    });
    vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({ endpoints: [] });
    vi.spyOn(API, "validateCustomEndpoint").mockResolvedValue(validation);
    vi.spyOn(API, "installMarketEntry").mockResolvedValue({
      endpoint,
      installation,
    });
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: [] });
    vi.spyOn(API, "deleteCustomEndpoint").mockResolvedValue(undefined);
  });

  it("shows trust and hints before installing, then offers provider creation with the saved key", async () => {
    const { onInstallationChange, location } = show();
    const confirm = await screen.findByRole("button", { name: "确认安装" });
    await waitFor(() => expect(confirm).toBeEnabled());
    expect(screen.getByText("该来源未经 ArcReel 审核")).toBeInTheDocument();
    expect(screen.getByText(definition.submit.url)).toBeInTheDocument();
    expect(screen.getByText(definition.poll.url)).toBeInTheDocument();
    expect(screen.getByText(/Bearer/)).toBeVisible();
    expect(screen.getByText("Video model")).toBeInTheDocument();
    await userEvent.click(confirm);
    expect(await screen.findByText("安装成功")).toBeInTheDocument();
    expect(onInstallationChange).toHaveBeenCalledWith(installation);
    expect(API.installMarketEntry).toHaveBeenCalledWith(1, "demo", undefined);
    await userEvent.click(screen.getByRole("button", { name: "用此端点新建供应商" }));
    expect(location.history?.at(-1)).toContain("endpoint=ce-7");
    expect(location.history?.at(-1)).toContain("base_url=https%3A%2F%2Fapi.example.com");
  });

  it.each([
    ["mismatch", "索引与定义不一致，无法安装"],
    ["errors", "Invalid submit URL"],
    ["version", "需要 ArcReel ≥ 99.0.0"],
  ])("blocks installation for %s and shows the reason", async (reason, shownReason) => {
    if (reason === "mismatch")
      vi.mocked(API.getMarketEntryDefinition).mockResolvedValue({
        definition,
        entry_matches_definition: false,
      });
    if (reason === "errors")
      vi.mocked(API.validateCustomEndpoint).mockResolvedValue({
        ...validation,
        errors: [
          {
            path: "submit.url",
            code: "invalid",
            message: "Invalid submit URL",
          },
        ],
      });
    if (reason === "version")
      vi.mocked(API.validateCustomEndpoint).mockResolvedValue({
        ...validation,
        min_app_version: {
          required: "99.0.0",
          current: "0.30.0",
          satisfied: false,
        },
      });
    show();
    await screen.findByText("凭证发往");
    expect(screen.getByText(shownReason)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认安装" })).toBeDisabled();
    expect(API.installMarketEntry).not.toHaveBeenCalled();
  });

  it("renders malformed trust fields as text while showing validation errors", async () => {
    vi.mocked(API.getMarketEntryDefinition).mockResolvedValue({
      definition: { ...definition, submit: { ...definition.submit, url: { invalid: "URL" } } },
      entry_matches_definition: true,
    });
    vi.mocked(API.validateCustomEndpoint).mockResolvedValue({
      ...validation,
      hints: null,
      errors: [{ path: "submit.url", code: "invalid", message: "URL must be a string" }],
    });
    show();
    expect(await screen.findByText("URL must be a string")).toBeInTheDocument();
    expect(screen.getByText('{"invalid":"URL"}')).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认安装" })).toBeDisabled();
  });

  it("allows overwriting local definitions but only displays endpoints installed from another source", async () => {
    const duplicate = {
      id: 7,
      key: "ce-7",
      display_name: "Local",
      version: "1.0.0",
      relation: "same" as const,
    };
    vi.mocked(API.validateCustomEndpoint).mockResolvedValue({
      ...validation,
      duplicates: [duplicate, { ...duplicate, id: 8, key: "ce-8", display_name: "Other" }],
    });
    vi.mocked(API.listCustomEndpoints).mockResolvedValue({
      endpoints: [
        endpoint,
        {
          ...endpoint,
          id: 8,
          installation: {
            source_key: "url:other",
            source_id: 2,
            source_display_name: "Other source",
            slug: "demo",
            installed_version: "1.0.0",
            installed_at: "2026-09-17",
            state: "current",
            modified: false,
          },
        },
      ],
    });
    show();
    expect(await screen.findByText("已从 Other source 安装")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /Other/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Local/ }));
    await userEvent.click(screen.getByRole("button", { name: "确认安装" }));
    expect(await screen.findByText("安装成功")).toBeInTheDocument();
    expect(API.installMarketEntry).toHaveBeenCalledWith(1, "demo", 7);
  });

  it("reuses the reference list when uninstall is rejected and navigates to the model", async () => {
    vi.mocked(API.getMarketEntry).mockResolvedValue({
      ...detail,
      entry: { ...entry, installation },
    });
    vi.mocked(API.deleteCustomEndpoint).mockRejectedValue(
      new ApiRequestError(
        "Referenced",
        {
          references: [
            {
              provider_id: 3,
              provider_display_name: "Provider",
              model_id: "video",
              model_display_name: "Video",
            },
          ],
        },
        409,
      ),
    );
    const { location, onClose } = show({ ...entry, installation });
    await userEvent.click(screen.getByRole("button", { name: "卸载" }));
    const reference = await screen.findByRole("button", {
      name: /Provider · Video/,
    });
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(reference);
    expect(location.history?.at(-1)).toContain("custom=3&model=video");
  });

  it("uninstalls through endpoint deletion and removes the installation from the grid", async () => {
    vi.mocked(API.getMarketEntry).mockResolvedValue({
      ...detail,
      entry: { ...entry, installation },
    });
    const { onClose, onInstallationChange } = show({ ...entry, installation });
    await userEvent.click(screen.getByRole("button", { name: "卸载" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(API.deleteCustomEndpoint).toHaveBeenCalledWith(7);
    expect(onInstallationChange).toHaveBeenCalledWith(null);
  });
});
