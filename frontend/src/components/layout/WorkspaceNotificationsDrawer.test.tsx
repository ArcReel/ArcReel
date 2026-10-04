import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "@/stores/app-store";
import { WorkspaceNotifications } from "./WorkspaceNotificationsDrawer";

function bell() {
  return screen.getByRole("button", { name: /工作区通知/ });
}

describe("WorkspaceNotifications", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  it("clears the badge on open and keeps unread items highlighted until the panel closes", async () => {
    const user = userEvent.setup();
    useAppStore.getState().pushWorkspaceNotification({ text: "道具「玉佩」已更新" });
    render(<WorkspaceNotifications onNavigate={vi.fn()} />);
    expect(bell()).toHaveAccessibleName("工作区通知，未读 1 条");

    await user.click(bell());
    const panel = await screen.findByRole("dialog", { name: "工作区通知" });
    expect(bell()).toHaveAccessibleName("工作区通知");
    expect(within(panel).getByText(/新通知/)).toBeInTheDocument();

    // 面板开着时新到的通知同样不再计入角标，也一并高亮
    act(() => useAppStore.getState().pushWorkspaceNotification({ text: "第 2 集分镜已生成" }));
    expect(bell()).toHaveAccessibleName("工作区通知");
    expect(within(panel).getAllByText(/新通知/)).toHaveLength(2);

    await user.keyboard("{Escape}");
    await user.click(bell());
    const reopened = await screen.findByRole("dialog", { name: "工作区通知" });
    expect(within(reopened).getByText("道具「玉佩」已更新")).toBeInTheDocument();
    expect(within(reopened).queryByText(/新通知/)).not.toBeInTheDocument();
  });

  it("closes the panel and hands the notification over when locating it", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    useAppStore.getState().pushWorkspaceNotification({
      text: "道具「玉佩」已更新",
      target: { type: "prop", id: "玉佩", route: "/props" },
    });
    render(<WorkspaceNotifications onNavigate={onNavigate} />);

    await user.click(bell());
    await user.click(await screen.findByRole("button", { name: "查看" }));

    expect(onNavigate).toHaveBeenCalledWith(expect.objectContaining({ text: "道具「玉佩」已更新" }));
    expect(screen.queryByRole("dialog", { name: "工作区通知" })).not.toBeInTheDocument();
  });
});
