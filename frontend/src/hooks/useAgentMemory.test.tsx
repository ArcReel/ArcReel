import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAgentMemory } from "@/hooks/useAgentMemory";
import type { AgentMemoryOverview } from "@/types/agent-memory";

const OVERVIEW: AgentMemoryOverview = {
  path: "/data/.arcreel/users/default/memory",
  index: { exists: true, line_count: 3, byte_size: 120, over_limit: false },
  files: [
    {
      name: "tone.md",
      size: 128,
      modified_at: "2026-09-01T03:58:41.123456+00:00",
      frontmatter: { name: "tone", description: "配音口味", type: "user" },
    },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useAgentMemory", () => {
  it("拉取一次并暴露记忆目录，scope 原样回传给调用点", async () => {
    const spy = vi.spyOn(API, "getAgentMemory").mockResolvedValue(OVERVIEW);
    const { result } = renderHook(() => useAgentMemory({ level: "user" }));

    await waitFor(() => expect(result.current.overview).toEqual(OVERVIEW));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.scope).toEqual({ level: "user" });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toEqual({ level: "user" });
  });

  it("项目级 scope 把项目名带进请求", async () => {
    const spy = vi.spyOn(API, "getAgentMemory").mockResolvedValue(OVERVIEW);
    const { result } = renderHook(() => useAgentMemory({ level: "project", projectName: "demo" }));

    await waitFor(() => expect(result.current.overview).toEqual(OVERVIEW));
    expect(spy.mock.calls[0][0]).toEqual({ level: "project", projectName: "demo" });
  });

  it("调用点每次渲染传入新的 scope 字面量也不重复拉取", async () => {
    const spy = vi.spyOn(API, "getAgentMemory").mockResolvedValue(OVERVIEW);
    const { result, rerender } = renderHook(() => useAgentMemory({ level: "project", projectName: "demo" }));

    await waitFor(() => expect(result.current.overview).toEqual(OVERVIEW));
    rerender();
    rerender();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("换项目即重取，旧目录的数据不残留", async () => {
    const other: AgentMemoryOverview = { ...OVERVIEW, path: "/projects/b/.arcreel/memory", files: [] };
    const spy = vi
      .spyOn(API, "getAgentMemory")
      .mockResolvedValueOnce(OVERVIEW)
      .mockResolvedValueOnce(other);
    const { result, rerender } = renderHook(
      ({ project }: { project: string }) => useAgentMemory({ level: "project", projectName: project }),
      { initialProps: { project: "a" } },
    );

    await waitFor(() => expect(result.current.overview).toEqual(OVERVIEW));
    rerender({ project: "b" });
    await waitFor(() => expect(result.current.overview).toEqual(other));
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy.mock.calls[1][0]).toEqual({ level: "project", projectName: "b" });
  });

  it("换项目后新目录响应到达前不露出旧目录的内容", async () => {
    const other: AgentMemoryOverview = { ...OVERVIEW, path: "/projects/b/.arcreel/memory", files: [] };
    let resolveB: ((value: AgentMemoryOverview) => void) | null = null;
    vi.spyOn(API, "getAgentMemory")
      .mockResolvedValueOnce(OVERVIEW)
      .mockImplementationOnce(
        () =>
          new Promise<AgentMemoryOverview>((resolve) => {
            resolveB = resolve;
          }),
      );
    const { result, rerender } = renderHook(
      ({ project }: { project: string }) => useAgentMemory({ level: "project", projectName: project }),
      { initialProps: { project: "a" } },
    );

    await waitFor(() => expect(result.current.overview).toEqual(OVERVIEW));
    rerender({ project: "b" });

    expect(result.current.overview).toBeNull();
    expect(result.current.loading).toBe(true);

    await act(async () => {
      resolveB?.(other);
    });
    expect(result.current.overview).toEqual(other);
  });

  it("拉取失败时给出可读说明，重试成功后清空错误", async () => {
    vi.spyOn(API, "getAgentMemory")
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(OVERVIEW);
    const { result } = renderHook(() => useAgentMemory({ level: "user" }));

    await waitFor(() => expect(result.current.error).toBe("boom"));
    expect(result.current.overview).toBeNull();
    expect(result.current.loading).toBe(false);

    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.error).toBeNull();
    expect(result.current.overview).toEqual(OVERVIEW);
  });

  it("卸载时作废在途请求", async () => {
    const signals: (AbortSignal | undefined)[] = [];
    vi.spyOn(API, "getAgentMemory").mockImplementation((_scope, options = {}) => {
      signals.push(options.signal);
      return new Promise<AgentMemoryOverview>(() => {});
    });
    const { unmount } = renderHook(() => useAgentMemory({ level: "user" }));

    await waitFor(() => expect(signals).toHaveLength(1));
    unmount();
    expect(signals[0]?.aborted).toBe(true);
  });
});
