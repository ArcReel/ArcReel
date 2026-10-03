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

  it("refetches once on language change without discarding unsaved edits", async () => {
    const getDetail = vi
      .spyOn(API, "getProviderConfig")
      .mockImplementation(() => Promise.resolve(detailFor(i18n.language)));

    render(<ProviderDetail providerId="gemini-aistudio" />);
    await screen.findByText("Gemini AI Studio（中文）");
    expect(getDetail).toHaveBeenCalledTimes(1);

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

    render(<ProviderDetail providerId="gemini-aistudio" onSaved={onSaved} />);
    fireEvent.click(await screen.findByRole("button", { name: "激活 主号" }));
    await waitFor(() => expect(API.activateCredential).toHaveBeenCalled());

    // 密钥已经改完：随后的详情重取被语言重取作废，二级栏的数量与状态仍须刷新
    await act(async () => i18n.changeLanguage("en"));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  });

  it("patches only the changed fields and clears emptied ones", async () => {
    const detail = detailFor("zh");
    detail.fields.push({ key: "gcs_bucket", label: "GCS Bucket", type: "text", required: false, is_set: true, value: "old" });
    vi.spyOn(API, "getProviderConfig").mockResolvedValue(detail);
    const patch = vi.spyOn(API, "patchProviderConfig").mockResolvedValue(undefined);

    render(<ProviderDetail providerId="gemini-aistudio" />);
    fireEvent.change(await screen.findByRole("textbox", { name: "GCS 存储桶" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith("gemini-aistudio", { gcs_bucket: null }));
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
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max Workers" }), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(API.getProviderConfig).toHaveBeenCalledTimes(2));

    await act(async () => i18n.changeLanguage("en"));

    // PATCH 已经成功：草稿要清、目录要刷新，否则已入库的值仍标着未保存、还能被重复提交。
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeDisabled());
    expect(screen.getByRole("spinbutton", { name: "Max Workers" })).toHaveValue(7);
  });
});
