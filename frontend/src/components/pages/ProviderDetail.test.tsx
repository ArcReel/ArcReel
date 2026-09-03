import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { createDeferred } from "@/test/deferred";
import { API } from "@/api";
import type { ProviderConfigDetail } from "@/types";
import { ProviderDetail } from "./ProviderDetail";

function detailFor(language: string, maxWorkers = "2"): ProviderConfigDetail {
  return {
    id: "gemini-aistudio",
    display_name: language === "en" ? "Gemini AI Studio (EN)" : "Gemini AI Studio（中文）",
    description: "",
    status: "ready",
    media_types: ["video"],
    fields: [
      {
        key: "max_workers",
        label: "Max Workers",
        type: "number",
        required: false,
        is_set: true,
        value: maxWorkers,
      },
    ],
    supports_base_url: false,
    secret_fields: [],
    secret_field_groups: [],
  };
}

describe("ProviderDetail", () => {
  beforeEach(async () => {
    await act(async () => i18n.changeLanguage("zh"));
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("refetches once on language change without discarding the draft", async () => {
    const getDetail = vi
      .spyOn(API, "getProviderConfig")
      .mockImplementation(() => Promise.resolve(detailFor(i18n.language)));

    render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    expect(getDetail).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    const workers = screen.getByRole("spinbutton", { name: "Max Workers" });
    fireEvent.change(workers, { target: { value: "7" } });

    await act(async () => i18n.changeLanguage("en"));

    await screen.findByText("Gemini AI Studio (EN)");
    await waitFor(() => expect(getDetail).toHaveBeenCalledTimes(2));
    expect(workers).toHaveValue(7);
  });

  it("clears the load error once a language refetch succeeds", async () => {
    const getDetail = vi
      .spyOn(API, "getProviderConfig")
      .mockRejectedValueOnce(new Error("boom"))
      .mockImplementation(() => Promise.resolve(detailFor(i18n.language)));

    render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("boom");

    await act(async () => i18n.changeLanguage("en"));

    await screen.findByText("Gemini AI Studio (EN)");
    expect(screen.queryByText("boom")).not.toBeInTheDocument();
    expect(getDetail).toHaveBeenCalledTimes(2);
  });

  it("surfaces a language refetch failure when no detail is on screen yet", async () => {
    // 首轮请求还在途时切换语言：接管的那次失败后没有可展示的详情，必须报错并给出重试入口，
    // 否则页面停在加载态。
    let resolveFirst: (detail: ProviderConfigDetail) => void = () => {};
    vi.spyOn(API, "getProviderConfig")
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockRejectedValue(new Error("refetch failed"));

    render(<ProviderDetail providerId="gemini-aistudio" />);
    await act(async () => i18n.changeLanguage("en"));
    await act(async () => resolveFirst(detailFor("zh")));

    await screen.findByText("refetch failed");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("keeps the saved detail when a slower language refetch lands last", async () => {
    let resolveLanguage: (detail: ProviderConfigDetail) => void = () => {};
    vi.spyOn(API, "getProviderConfig")
      .mockResolvedValueOnce(detailFor("zh"))
      .mockImplementationOnce(() => new Promise((resolve) => (resolveLanguage = resolve)))
      .mockResolvedValueOnce(detailFor("en", "7"));
    vi.spyOn(API, "patchProviderConfig").mockResolvedValue(undefined);

    render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });

    await act(async () => i18n.changeLanguage("en"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Gemini AI Studio (EN)");

    // 语言重取最后才返回，带的是保存前的旧值——它已被保存后的重取接管，不得回写。
    await act(async () => resolveLanguage(detailFor("en")));
    expect(screen.getByRole("spinbutton", { name: "Max Workers" })).toHaveValue(7);
  });

  it("does not refetch the old provider after the panel switched to another one", async () => {
    const patch = createDeferred<void>();
    vi.spyOn(API, "patchProviderConfig").mockReturnValue(patch.promise);
    const getDetail = vi
      .spyOn(API, "getProviderConfig")
      .mockImplementation((id) => Promise.resolve({ ...detailFor(i18n.language), id }));

    const { rerender } = render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    rerender(<ProviderDetail providerId="openai-compatible" />);
    await waitFor(() => expect(getDetail).toHaveBeenLastCalledWith("openai-compatible", expect.anything()));
    const callsBeforePatchLands = getDetail.mock.calls.length;

    // 保存的后续重取属于已经离场的供应商：既不该再打请求，也不该作废新供应商的加载。
    await act(async () => {
      patch.resolve();
      await patch.promise;
    });
    expect(getDetail.mock.calls.length).toBe(callsBeforePatchLands);
  });

  it("leaves the new provider's draft and errors untouched when an old save settles", async () => {
    const patch = createDeferred<void>();
    vi.spyOn(API, "patchProviderConfig").mockReturnValue(patch.promise);
    vi.spyOn(API, "getProviderConfig").mockImplementation((id) =>
      Promise.resolve({ ...detailFor(i18n.language), id }),
    );

    const { rerender } = render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    rerender(<ProviderDetail providerId="openai-compatible" />);
    // 高级区保持展开：切换供应商只重置详情与草稿，不重置本地展开态
    const workers = await screen.findByRole("spinbutton", { name: "Max Workers" });
    fireEvent.change(workers, { target: { value: "9" } });

    // 旧供应商的保存失败：错误属于它，不该出现在当前供应商的表单上，草稿也不该被清掉
    await act(async () => {
      patch.reject(new Error("A 保存失败"));
      await patch.promise.catch(() => {});
    });

    expect(screen.queryByText(/A 保存失败/)).not.toBeInTheDocument();
    expect(workers).toHaveValue(9);
  });

  it("keeps the new provider's draft when an old save succeeds", async () => {
    const patch = createDeferred<void>();
    vi.spyOn(API, "patchProviderConfig").mockReturnValue(patch.promise);
    vi.spyOn(API, "getProviderConfig").mockImplementation((id) =>
      Promise.resolve({ ...detailFor(i18n.language), id }),
    );
    const onSaved = vi.fn();

    const { rerender } = render(<ProviderDetail providerId="gemini-aistudio" onSaved={onSaved} />);
    await screen.findByText("Gemini AI Studio（中文）");
    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    rerender(<ProviderDetail providerId="openai-compatible" onSaved={onSaved} />);
    const workers = await screen.findByRole("spinbutton", { name: "Max Workers" });
    fireEvent.change(workers, { target: { value: "9" } });

    await act(async () => {
      patch.resolve();
      await patch.promise;
    });

    // 旧供应商保存成功：目录照常刷新，但当前供应商上没保存的编辑不能被它清掉
    expect(workers).toHaveValue(9);
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("re-enables the save button on the new provider while an old save is still in flight", async () => {
    const patch = createDeferred<void>();
    vi.spyOn(API, "patchProviderConfig").mockReturnValue(patch.promise);
    vi.spyOn(API, "getProviderConfig").mockImplementation((id) =>
      Promise.resolve({ ...detailFor(i18n.language), id }),
    );

    const { rerender } = render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("button", { name: /保存中/ })).toBeDisabled();

    // 切到别的供应商就是新的一次面板停留：上一次保存的进行态属于上一次停留，
    // 不能让新面板的保存按钮跟着一起禁用。
    rerender(<ProviderDetail providerId="openai-compatible" />);
    const workers = await screen.findByRole("spinbutton", { name: "Max Workers" });
    fireEvent.change(workers, { target: { value: "9" } });
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();

    // 旧保存随后结算：收尾按代次判定，不把新面板重新推回保存中
    await act(async () => {
      patch.resolve();
      await patch.promise;
    });

    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
    expect(workers).toHaveValue(9);
  });

  it("keeps the new provider's own save in progress when an older save settles first", async () => {
    const first = createDeferred<void>();
    const second = createDeferred<void>();
    vi.spyOn(API, "patchProviderConfig")
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    vi.spyOn(API, "getProviderConfig").mockImplementation((id) =>
      Promise.resolve({ ...detailFor(i18n.language), id }),
    );

    const { rerender } = render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    rerender(<ProviderDetail providerId="openai-compatible" />);
    fireEvent.change(await screen.findByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "9" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("button", { name: /保存中/ })).toBeDisabled();

    // 旧供应商的 PATCH 后到：它的收尾不能把新面板从自己的保存中态里放出来，
    // 否则同一份草稿会被重复提交。
    await act(async () => {
      first.resolve();
      await first.promise;
    });
    expect(screen.getByRole("button", { name: /保存中/ })).toBeDisabled();

    // 新面板自己的 PATCH 结算后才收尾：草稿清空，保存按钮随之收起。
    await act(async () => {
      second.resolve();
      await second.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /保存/ })).not.toBeInTheDocument(),
    );
  });

  it("does not clear the draft when a save from an earlier visit to the same provider settles", async () => {
    const patch = createDeferred<void>();
    vi.spyOn(API, "patchProviderConfig").mockReturnValue(patch.promise);
    vi.spyOn(API, "getProviderConfig").mockImplementation((id) =>
      Promise.resolve({ ...detailFor(i18n.language), id }),
    );

    const { rerender } = render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    // 离开又切回同一个供应商：providerId 相同，但这已经是新的一次停留，旧保存的收尾
    // 不能再认领它——否则新输入的草稿会被清掉。
    rerender(<ProviderDetail providerId="openai-compatible" />);
    await screen.findByRole("spinbutton", { name: "Max Workers" });
    rerender(<ProviderDetail providerId="gemini-aistudio" />);
    const workers = await screen.findByRole("spinbutton", { name: "Max Workers" });
    fireEvent.change(workers, { target: { value: "9" } });

    await act(async () => {
      patch.resolve();
      await patch.promise;
    });

    expect(workers).toHaveValue(9);
  });

  it("refreshes the catalog after a credential change even if the detail refetch is aborted", async () => {
    vi.mocked(API.listCredentials).mockResolvedValue({
      credentials: [
        {
          id: 1,
          provider: "gemini-aistudio",
          name: "主号",
          api_key_masked: "AI***",
          credentials_filename: null,
          base_url: null,
          is_active: false,
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    const detailRefetch = createDeferred<ProviderConfigDetail>();
    vi.spyOn(API, "getProviderConfig")
      .mockResolvedValueOnce(detailFor("zh"))
      .mockImplementationOnce(
        (_id, options) =>
          new Promise((_resolve, reject) => {
            options?.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
            void detailRefetch.promise;
          }),
      )
      .mockImplementation(() => Promise.resolve(detailFor(i18n.language)));
    vi.spyOn(API, "activateCredential").mockResolvedValue(undefined);
    const onSaved = vi.fn();

    const { rerender } = render(<ProviderDetail providerId="gemini-aistudio" onSaved={onSaved} />);
    fireEvent.click(await screen.findByRole("button", { name: "激活 主号" }));
    await waitFor(() => expect(API.activateCredential).toHaveBeenCalled());

    // 凭证已经改完：切换供应商作废了随后的详情重取，侧栏状态仍须刷新
    rerender(<ProviderDetail providerId="openai-compatible" onSaved={onSaved} />);
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    // 新供应商的凭证列表也要在替身还在时拉完，否则它落在用例之外打真实请求
    await act(async () => {});
  });

  it("completes the save bookkeeping when the post-save refetch is superseded", async () => {
    vi.spyOn(API, "patchProviderConfig").mockResolvedValue(undefined);
    vi.spyOn(API, "getProviderConfig")
      .mockResolvedValueOnce(detailFor("zh"))
      // 保存后的重取挂起，直到被接管方 abort——与真实 fetch 的行为一致
      .mockImplementationOnce(
        (_id, options) =>
          new Promise((_resolve, reject) => {
            options?.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      )
      .mockImplementation(() => Promise.resolve(detailFor(i18n.language)));
    const onSaved = vi.fn();

    render(<ProviderDetail providerId="gemini-aistudio" onSaved={onSaved} />);
    await screen.findByText("Gemini AI Studio（中文）");
    fireEvent.click(screen.getByRole("button", { name: "高级配置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(API.getProviderConfig).toHaveBeenCalledTimes(2));

    await act(async () => i18n.changeLanguage("en"));

    // PATCH 已经成功：草稿要清、目录要刷新，否则已入库的值仍标着未保存、还能被重复提交。
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole("spinbutton", { name: "Max Workers" })).toHaveValue(2),
    );
  });
});
