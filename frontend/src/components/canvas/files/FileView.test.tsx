import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, onTestFinished, vi } from "vitest";

import { useAppStore, type Toast } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { FileViewEntry, ProjectData, RestoredFile, RestoreResult, TrashRecord } from "@/types";

import { FileView } from "./FileView";

afterEach(() => vi.unstubAllGlobals());

function restoreResult(
  object: TrashRecord["object"], restored: RestoredFile[], download_only: string[] = [], degraded = false,
): RestoreResult {
  return { object, restored, degraded, download_only };
}

it("deletes checked files individually, retains a failed selection and restores one record", async () => {
  const entry = (name: string, role: FileViewEntry["role"] = "unreferenced"): FileViewEntry => ({
    name, path: name, type: "file", size: 10, modified_at: "2026-01-01T00:00:00Z", role,
  });
  const files = new Map([
    ...["a.txt", "b.txt", "c.txt"].map((name) => [name, entry(name)] as const),
    ["project.json", entry("project.json", "structure")] as const,
    ["source.txt", { ...entry("source.txt"), owner: { kind: "source" as const } }] as const,
  ]);
  const deleted: string[] = [];
  const restored: string[] = [];
  const toasts: Toast[] = [];
  onTestFinished(useAppStore.subscribe((state, previous) => {
    if (state.toast && state.toast !== previous.toast) toasts.push(state.toast);
  }));
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.searchParams.get("path") ?? "";
    if (init?.method === "DELETE") {
      deleted.push(path);
      if (path === "b.txt") return Response.json({ detail: "file is now referenced" }, { status: 409 });
      files.delete(path);
      return Response.json({ record: { id: `record-${path}` } });
    }
    if (init?.method === "POST") {
      restored.push(url.pathname);
      files.set("c.txt", entry("c.txt"));
      return Response.json(restoreResult({ kind: "file", path: "c.txt" }, [{ original: "c.txt", path: "c.txt", as_history: false }]));
    }
    return Response.json({ path: "", entries: [...files.values()] });
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  for (const name of ["a.txt", "b.txt", "c.txt"]) {
    await user.click(await screen.findByRole("checkbox", { name: `选择 ${name}` }));
  }
  expect(screen.queryByRole("checkbox", { name: "选择 project.json" })).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: "选择 source.txt" })).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "移入项目回收站（3）" }));

  await waitFor(() => expect(screen.getByRole("button", { name: "移入项目回收站（1）" })).toBeEnabled());
  expect(deleted).toEqual(["a.txt", "b.txt", "c.txt"]);
  expect(screen.getByRole("checkbox", { name: "选择 b.txt" })).toBeChecked();
  await waitFor(() => expect(screen.queryByRole("checkbox", { name: "选择 c.txt" })).not.toBeInTheDocument());
  expect(toasts.some((toast) => toast.text.includes("b.txt") && toast.tone === "error")).toBe(true);
  const action = toasts.find((toast) => toast.text.startsWith("c.txt "))?.action;
  expect(action?.label).toBe("恢复");
  act(() => action?.onClick());
  await screen.findByRole("checkbox", { name: "选择 c.txt" });
  expect(restored).toEqual(["/api/v1/projects/demo/trash/record-c.txt/restore"]);
  expect(files.has("a.txt")).toBe(false);
});

it("removes a deleted file from the tree at once instead of waiting for a slow refresh", async () => {
  let listCalls = 0;
  vi.stubGlobal("fetch", (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "DELETE") return Promise.resolve(Response.json({ record: { id: "record-a" } }));
    listCalls++;
    // 首次之后的重拉一直不返回，模拟慢刷新
    if (listCalls > 1) return new Promise<Response>(() => {});
    return Promise.resolve(Response.json({
      path: "",
      entries: [{ name: "a.txt", path: "a.txt", type: "file", size: 1, modified_at: "2026-01-01T00:00:00Z", role: "unreferenced" }],
    }));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("checkbox", { name: "选择 a.txt" }));
  await user.click(screen.getByRole("button", { name: "移入项目回收站（1）" }));

  await waitFor(() => expect(screen.queryByRole("button", { name: /a\.txt/ })).not.toBeInTheDocument());
});

it("ignores an older directory response after refresh has taken over", async () => {
  let finishOld!: (response: Response) => void;
  let oldSignal: AbortSignal | null | undefined;
  let calls = 0;
  vi.stubGlobal("fetch", (_input: RequestInfo | URL, init?: RequestInit) => {
    calls++;
    if (calls === 1) {
      oldSignal = init?.signal;
      return new Promise<Response>((resolve) => { finishOld = resolve; });
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  await userEvent.setup().click(screen.getByRole("button", { name: "刷新" }));
  await screen.findByText("空目录");
  expect(oldSignal?.aborted).toBe(true);

  await act(async () => {
    finishOld(Response.json({
      path: "", entries: [{ name: "old.txt", path: "old.txt", type: "file", role: "unreferenced", size: 1 }],
    }));
  });

  expect(screen.queryByRole("button", { name: /old\.txt/ })).not.toBeInTheDocument();
});

it("refreshes the selected file's details and preview when it changed on disk", async () => {
  let file: FileViewEntry = {
    name: "note.txt", path: "note.txt", type: "file", size: 3, modified_at: "2026-01-01T00:00:00Z", role: "unreferenced",
  };
  let content = "old";
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/file-view/content")) return new Response(content);
    return Response.json({ path: "", entries: [file] });
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /note\.txt/ }));
  expect(await screen.findByText("old")).toBeInTheDocument();

  file = { ...file, size: 7, modified_at: "2026-01-02T00:00:00Z" };
  content = "rewrite";
  await user.click(screen.getByRole("button", { name: "刷新" }));

  expect(await screen.findByText("rewrite")).toBeInTheDocument();
});

it("keeps each in-flight restore protected until its own request finishes", async () => {
  const record = (id: string) => ({
    id, object: { kind: "file", path: `${id}.txt` }, files: [`${id}.txt`], schema_version: 1,
    deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 1, has_snapshot: false, download_only: false,
  });
  const pending = new Map<string, () => void>();
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      const id = url.pathname.split("/").at(-2) ?? "";
      return new Promise<Response>((resolve) => {
        pending.set(id, () => resolve(Response.json(restoreResult({ kind: "file", path: `${id}.txt` }, [{ original: `${id}.txt`, path: `${id}.txt`, as_history: false }]))));
      });
    }
    if (url.pathname.endsWith("/trash")) {
      return Promise.resolve(Response.json({ records: [record("a"), record("b")], total_size: 2, retention_days: 30 }));
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  const [restoreA, restoreB] = await screen.findAllByRole("button", { name: "恢复" });
  await user.click(restoreA);
  await user.click(restoreB);
  await waitFor(() => expect(pending.size).toBe(2));
  // 恢复在途时，清空会连带删掉正在恢复的记录：一并禁用
  expect(screen.getByRole("button", { name: "清空项目回收站" })).toBeDisabled();

  await act(async () => pending.get("b")?.());

  await waitFor(() => expect(screen.getAllByRole("button", { name: "恢复" })[1]).toBeEnabled());
  expect(screen.getAllByRole("button", { name: "恢复" })[0]).toBeDisabled();
  expect(screen.getByRole("button", { name: "清空项目回收站" })).toBeDisabled();

  await act(async () => pending.get("a")?.());
  await waitFor(() => expect(screen.getByRole("button", { name: "清空项目回收站" })).toBeEnabled());
});

it("removes a restored record at once instead of waiting for a slow listing refresh", async () => {
  const record = {
    id: "a", object: { kind: "file", path: "a.txt" }, files: ["a.txt"], schema_version: 1,
    deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 2048, has_snapshot: false, download_only: false,
  };
  let trashCalls = 0;
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      return Promise.resolve(Response.json(restoreResult({ kind: "file", path: "a.txt" }, [{ original: "a.txt", path: "a.txt", as_history: false }])));
    }
    if (!url.pathname.endsWith("/trash")) return Promise.resolve(Response.json({ path: "", entries: [] }));
    trashCalls++;
    // 首次之后的重拉一直不返回，模拟慢刷新
    if (trashCalls > 1) return new Promise<Response>(() => {});
    return Promise.resolve(Response.json({ records: [record], total_size: 2048, retention_days: 30 }));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  expect(await screen.findByText(/占用 2\.0 KB/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "恢复" }));

  await waitFor(() => expect(screen.queryByRole("button", { name: "恢复" })).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "彻底删除" })).not.toBeInTheDocument();
  expect(screen.getByText(/共 0 条删除记录，占用 0 B/)).toBeInTheDocument();
});

it("drops a retried trash listing once a newer load has started", async () => {
  const listing = (path: string) => ({
    records: [{
      id: path, object: { kind: "file", path }, files: [path], schema_version: 1,
      deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 1, has_snapshot: false, download_only: false,
    }],
    total_size: 1,
    retention_days: 30,
  });
  let trashCalls = 0;
  let finishRetry!: (response: Response) => void;
  vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (!url.pathname.endsWith("/trash")) return Promise.resolve(Response.json({ path: "", entries: [] }));
    trashCalls++;
    if (trashCalls === 1) return Promise.resolve(Response.json({ detail: "boom" }, { status: 500 }));
    if (trashCalls === 2) return new Promise<Response>((resolve) => { finishRetry = resolve; });
    return Promise.resolve(Response.json(listing("new.txt")));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  await user.click(await screen.findByRole("button", { name: "重试" }));
  await user.click(screen.getByRole("button", { name: "刷新" }));
  await screen.findByText("new.txt");

  await act(async () => finishRetry(Response.json(listing("old.txt"))));

  expect(screen.queryByText("old.txt")).not.toBeInTheDocument();
  expect(screen.getByText("new.txt")).toBeInTheDocument();
});

it("keeps in-flight writes protected when switching between sections", async () => {
  const record = {
    id: "r", object: { kind: "file", path: "r.txt" }, files: ["r.txt"], schema_version: 1,
    deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 1, has_snapshot: false, download_only: false,
  };
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    // 删除与恢复一直不返回，模拟在途的写操作
    if (init?.method === "DELETE" || init?.method === "POST") return new Promise<Response>(() => {});
    if (url.pathname.endsWith("/trash")) {
      return Promise.resolve(Response.json({ records: [record], total_size: 1, retention_days: 30 }));
    }
    return Promise.resolve(Response.json({
      path: "",
      entries: [{ name: "a.txt", path: "a.txt", type: "file", size: 1, modified_at: "2026-01-01T00:00:00Z", role: "unreferenced" }],
    }));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("checkbox", { name: "选择 a.txt" }));
  await user.click(screen.getByRole("button", { name: "移入项目回收站（1）" }));
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  await user.click(await screen.findByRole("button", { name: "恢复" }));

  await user.click(screen.getByRole("tab", { name: "文件" }));
  expect(screen.getByRole("button", { name: "移入项目回收站（1）" })).toBeDisabled();
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  expect(screen.getByRole("tabpanel", { name: "项目回收站" })).toContainElement(screen.getByRole("button", { name: "恢复" }));
  expect(screen.getByRole("button", { name: "恢复" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "彻底删除" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "清空项目回收站" })).toBeDisabled();
});

it("labels a generation result whose target was deleted instead of naming who deleted it", async () => {
  const record = {
    id: "g",
    object: { kind: "generation_result", path: "characters/阿岚.png", note: "target_deleted_during_generation" },
    files: ["characters/阿岚.png"], schema_version: 1,
    deleted_at: "2026-01-01T00:00:00Z", origin: "agent", size: 1, has_snapshot: false, download_only: false,
  };
  vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (!url.pathname.endsWith("/trash")) return Promise.resolve(Response.json({ path: "", entries: [] }));
    return Promise.resolve(Response.json({ records: [record], total_size: 1, retention_days: 30 }));
  });
  render(<FileView projectName="demo" />);
  await userEvent.setup().click(screen.getByRole("tab", { name: "项目回收站" }));

  expect(await screen.findByText(/生成完成，目标已删除/)).toBeInTheDocument();
  expect(screen.queryByText(/由 Agent 删除/)).not.toBeInTheDocument();
});

const TIMELINE_DOCUMENT = "edit_timelines/episode_1/tl-1.json";

function timelineRecord(overrides: Partial<TrashRecord> = {}): TrashRecord {
  return {
    id: "tl", object: { kind: "edit_timeline", id: "tl-1", episode: 1, name: "初剪" },
    files: [TIMELINE_DOCUMENT, "renders/episode_1/tl-1/final.mp4"], schema_version: 17,
    deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 10, has_snapshot: true, download_only: false,
    ...overrides,
  };
}

function collectToasts(): Toast[] {
  const toasts: Toast[] = [];
  onTestFinished(useAppStore.subscribe((state, previous) => {
    if (state.toast && state.toast !== previous.toast) toasts.push(state.toast);
  }));
  return toasts;
}

it("names a timeline record by its object and reports the name it was restored under", async () => {
  const toasts = collectToasts();
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      return Promise.resolve(Response.json(restoreResult(
        { kind: "edit_timeline", id: "tl-1", episode: 1, name: "初剪 (1)" },
        [{ original: TIMELINE_DOCUMENT, path: TIMELINE_DOCUMENT, as_history: false }],
      )));
    }
    if (url.pathname.endsWith("/trash")) {
      return Promise.resolve(Response.json({ records: [timelineRecord()], total_size: 10, retention_days: 30 }));
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  expect(await screen.findByText("剪辑时间线「初剪」（第 1 集）")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "恢复" }));

  await waitFor(() => expect(toasts.at(-1)?.text).toBe("原名已被占用，剪辑时间线「初剪」（第 1 集） 已改名为「初剪 (1)」恢复"));
});

it("names source file and planning draft records by their object", async () => {
  const record = (id: string, object: TrashRecord["object"]) => timelineRecord({ id, object, files: [String(object.path)] });
  vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/trash")) {
      return Promise.resolve(Response.json({
        records: [
          record("s", { kind: "source_file", path: "source/第一章.txt", raw: [] }),
          record("p", { kind: "planning_draft", path: "drafts/episode_2/plan.json", episode: 2 }),
        ],
        total_size: 20,
        retention_days: 30,
      }));
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  await userEvent.setup().click(screen.getByRole("tab", { name: "项目回收站" }));
  expect(await screen.findByText("源文件「第一章.txt」")).toBeInTheDocument();
  expect(screen.getByText("第 2 集的规划草稿")).toBeInTheDocument();
});

it("names an episode record by its title and says the restored episode goes to the end of the order", async () => {
  const toasts = collectToasts();
  const episode = { kind: "episode", id: 3, title: "番外" };
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      return Promise.resolve(Response.json(restoreResult(episode, [
        { original: "scripts/episode_3.json", path: "scripts/episode_3.json", as_history: false },
      ])));
    }
    if (url.pathname.endsWith("/trash")) {
      const records = [
        timelineRecord({ id: "ep", object: episode, files: ["scripts/episode_3.json"] }),
        timelineRecord({ id: "ep2", object: { kind: "episode", id: 4, title: "" }, files: ["scripts/episode_4.json"] }),
      ];
      return Promise.resolve(Response.json({ records, total_size: 20, retention_days: 30 }));
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  expect(await screen.findByText("集「番外」")).toBeInTheDocument();
  expect(screen.getByText("第 4 集")).toBeInTheDocument();

  await user.click(screen.getAllByRole("button", { name: "恢复" })[0]);

  await waitFor(() => expect(toasts.at(-1)?.text).toBe("集「番外」 已恢复，排在播出顺序末尾"));
});

it("names asset and derivative records by their objects", async () => {
  vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/trash")) {
      const base = { ...timelineRecord(), has_snapshot: true, files: [] };
      return Promise.resolve(Response.json({
        records: [
          { ...base, id: "a", object: { kind: "asset", asset_type: "scene", name: "茶楼" } },
          { ...base, id: "b", object: { kind: "asset_derivative", asset_type: "character", owner: "阿岚", name: "战甲" } },
        ],
        total_size: 10,
        retention_days: 30,
      }));
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  await userEvent.setup().click(screen.getByRole("tab", { name: "项目回收站" }));

  expect(await screen.findByText("场景「茶楼」")).toBeInTheDocument();
  expect(screen.getByText("角色「阿岚」的衍生「战甲」")).toBeInTheDocument();
});

it("tells what a degraded restore recovered and offers the remaining structure files for download", async () => {
  const toasts = collectToasts();
  const downloads: string[] = [];
  let restoredOnce = false;
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => {} }));
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      restoredOnce = true;
      return Promise.resolve(Response.json(restoreResult(
        timelineRecord().object,
        [{ original: "renders/episode_1/tl-1/final.mp4", path: "renders/episode_1/tl-1/final.mp4", as_history: false }],
        [TIMELINE_DOCUMENT],
        true,
      )));
    }
    if (url.pathname.endsWith("/files")) {
      downloads.push(url.searchParams.get("path") ?? "");
      return Promise.resolve(new Response("{}"));
    }
    if (url.pathname.endsWith("/trash")) {
      const record = restoredOnce
        ? timelineRecord({ files: [TIMELINE_DOCUMENT], has_snapshot: false, download_only: true })
        : timelineRecord();
      return Promise.resolve(Response.json({ records: [record], total_size: 10, retention_days: 30 }));
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  await user.click(await screen.findByRole("button", { name: "恢复" }));

  const download = await screen.findByRole("button", { name: `下载 ${TIMELINE_DOCUMENT}` });
  expect(toasts.at(-1)).toMatchObject({
    tone: "warning",
    text: "剪辑时间线「初剪」（第 1 集） 无法按原结构恢复，已找回其中 1 个文件；结构文件留在项目回收站里，只能下载",
  });
  expect(screen.getByText("无法按原结构恢复，以下结构文件只能下载")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "恢复" })).not.toBeInTheDocument();
  await user.click(download);
  await waitFor(() => expect(downloads).toEqual([TIMELINE_DOCUMENT]));
});

it("names shot, unit and end frame records by their position and text when deleted", async () => {
  const project: ProjectData = {
    title: "演示", content_mode: "narration", style: "Anime", characters: {},
    episodes: [{ episode: 1, title: "雨夜", script_file: "scripts/episode_1.json" }],
  };
  useProjectsStore.getState().setCurrentProject("demo", project, {}, {});
  onTestFinished(() => useProjectsStore.getState().setCurrentProject(null, null));
  const entry = { kind: "script_entry", episode: 1, item_kind: "segments" };
  vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/trash")) {
      const records = [
        timelineRecord({ id: "a", object: { ...entry, id: "E1S07", item_position: 5, item_excerpt: "他推开门…" } }),
        timelineRecord({ id: "b", object: { ...entry, id: "E1S09", item_position: 6, item_excerpt: "" } }),
        timelineRecord({
          id: "c",
          object: { ...entry, id: "E1U3", item_kind: "video_units", item_position: 2, item_excerpt: "@林 回头" },
        }),
        timelineRecord({
          id: "d",
          object: {
            kind: "end_frame", shot_id: "E1S07", script_file: "episode_1.json", path: "end_frames/scene_E1S07.png",
            episode: 1, item_position: 5, item_excerpt: "他推开门…",
          },
        }),
      ];
      return Promise.resolve(Response.json({ records, total_size: 40, retention_days: 30 }));
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  await userEvent.setup().click(screen.getByRole("tab", { name: "项目回收站" }));

  // 同一集删掉的多条记录凭删除时的位置与正文区分，不露内部编号。
  expect(await screen.findByText("分镜 雨夜 · 5：他推开门…")).toBeInTheDocument();
  expect(screen.getByText("分镜 雨夜 · 6")).toBeInTheDocument();
  expect(screen.getByText("视频单元 雨夜 · 2：@林 回头")).toBeInTheDocument();
  expect(screen.getByText("分镜 雨夜 · 5 的尾帧：他推开门…")).toBeInTheDocument();
  expect(screen.queryByText(/E1S07|E1S09|E1U3/)).not.toBeInTheDocument();
});

it("uses the current position after restoring a shot under a new ID", async () => {
  const toasts = collectToasts();
  const shot = { kind: "script_entry", id: "E1S03", episode: 1, item_kind: "segments" };
  const project: ProjectData = {
    title: "演示", content_mode: "narration", style: "Anime", characters: {},
    episodes: [{ episode: 1, title: "雨夜", script_file: "scripts/episode_1.json" }],
  };
  useProjectsStore.getState().setCurrentProject("demo", project, {}, {});
  onTestFinished(() => useProjectsStore.getState().setCurrentProject(null, null));
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      return Promise.resolve(Response.json(restoreResult({ ...shot, id: "E1S04" }, [
        { original: "storyboards/scene_E1S03.png", path: "storyboards/scene_E1S04.png", as_history: false },
      ])));
    }
    if (url.pathname.endsWith("/trash")) {
      const records = [
        timelineRecord({ id: "shot", object: shot, files: ["storyboards/scene_E1S03.png"] }),
        timelineRecord({
          id: "unit",
          object: { kind: "script_entry", id: "E1U2", episode: 1, item_kind: "video_units" },
          files: ["reference_videos/E1U2.mp4"],
        }),
      ];
      return Promise.resolve(Response.json({ records, total_size: 20, retention_days: 30 }));
    }
    if (url.pathname.endsWith("/projects/demo")) {
      return Promise.resolve(Response.json({
        project,
        scripts: { "episode_1.json": {
          episode: 1,
          segments: [{ segment_id: "E1S01" }, { segment_id: "E1S04" }, { segment_id: "E1S02" }],
        } },
        asset_fingerprints: {},
      }));
    }
    return Promise.resolve(Response.json({ path: "", entries: [] }));
  });
  render(<FileView projectName="demo" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "项目回收站" }));
  // 没有位置快照的旧记录退回编号显示。
  expect(await screen.findByText("分镜 E1S03（第 1 集）")).toBeInTheDocument();
  expect(screen.getByText("视频单元 E1U2（第 1 集）")).toBeInTheDocument();

  await user.click(screen.getAllByRole("button", { name: "恢复" })[0]);

  await waitFor(() => expect(toasts.at(-1)?.text).toBe("雨夜 · 2 已恢复"));
});
