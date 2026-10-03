import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { createDeferred } from "@/test/deferred";
import type { ApiKeyInfo } from "@/types";

import { AccessTokensSection } from "./AccessTokensSection";

const EXISTING: ApiKeyInfo = {
  id: 1,
  name: "外部智能体",
  key_prefix: "arc-live",
  created_at: "2026-01-01T00:00:00Z",
  expires_at: null,
  last_used_at: null,
};

// 剪贴板是浏览器边界，jsdom 不实现；userEvent.setup() 会装自己的剪贴板，要在它之后再替换。
function stubClipboard() {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  return writeText;
}

describe("AccessTokensSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(API, "listApiKeys").mockResolvedValue([EXISTING]);
  });

  afterEach(() => {
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("创建后只在对话框里展示一次完整令牌，关闭后列表只留前缀", async () => {
    vi.spyOn(API, "createApiKey").mockResolvedValue({
      id: 2,
      name: "CI 流水线",
      key: "arc-new-full-secret",
      key_prefix: "arc-new",
      created_at: "2026-02-01T00:00:00Z",
      expires_at: "2026-03-03T00:00:00Z",
    });
    const user = userEvent.setup();
    const writeText = stubClipboard();
    render(<AccessTokensSection />);
    await screen.findByText("arc-live****");

    await user.click(screen.getByRole("button", { name: "创建访问令牌" }));
    const dialog = await screen.findByRole("dialog", { name: "创建访问令牌" });
    await user.type(within(dialog).getByRole("textbox", { name: "名称" }), "CI 流水线");
    await user.click(within(dialog).getByRole("button", { name: "创建" }));

    await screen.findByRole("dialog", { name: "访问令牌已创建" });
    expect(API.createApiKey).toHaveBeenCalledWith("CI 流水线", 30);
    expect(screen.getByText("arc-new-full-secret")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复制访问令牌" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("arc-new-full-secret"));

    await user.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("arc-new****")).toBeInTheDocument();
    expect(screen.queryByText("arc-new-full-secret")).not.toBeInTheDocument();
  });

  it("创建发生在列表加载期间时保留新令牌和既有令牌", async () => {
    const initial = createDeferred<ApiKeyInfo[]>();
    const added: ApiKeyInfo = { ...EXISTING, id: 2, name: "新接入", key_prefix: "arc-new" };
    vi.mocked(API.listApiKeys).mockReturnValueOnce(initial.promise).mockResolvedValueOnce([added, EXISTING]);
    vi.spyOn(API, "createApiKey").mockResolvedValue({ ...added, key: "arc-new-secret" });
    const user = userEvent.setup();
    render(<AccessTokensSection />);
    await user.click(screen.getByRole("button", { name: "创建访问令牌" }));
    const dialog = await screen.findByRole("dialog", { name: "创建访问令牌" });
    await user.type(within(dialog).getByRole("textbox", { name: "名称" }), "新接入");
    await user.click(within(dialog).getByRole("button", { name: "创建" }));
    await screen.findByRole("dialog", { name: "访问令牌已创建" });
    initial.resolve([EXISTING]);
    await user.click(screen.getByRole("button", { name: "完成" }));
    expect(await screen.findByText("arc-new****")).toBeInTheDocument();
    expect(screen.getByText("arc-live****")).toBeInTheDocument();
  });

  it("创建失败时留在表单并显示错误，不出现令牌", async () => {
    vi.spyOn(API, "createApiKey").mockRejectedValue(new Error("名称已存在"));
    const user = userEvent.setup();
    render(<AccessTokensSection />);
    await screen.findByText("arc-live****");

    await user.click(screen.getByRole("button", { name: "创建访问令牌" }));
    const dialog = await screen.findByRole("dialog", { name: "创建访问令牌" });
    await user.type(within(dialog).getByRole("textbox", { name: "名称" }), "重复");
    await user.click(within(dialog).getByRole("button", { name: "创建" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("名称已存在");
    expect(within(dialog).getByRole("textbox", { name: "名称" })).toHaveValue("重复");
  });

  it("吊销要在确认对话框里确认，取消不吊销", async () => {
    vi.spyOn(API, "deleteApiKey").mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<AccessTokensSection />);
    await screen.findByText("arc-live****");

    await user.click(screen.getByRole("button", { name: "吊销「外部智能体」" }));
    let confirm = await screen.findByRole("alertdialog", { name: "吊销访问令牌「外部智能体」？" });
    await user.click(within(confirm).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(API.deleteApiKey).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "吊销「外部智能体」" }));
    confirm = await screen.findByRole("alertdialog", { name: "吊销访问令牌「外部智能体」？" });
    await user.click(within(confirm).getByRole("button", { name: "吊销" }));

    await waitFor(() => expect(screen.queryByText("arc-live****")).not.toBeInTheDocument());
    expect(API.deleteApiKey).toHaveBeenCalledWith(1);
    expect(screen.getByText("还没有访问令牌")).toBeInTheDocument();
  });

  it("吊销失败时对话框保留并显示错误，令牌仍在列表中", async () => {
    vi.spyOn(API, "deleteApiKey").mockRejectedValue(new Error("网络错误"));
    const user = userEvent.setup();
    render(<AccessTokensSection />);
    await screen.findByText("arc-live****");

    await user.click(screen.getByRole("button", { name: "吊销「外部智能体」" }));
    const confirm = await screen.findByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "吊销" }));

    expect(await within(confirm).findByRole("alert")).toHaveTextContent("网络错误");
    expect(screen.getByText("arc-live****")).toBeInTheDocument();
  });

  it("列表加载失败时给出错误与重试，重试成功后显示列表", async () => {
    vi.spyOn(API, "listApiKeys").mockRejectedValueOnce(new Error("服务不可用")).mockResolvedValueOnce([EXISTING]);
    const user = userEvent.setup();
    render(<AccessTokensSection />);

    expect(await screen.findByRole("alert")).toHaveTextContent("服务不可用");
    expect(screen.queryByText("还没有访问令牌")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("arc-live****")).toBeInTheDocument();
  });
});
