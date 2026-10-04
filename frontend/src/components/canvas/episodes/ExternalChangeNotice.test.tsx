import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ExternalSourceChange, SourceFileChangeResponse, SourceFileImpact } from "@/types";

import { ExternalChangeNotice } from "./ExternalChangeNotice";

const NO_IMPACT: SourceFileImpact = {
  shifted: [],
  changed_with_products: [],
  changed_without_products: [],
  retired: [],
  removed: [],
  kind_stale: [],
};

const CHANGED: ExternalSourceChange = {
  source_file: "source/上卷.txt",
  impact: { ...NO_IMPACT, changed_without_products: [1], text: "原文有变化、还没有产物，标为「原文已重新规划」：下山" },
  revision: "r1",
  problem: null,
};

describe("ExternalChangeNotice", () => {
  let refresh: MockInstance;

  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    refresh = vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
  });

  it("lists the affected episodes and updates the ledger with the listed revision", async () => {
    const accept = vi
      .spyOn(API, "acceptExternalSourceChange")
      .mockResolvedValue({ status: "applied", impact: { ...NO_IMPACT, changed_without_products: [1] } });
    render(<ExternalChangeNotice projectName="demo" changes={[CHANGED]} onLocate={() => {}} />);

    expect(screen.getByRole("heading", { name: "「上卷.txt」在 ArcReel 之外被改动过" })).toBeInTheDocument();
    expect(screen.getByText("原文有变化、还没有产物，标为「原文已重新规划」：下山")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "更新分集账本" }));

    await waitFor(() => expect(refresh).toHaveBeenCalledWith("demo"));
    expect(accept).toHaveBeenCalledWith("demo", "上卷.txt", "r1");
  });

  it("sends one update while the first request is still in flight", async () => {
    let finish: (reply: SourceFileChangeResponse) => void = () => {};
    const accept = vi
      .spyOn(API, "acceptExternalSourceChange")
      .mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    render(<ExternalChangeNotice projectName="demo" changes={[CHANGED]} onLocate={() => {}} />);

    const button = screen.getByRole("button", { name: "更新分集账本" });
    act(() => {
      button.click();
      button.click();
    });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(accept).toHaveBeenCalledTimes(1);

    await act(async () => finish({ status: "applied", impact: NO_IMPACT }));
    await waitFor(() => expect(refresh).toHaveBeenCalledWith("demo"));
    expect(button).toBeEnabled();
  });

  it("asks again when the list changed since the view was loaded", async () => {
    const accept = vi
      .spyOn(API, "acceptExternalSourceChange")
      .mockResolvedValueOnce({
        status: "confirmation_required",
        impact: { ...NO_IMPACT, removed: [2], text: "原文整段被删、还没有产物，直接移除：雨夜" },
        revision: "r2",
      })
      .mockResolvedValueOnce({ status: "applied", impact: { ...NO_IMPACT, removed: [2] } });
    render(<ExternalChangeNotice projectName="demo" changes={[CHANGED]} onLocate={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: "更新分集账本" }));
    expect(await screen.findByText("原文整段被删、还没有产物，直接移除：雨夜")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "更新分集账本" }).at(-1)!);

    await waitFor(() => expect(accept).toHaveBeenCalledTimes(2));
    expect(accept.mock.calls[1]).toEqual(["demo", "上卷.txt", "r2"]);
  });

  it("states why a file cannot be aligned and offers no update", () => {
    const onLocate = vi.fn();
    const problem = { ...CHANGED, impact: null, revision: null, problem: "这个文件没有留存改动前的文本" };
    render(<ExternalChangeNotice projectName="demo" changes={[problem]} onLocate={onLocate} />);

    expect(screen.getByRole("alert")).toHaveTextContent("这个文件没有留存改动前的文本");
    expect(screen.queryByRole("button", { name: "更新分集账本" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看文件" }));
    expect(onLocate).toHaveBeenCalledWith("source/上卷.txt");
  });
});
