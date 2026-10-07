import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { EndpointInstallation, MarketEntry } from "@/types";
import { MARKET_CONTRIBUTING_URL } from "../market/market-links";
import {
  MINE,
  captureDownloads,
  chooseMenu,
  clickRail,
  makeDefinition,
  renderSection,
  stubEndpointsSection,
} from "./endpoints-section-test-utils";

describe("EndpointsSection", () => {
  beforeEach(stubEndpointsSection);

  describe("market integration", () => {
    const INSTALLATION: EndpointInstallation = {
      source_key: "github:arcreel/arcreel-market@HEAD",
      source_id: 1,
      source_display_name: "ArcReel 官方市场",
      source_enabled: true,
      slug: "kling-master",
      installed_version: "1.0.0",
      installed_at: "2026-09-01T00:00:00+00:00",
      state: "current",
      modified: false,
    };

    function withInstallation(overrides: Partial<EndpointInstallation>) {
      vi.spyOn(API, "listCustomEndpoints").mockResolvedValue({
        endpoints: [{ ...MINE, installation: { ...INSTALLATION, ...overrides } }],
      });
    }

    it("links from the new-endpoint page to the market section", async () => {
      const { location } = renderSection("section=endpoints&endpoint=new");
      await userEvent.click(await screen.findByRole("link", { name: "从市场获取" }));
      expect(location.history.at(-1)).toBe("/app/settings?section=market&media=video");
    });

    it("carries the media type of the template in use to the market", async () => {
      const { location } = renderSection("section=endpoints&endpoint=new");
      await userEvent.click(await screen.findByRole("combobox", { name: "示例模板" }));
      await userEvent.click(await screen.findByRole("option", { name: "图片：提交 + 轮询" }));
      await userEvent.click(await screen.findByRole("link", { name: "从市场获取" }));
      expect(location.history.at(-1)).toBe("/app/settings?section=market&media=image");
    });

    it("shows both status axes and the source of an installed endpoint without an update action", async () => {
      withInstallation({ modified: true });
      renderSection("section=endpoints&endpoint=ce-7");
      expect(await screen.findByText("来自市场 ArcReel 官方市场")).toBeInTheDocument();
      expect(screen.getByText("自定义")).toBeInTheDocument();
      expect(screen.getByText("已安装")).toBeInTheDocument();
      expect(screen.getByText("已修改")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "更新" })).not.toBeInTheDocument();
    });

    it("leaves a hand-made endpoint without market badges", async () => {
      renderSection("section=endpoints&endpoint=ce-7");
      expect(await screen.findByDisplayValue("Example Video API")).toBeInTheDocument();
      expect(screen.queryByText(/来自市场/)).not.toBeInTheDocument();
      expect(screen.queryByText("已安装")).not.toBeInTheDocument();
    });

    it("names a disabled source and falls back to the canonical key once the source is deleted", async () => {
      withInstallation({ state: "unavailable", source_enabled: false });
      const { unmount } = renderSection("section=endpoints&endpoint=ce-7");
      expect(await screen.findByText("来自市场 ArcReel 官方市场（来源已禁用）")).toBeInTheDocument();
      expect(screen.getByText("市场中不可用")).toBeInTheDocument();
      unmount();

      withInstallation({ state: "unavailable", source_id: null, source_display_name: null, source_enabled: null });
      renderSection("section=endpoints&endpoint=ce-7");
      expect(
        await screen.findByText("来自市场 github:arcreel/arcreel-market@HEAD（来源已删除）"),
      ).toBeInTheDocument();
    });

    it("opens the install dialog in update mode from the update action", async () => {
      withInstallation({ state: "update_available" });
      const entry: MarketEntry = {
        source_id: 1,
        source_display_name: "ArcReel 官方市场",
        type: "endpoint",
        slug: "kling-master",
        path: "endpoints/kling-master/definition.json",
        name: "Example Video API",
        author: "Ada",
        version: "1.1.0",
        media_type: "video",
        description: null,
        homepage: null,
        icon: null,
        min_app_version: null,
        min_app_version_satisfied: true,
        installation: {
          endpoint_id: 7,
          endpoint_key: "ce-7",
          endpoint_display_name: "Example Video API",
          installed_version: "1.0.0",
          state: "update_available",
          modified: false,
        },
      };
      vi.spyOn(API, "getMarketEntry").mockResolvedValue({
        entry,
        source: {
          id: 1,
          kind: "official",
          display_name: "ArcReel 官方市场",
          canonical_key: INSTALLATION.source_key,
          is_enabled: true,
          status: "ok",
          fetched_at: null,
          index: null,
        },
        app_version: null,
      });
      vi.spyOn(API, "getMarketEntryDefinition").mockResolvedValue({
        definition: makeDefinition({ meta: { name: "Example Video API", author: "Ada", version: "1.1.0" } }),
        entry_matches_definition: true,
        definition_digest: "reviewed-digest",
      });
      renderSection("section=endpoints&endpoint=ce-7");

      await screen.findByRole("button", { name: "更新" });
      await userEvent.type(screen.getByDisplayValue("Example Video API"), "!");
      await userEvent.click(screen.getByRole("button", { name: "更新" }));
      expect(await screen.findByRole("button", { name: "更新到 v1.1.0" })).toBeInTheDocument();
      expect(API.getMarketEntry).toHaveBeenCalledWith(1, "kling-master", expect.anything());
      expect(await screen.findByText("你的本地修改会被覆盖")).toBeInTheDocument();
      const downloads = captureDownloads();
      await userEvent.click(screen.getByRole("button", { name: "先导出当前定义" }));
      expect(JSON.parse(await downloads[0].blob.text()).meta.name).toBe("Example Video API!");
      expect(await screen.findByRole("button", { name: "更新到 v1.1.0" })).toBeInTheDocument();
    });

    it("locks editing and saving while the entry for an update is loading", async () => {
      withInstallation({ state: "update_available" });
      vi.spyOn(API, "getMarketEntry").mockReturnValue(new Promise(() => undefined));
      renderSection("section=endpoints&endpoint=ce-7");

      const nameField = await screen.findByDisplayValue("Example Video API");
      await userEvent.type(nameField, "!");
      await userEvent.click(screen.getByRole("button", { name: "更新" }));

      expect(nameField).toHaveAttribute("readonly");
    });

    it("aborts an entry request when the selected endpoint changes", async () => {
      withInstallation({ state: "update_available" });
      let signal: AbortSignal | undefined;
      vi.spyOn(API, "getMarketEntry").mockImplementation((_sourceId, _slug, options) => {
        signal = options?.signal;
        return new Promise(() => undefined);
      });
      renderSection("section=endpoints&endpoint=ce-7");

      await userEvent.click(await screen.findByRole("button", { name: "更新" }));
      await waitFor(() => expect(signal).toBeDefined());
      await clickRail("新建端点");
      expect(signal?.aborted).toBe(true);
      expect(screen.queryByText("Update endpoint")).not.toBeInTheDocument();
    });

    it("reports an entry request failure and restores the update action", async () => {
      withInstallation({ state: "update_available" });
      vi.spyOn(API, "getMarketEntry").mockRejectedValue(new Error("entry unavailable"));
      const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
      renderSection("section=endpoints&endpoint=ce-7");

      const update = await screen.findByRole("button", { name: "更新" });
      await userEvent.click(update);
      await waitFor(() => expect(pushToast).toHaveBeenCalledWith("entry unavailable", "error"));
      expect(update).toBeEnabled();
    });

    it("opens the official contribution guide from the more-actions menu", async () => {
      const open = vi.spyOn(window, "open").mockReturnValue(null);
      renderSection("section=endpoints&endpoint=ce-7");
      await screen.findByDisplayValue("Example Video API");
      await chooseMenu("投稿到市场");
      expect(open).toHaveBeenCalledWith(MARKET_CONTRIBUTING_URL, "_blank", "noopener,noreferrer");
    });

    it("exports an installed endpoint under its market slug with unchanged content", async () => {
      withInstallation({});
      const downloads = captureDownloads();
      renderSection("section=endpoints&endpoint=ce-7");
      await screen.findByDisplayValue("Example Video API");
      await chooseMenu("导出 JSON");
      expect(downloads).toHaveLength(1);
      expect(downloads[0].name).toBe("kling-master.json");
      expect(await downloads[0].blob.text()).toBe(JSON.stringify(makeDefinition(), null, 2));
    });
  });
});
