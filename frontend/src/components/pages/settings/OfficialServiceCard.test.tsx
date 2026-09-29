import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { OfficialServiceState } from "@/types";
import { OfficialServiceCard } from "./OfficialServiceCard";

const INSTANCE_ID = "5b8f0c1e-2d3a-4f6b-9c7d-0e1f2a3b4c5d";
const ON: OfficialServiceState = { available: true, enabled: true, notice_seen: true, instance_id: INSTANCE_ID };

describe("OfficialServiceCard", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("turns the official service off and on immediately", async () => {
    vi.spyOn(API, "getOfficialService").mockResolvedValue(ON);
    const update = vi
      .spyOn(API, "updateOfficialService")
      .mockResolvedValueOnce({ ...ON, enabled: false })
      .mockResolvedValueOnce(ON);
    render(<OfficialServiceCard />);

    const toggle = await screen.findByRole("switch", { name: "使用官方服务" });
    expect(toggle).toBeChecked();
    expect(screen.getByText(INSTANCE_ID)).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(update).toHaveBeenLastCalledWith({ enabled: false });
    await waitFor(() => expect(toggle).not.toBeChecked());
    await userEvent.click(toggle);
    expect(update).toHaveBeenLastCalledWith({ enabled: true });
    await waitFor(() => expect(toggle).toBeChecked());
  });

  it("resets the instance ID after confirmation", async () => {
    vi.spyOn(API, "getOfficialService").mockResolvedValue(ON);
    const reset = vi.spyOn(API, "resetOfficialInstanceId").mockResolvedValue({ ...ON, instance_id: null });
    render(<OfficialServiceCard />);

    await userEvent.click(await screen.findByRole("button", { name: "重置实例标识" }));
    const dialog = await screen.findByRole("dialog", { name: "重置实例标识" });
    expect(reset).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "重置" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(reset).toHaveBeenCalledTimes(1);
    expect(screen.getByText("尚未生成")).toBeInTheDocument();
    expect(useAppStore.getState().toast?.text).toBe("实例标识已重置");
  });

  it("only explains the off state when the build has no official service address", async () => {
    vi.spyOn(API, "getOfficialService").mockResolvedValue({ ...ON, available: false, enabled: false, instance_id: null });
    render(<OfficialServiceCard />);

    expect(await screen.findByText("当前版本未配置官方服务地址，官方服务处于关闭状态。")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重置实例标识" })).not.toBeInTheDocument();
  });
});
