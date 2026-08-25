import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { ExternalAgentModal } from "@/components/pages/ExternalAgentModal";
import { copyText } from "@/utils/clipboard";

vi.mock("@/utils/clipboard", () => ({
  copyText: vi.fn().mockResolvedValue(undefined),
}));

describe("ExternalAgentModal", () => {
  beforeEach(() => {
    vi.mocked(copyText).mockResolvedValue(undefined);
  });

  it("shows and copies the MCP endpoint and skill installation command", async () => {
    const user = userEvent.setup();
    render(<ExternalAgentModal onClose={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "外部智能体接入" })).toBeInTheDocument();
    expect(screen.getByText(`${window.location.origin}/mcp`)).toBeInTheDocument();
    expect(screen.getByText("npx skills add ArcReel/ArcReel@setup-arcreel-skills")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看完整安装指引" })).toHaveAttribute(
      "href",
      `${window.location.origin}/agent-installation-guide.md`,
    );

    await user.click(screen.getByRole("button", { name: "复制 MCP 端点" }));
    expect(copyText).toHaveBeenLastCalledWith(`${window.location.origin}/mcp`);
    expect(screen.getByRole("status")).toHaveTextContent("MCP 端点已复制");

    await user.click(screen.getByRole("button", { name: "复制安装命令" }));
    expect(copyText).toHaveBeenLastCalledWith("npx skills add ArcReel/ArcReel@setup-arcreel-skills");
    expect(screen.getByRole("status")).toHaveTextContent("安装命令已复制");
  });

  it("reports clipboard failures with a recovery action", async () => {
    vi.mocked(copyText).mockRejectedValueOnce(new Error("clipboard unavailable"));
    const user = userEvent.setup();
    render(<ExternalAgentModal onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "复制 MCP 端点" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("复制失败，请手动选择并复制上方内容。");
  });

  it("opens API Key management through the existing creation and copy flow", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const location = memoryLocation({ path: "/app/projects", record: true });
    render(
      <Router hook={location.hook}>
        <ExternalAgentModal onClose={onClose} />
      </Router>,
    );

    await user.click(screen.getByRole("button", { name: "生成或复制 API Key" }));

    expect(onClose).toHaveBeenCalledOnce();
    expect(location.history?.at(-1)).toBe("/app/settings?section=api-keys");
  });
});
