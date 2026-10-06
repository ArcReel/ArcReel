import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import "@/i18n";
import { API } from "@/api";
import { AgentMemorySection } from "@/components/agent-memory/AgentMemorySection";
import { LeaveGuardProvider } from "@/components/shared/edit-unit/LeaveGuard";
import { createDeferred } from "@/test/deferred";
import { useAppStore } from "@/stores/app-store";
import type { AgentMemoryOverview } from "@/types/agent-memory";

const USER = { level: "user" } as const;

function overview(patch: Partial<AgentMemoryOverview> = {}): AgentMemoryOverview {
  return {
    path: "/data/users/default/memory",
    index: { exists: true, line_count: 3, byte_size: 120, over_limit: false },
    files: [
      {
        name: "aspect-ratio.md",
        size: 200,
        modified_at: "2026-08-25T02:00:00+00:00",
        frontmatter: { name: "aspect-ratio", description: "创作者的默认画幅偏好", type: "user" },
      },
      {
        name: "feedback-no-plot-changes.md",
        size: 320,
        modified_at: "2026-09-01T02:00:00+00:00",
        frontmatter: { name: "feedback", description: "改稿时不要改动原文情节", type: "feedback" },
      },
    ],
    ...patch,
  };
}

const CONTENT: Record<string, string> = {
  "MEMORY.md": "- [画幅偏好](aspect-ratio.md)\n",
  "aspect-ratio.md": "---\nname: aspect-ratio\n---\n\n默认竖屏\n",
  "feedback-no-plot-changes.md": "---\nname: feedback\n---\n\n不改情节\n",
};

function renderSection(search = "section=agent-memory") {
  const location = memoryLocation({ path: "/app/settings", searchPath: search, record: true });
  render(
    <Router hook={location.hook}>
      <LeaveGuardProvider>
        <AgentMemorySection />
      </LeaveGuardProvider>
    </Router>,
  );
  return location;
}

/** 二级栏标准档的文件列表（jsdom 不跑容器查询，紧凑档的同名列表也在 DOM 里）。 */
async function fileList() {
  const [standard] = await screen.findAllByRole("list", { name: "记忆文件" });
  return standard;
}

const lastSearch = (location: ReturnType<typeof memoryLocation>) =>
  new URLSearchParams(location.history.at(-1)?.split("?")[1] ?? "");

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  vi.restoreAllMocks();
  vi.spyOn(API, "getAgentMemoryFile").mockImplementation(async (_scope, name) => CONTENT[name] ?? "");
});

describe("AgentMemorySection", () => {
  it("索引置顶并显示行数，主题文件按修改时间倒序并带说明；默认打开第一项", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    renderSection();

    const links = within(await fileList()).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "MEMORY.md索引 3/200 行",
      "feedback-no-plot-changes.md改稿时不要改动原文情节",
      "aspect-ratio.md创作者的默认画幅偏好",
      "新建记忆文件",
    ]);
    expect(links[0]).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("textbox", { name: "MEMORY.md" })).toHaveValue(CONTENT["MEMORY.md"]);
    expect(screen.getByText("/data/users/default/memory/MEMORY.md")).toBeInTheDocument();
    expect(API.getAgentMemory).toHaveBeenCalledWith(USER, expect.anything());
  });

  it("索引超出行数上限时用文字标明", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(
      overview({ index: { exists: true, line_count: 240, byte_size: 30000, over_limit: true } }),
    );
    renderSection();

    expect(within(await fileList()).getByText("索引 240/200 行，超出上限")).toBeInTheDocument();
  });

  it("主题文件的页头显示类型与修改时间", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    renderSection("section=agent-memory&file=feedback-no-plot-changes.md");

    await screen.findByRole("textbox", { name: "feedback-no-plot-changes.md" });
    expect(screen.getByRole("heading", { level: 2, name: "feedback-no-plot-changes.md" })).toBeInTheDocument();
    expect(screen.getByText("反馈")).toBeInTheDocument();
    expect(screen.getByText(/^修改于 /)).toBeInTheDocument();
  });

  it("修改后出现内联提示条，保存整段 PUT 回去并重取列表，不弹提示", async () => {
    const getMemory = vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const save = vi.spyOn(API, "saveAgentMemoryFile").mockResolvedValue({ name: "MEMORY.md" });
    renderSection();

    const editor = await screen.findByRole("textbox", { name: "MEMORY.md" });
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    fireEvent.change(editor, { target: { value: "- [新条目](new.md)\n" } });
    expect(screen.getByText("有未保存的修改")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith(USER, "MEMORY.md", "- [新条目](new.md)\n"));
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(getMemory).toHaveBeenCalledTimes(2);
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("保存进行中禁用删除与更多操作，失败后恢复操作", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const pending = createDeferred<{ name: string }>();
    vi.spyOn(API, "saveAgentMemoryFile").mockReturnValue(pending.promise);
    const remove = vi.spyOn(API, "deleteAgentMemoryFile").mockResolvedValue({ name: "MEMORY.md" });
    const clear = vi.spyOn(API, "clearAgentMemory").mockResolvedValue({ cleared: true });
    renderSection();

    fireEvent.change(await screen.findByRole("textbox", { name: "MEMORY.md" }), { target: { value: "新的记忆" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    const deleteButton = screen.getByRole("button", { name: "删除" });
    const moreButton = screen.getByRole("button", { name: "更多操作" });
    expect(deleteButton).toBeDisabled();
    expect(moreButton).toBeDisabled();
    fireEvent.click(deleteButton);
    fireEvent.click(moreButton);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();

    pending.reject(new Error("磁盘已满"));
    expect(await screen.findByRole("alert")).toHaveTextContent("磁盘已满");
    expect(deleteButton).toBeEnabled();
    expect(moreButton).toBeEnabled();
  });

  it("保存失败时提示条显示原因，修改保留", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    vi.spyOn(API, "saveAgentMemoryFile").mockRejectedValue(new Error("磁盘已满"));
    renderSection();

    const editor = await screen.findByRole("textbox", { name: "MEMORY.md" });
    fireEvent.change(editor, { target: { value: "改过的内容" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("磁盘已满");
    expect(editor).toHaveValue("改过的内容");
  });

  it("有未保存修改时切换文件先询问；放弃修改后打开另一个文件", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const location = renderSection();

    fireEvent.change(await screen.findByRole("textbox", { name: "MEMORY.md" }), { target: { value: "改过的内容" } });
    fireEvent.click(within(await fileList()).getByRole("link", { name: /^aspect-ratio\.md/ }));

    const dialog = await screen.findByRole("alertdialog", { name: "「MEMORY.md」有未保存的修改" });
    expect(lastSearch(location).get("file")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "放弃修改" }));

    expect(await screen.findByRole("textbox", { name: "aspect-ratio.md" })).toHaveValue(CONTENT["aspect-ratio.md"]);
    expect(lastSearch(location).get("file")).toBe("aspect-ratio.md");
  });

  it("文件读取失败时就地显示原因，可以重试", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const read = vi.spyOn(API, "getAgentMemoryFile").mockRejectedValueOnce(new Error("文件不存在"));
    renderSection();

    expect(await screen.findByRole("alert")).toHaveTextContent("文件不存在");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("textbox", { name: "MEMORY.md" })).toHaveValue(CONTENT["MEMORY.md"]);
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("新建在途时切到别的文件：创建完成后刷新列表，但不把人拉回新文件", async () => {
    const getMemory = vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const pending = createDeferred<{ name: string }>();
    vi.spyOn(API, "saveAgentMemoryFile").mockReturnValue(pending.promise);
    const location = renderSection();

    fireEvent.click(within(await fileList()).getByRole("link", { name: "新建记忆文件" }));
    fireEvent.change(await screen.findByRole("textbox", { name: "文件名" }), { target: { value: "tone.md" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    fireEvent.click(within(await fileList()).getByRole("link", { name: /^aspect-ratio\.md/ }));
    await waitFor(() => expect(lastSearch(location).get("file")).toBe("aspect-ratio.md"));
    const loads = getMemory.mock.calls.length;

    pending.resolve({ name: "tone.md" });
    await waitFor(() => expect(getMemory.mock.calls.length).toBeGreaterThan(loads));
    expect(lastSearch(location).get("file")).toBe("aspect-ratio.md");
  });

  it("新建：文件名不合规或重名时就地报错、不发请求；创建后选中新文件", async () => {
    const getMemory = vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const save = vi.spyOn(API, "saveAgentMemoryFile").mockResolvedValue({ name: "tone.md" });
    const location = renderSection();

    fireEvent.click(within(await fileList()).getByRole("link", { name: "新建记忆文件" }));
    const input = await screen.findByRole("textbox", { name: "文件名" });
    const create = screen.getByRole("button", { name: "创建" });

    fireEvent.change(input, { target: { value: "../escape.md" } });
    fireEvent.click(create);
    expect(await screen.findByRole("alert")).toHaveTextContent(/以 \.md 结尾/);
    fireEvent.change(input, { target: { value: "aspect-ratio.md" } });
    fireEvent.click(create);
    expect(await screen.findByRole("alert")).toHaveTextContent("已存在同名文件");
    // 默认不区分大小写的文件系统上，只差大小写的文件名指向同一个文件
    fireEvent.change(input, { target: { value: "memory.md" } });
    fireEvent.click(create);
    expect(await screen.findByRole("alert")).toHaveTextContent("已存在同名文件");
    expect(save).not.toHaveBeenCalled();

    const withTone = overview();
    withTone.files.push({
      name: "tone.md",
      size: 80,
      modified_at: "2026-09-02T02:00:00+00:00",
      frontmatter: { name: "tone", description: "这条记忆的一句话说明", type: "user" },
    });
    getMemory.mockResolvedValue(withTone);
    fireEvent.change(input, { target: { value: "tone.md" } });
    fireEvent.click(create);

    await waitFor(() => expect(lastSearch(location).get("file")).toBe("tone.md"));
    const [scope, filename, content] = save.mock.calls[0];
    expect(scope).toEqual(USER);
    expect(filename).toBe("tone.md");
    expect(content).toMatch(/^---\nname: tone\n/);
    expect(content).toContain("type: user");
    expect(await screen.findByRole("heading", { level: 2, name: "tone.md" })).toBeInTheDocument();
  });

  it("删除先确认，确认后删除并回到列表第一项", async () => {
    const getMemory = vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const remove = vi.spyOn(API, "deleteAgentMemoryFile").mockResolvedValue({ name: "aspect-ratio.md" });
    renderSection("section=agent-memory&file=aspect-ratio.md");

    await screen.findByRole("textbox", { name: "aspect-ratio.md" });
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("alertdialog", { name: "删除 aspect-ratio.md？" });
    expect(within(dialog).getByText(/正在进行的会话不受影响/)).toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();

    getMemory.mockResolvedValue(overview({ files: overview().files.slice(1) }));
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith(USER, "aspect-ratio.md"));
    expect(await screen.findByRole("textbox", { name: "MEMORY.md" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("删除失败时对话框保持打开并显示原因", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    vi.spyOn(API, "deleteAgentMemoryFile").mockRejectedValue(new Error("权限不足"));
    renderSection();

    await screen.findByRole("textbox", { name: "MEMORY.md" });
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = await screen.findByRole("alertdialog", { name: "删除 MEMORY.md？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("权限不足");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("从更多操作清空全部记忆，确认后清空并显示新建表单", async () => {
    const getMemory = vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const clear = vi.spyOn(API, "clearAgentMemory").mockResolvedValue({ cleared: true });
    renderSection();

    await screen.findByRole("textbox", { name: "MEMORY.md" });
    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "清空全部记忆" }));
    const dialog = await screen.findByRole("alertdialog", { name: "清空用户记忆？" });
    expect(within(dialog).getByText(/全部 3 个记忆文件/)).toBeInTheDocument();

    getMemory.mockResolvedValue(
      overview({ index: { exists: false, line_count: 0, byte_size: 0, over_limit: false }, files: [] }),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "清空" }));

    await waitFor(() => expect(clear).toHaveBeenCalledWith(USER));
    expect(await screen.findByRole("heading", { level: 2, name: "新建记忆文件" })).toBeInTheDocument();
    expect(screen.getByText(/Agent 会在创作过程中自动记录/)).toBeInTheDocument();
  });

  it("列表加载失败时显示原因，可以重试", async () => {
    vi.spyOn(API, "getAgentMemory").mockRejectedValueOnce(new Error("网络错误")).mockResolvedValue(overview());
    renderSection();

    expect(await screen.findByRole("alert")).toHaveTextContent("网络错误");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("textbox", { name: "MEMORY.md" })).toBeInTheDocument();
  });

  it("记忆原文用等宽字体编辑", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    renderSection();

    expect(await screen.findByRole("textbox", { name: "MEMORY.md" })).toHaveClass("font-mono");
  });
});
