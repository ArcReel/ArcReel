import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, onTestFinished, vi } from "vitest";

import { useAppStore, type Toast } from "@/stores/app-store";
import type { FileViewEntry } from "@/types";

import { FileView } from "./FileView";

afterEach(() => vi.unstubAllGlobals());

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
      return Response.json({ restored: [{ original: "c.txt", path: "c.txt", as_history: false }] });
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
    deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 1, has_snapshot: false,
  });
  const pending = new Map<string, () => void>();
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      const id = url.pathname.split("/").at(-2) ?? "";
      return new Promise<Response>((resolve) => {
        pending.set(id, () => resolve(Response.json({ restored: [{ original: `${id}.txt`, path: `${id}.txt`, as_history: false }] })));
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
    deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 2048, has_snapshot: false,
  };
  let trashCalls = 0;
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (init?.method === "POST") {
      return Promise.resolve(Response.json({ restored: [{ original: "a.txt", path: "a.txt", as_history: false }] }));
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
      deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 1, has_snapshot: false,
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
    deleted_at: "2026-01-01T00:00:00Z", origin: "user", size: 1, has_snapshot: false,
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
    deleted_at: "2026-01-01T00:00:00Z", origin: "agent", size: 1, has_snapshot: false,
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
