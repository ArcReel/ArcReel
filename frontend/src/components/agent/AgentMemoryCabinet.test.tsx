import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import { API } from "@/api";
import { AgentMemoryCabinet } from "@/components/agent/AgentMemoryCabinet";
import { useAppStore } from "@/stores/app-store";
import type { AgentMemoryOverview } from "@/types/agent-memory";

function overview(patch: Partial<AgentMemoryOverview> = {}): AgentMemoryOverview {
  return {
    path: "/data/.arcreel/users/default/memory",
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

const EMPTY: AgentMemoryOverview = {
  path: "/data/.arcreel/users/default/memory",
  index: { exists: false, line_count: 0, byte_size: 0, over_limit: false },
  files: [],
};

/** 左侧文件列表；等它出现即等过了首屏加载。 */
function findFileList() {
  return screen.findByRole("list");
}

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  vi.restoreAllMocks();
  vi.spyOn(API, "getAgentMemoryFile").mockResolvedValue("- [画幅偏好](aspect-ratio.md)\n");
});

describe("AgentMemoryCabinet", () => {
  it("索引置顶显示行数，主题文件按修改时间倒序并带类型标签与说明", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    const list = await findFileList();
    expect(within(list).getByText("MEMORY.md")).toBeInTheDocument();
    expect(within(list).getByText(/3\/200/)).toBeInTheDocument();
    expect(screen.getByText("创作者的默认画幅偏好")).toBeInTheDocument();
    expect(screen.getByText(/反馈|Feedback/)).toBeInTheDocument();
    expect(screen.getByText("/data/.arcreel/users/default/memory")).toBeInTheDocument();

    const names = within(list)
      .getAllByText(/\.md$/)
      .map((node) => node.textContent);
    expect(names).toEqual(["MEMORY.md", "feedback-no-plot-changes.md", "aspect-ratio.md"]);
  });

  it("索引超限时行数标红", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(
      overview({ index: { exists: true, line_count: 240, byte_size: 30000, over_limit: true } }),
    );

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    const stats = within(await findFileList()).getByText(/240\/200/);
    expect(stats).toHaveClass("text-danger-2");
  });

  it("保存把编辑器原文整段 PUT 回去并弹 toast", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const save = vi.spyOn(API, "saveAgentMemoryFile").mockResolvedValue({ name: "MEMORY.md" });

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    const editor = await screen.findByRole("textbox", { name: "MEMORY.md" });
    fireEvent.change(editor, { target: { value: "- [新条目](new.md)\n" } });
    fireEvent.click(screen.getByRole("button", { name: /保存|Save/ }));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({ level: "user" }, "MEMORY.md", "- [新条目](new.md)\n"),
    );
    await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("success"));
  });

  it("新建走同一个 PUT，正文是带 frontmatter 的模板", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const save = vi.spyOn(API, "saveAgentMemoryFile").mockResolvedValue({ name: "tone.md" });

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    fireEvent.click(await screen.findByRole("button", { name: /新建文件|New file/ }));
    fireEvent.change(screen.getByRole("textbox", { name: /新建文件|New file/ }), {
      target: { value: "tone.md" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^创建$|^Create$/ }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const [scope, filename, content] = save.mock.calls[0];
    expect(scope).toEqual({ level: "user" });
    expect(filename).toBe("tone.md");
    expect(content).toMatch(/^---\nname: tone\n/);
    expect(content).toContain("type: user");
  });

  it("文件名不合规或重名时就地报错，不发请求", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const save = vi.spyOn(API, "saveAgentMemoryFile").mockResolvedValue({ name: "x" });

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    fireEvent.click(await screen.findByRole("button", { name: /新建文件|New file/ }));
    const input = screen.getByRole("textbox", { name: /新建文件|New file/ });
    fireEvent.change(input, { target: { value: "../escape.md" } });
    fireEvent.click(screen.getByRole("button", { name: /^创建$|^Create$/ }));
    expect(await screen.findByText(/以 .md 结尾|end with .md/)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: "aspect-ratio.md" } });
    fireEvent.click(screen.getByRole("button", { name: /^创建$|^Create$/ }));
    expect(await screen.findByText(/已存在同名文件|already exists/)).toBeInTheDocument();

    expect(save).not.toHaveBeenCalled();
  });

  it("删除先二次确认，确认后才删并弹 toast", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const remove = vi.spyOn(API, "deleteAgentMemoryFile").mockResolvedValue({ name: "MEMORY.md" });

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    fireEvent.click(await screen.findByRole("button", { name: /删除|Delete/ }));
    expect(remove).not.toHaveBeenCalled();
    expect(await screen.findByText(/正在进行的会话不受影响|Sessions already running/)).toBeInTheDocument();

    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /删除|Delete/ }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith({ level: "user" }, "MEMORY.md"));
    await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("success"));
  });

  it("清空先二次确认，确认后才清并弹 toast", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const clear = vi.spyOn(API, "clearAgentMemory").mockResolvedValue({ cleared: true });

    render(<AgentMemoryCabinet scope={{ level: "project", projectName: "demo" }} frame="card" />);

    fireEvent.click(await screen.findByRole("button", { name: /清空|Clear/ }));
    expect(clear).not.toHaveBeenCalled();
    expect(await screen.findByText(/正在进行的会话不受影响|Sessions already running/)).toBeInTheDocument();

    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /清空|Clear/ }));
    await waitFor(() => expect(clear).toHaveBeenCalledWith({ level: "project", projectName: "demo" }));
    await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("success"));
  });

  it("空目录渲染空态文案与新建入口，清空入口不可用", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(EMPTY);

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    expect(await screen.findByText(/暂无文件|No files yet/)).toBeInTheDocument();
    expect(screen.getByText(/暂无记忆。|No memory yet/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /新建文件|New file/ })).toHaveLength(2);
    expect(screen.getByRole("button", { name: /清空|Clear/ })).toBeDisabled();
  });

  it("正文读取失败时编辑器给出说明与重试入口，而不是停在加载态", async () => {
    vi.spyOn(API, "getAgentMemory").mockResolvedValue(overview());
    const read = vi
      .spyOn(API, "getAgentMemoryFile")
      .mockRejectedValueOnce(new Error("记忆文件不存在"))
      .mockResolvedValueOnce("- [画幅偏好](aspect-ratio.md)\n");

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    expect(await screen.findByText(/记忆文件不存在/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "MEMORY.md" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重试|Retry/ }));
    expect(await screen.findByRole("textbox", { name: "MEMORY.md" })).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("列表拉取失败时给出说明与重试入口", async () => {
    const list = vi
      .spyOn(API, "getAgentMemory")
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(overview());

    render(<AgentMemoryCabinet scope={{ level: "user" }} frame="section" />);

    expect(await screen.findByText(/加载记忆失败|Could not load memory/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重试|Retry/ }));
    expect(within(await findFileList()).getByText("MEMORY.md")).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });
});
