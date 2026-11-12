import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import i18n from "@/i18n";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageRecordsStore } from "@/stores/usage-records-store";
import {
  makeUsageRecord,
  makeUsageRecordDetail,
  makeUsageSummary,
} from "./usage-fixtures";
import { renderUsageRecordsSection } from "./usage-test-utils";

describe("UsageRecordsSection detail", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh");
    useUsageRecordsStore.setState(useUsageRecordsStore.getInitialState(), true);
    useTasksStore.setState(useTasksStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getUsageSummary").mockResolvedValue(makeUsageSummary());
    // 进行中区的取数不带时间范围，只有记录表的那一次返回行。
    vi.spyOn(API, "getUsageRecords").mockImplementation(async (query) =>
      query?.since === undefined
        ? { items: [], next_cursor: null, total: 0 }
        : { items: [makeUsageRecord({ id: 42 })], next_cursor: null, total: 1 },
    );
  });

  it("opens the dialog straight from a record deep link", async () => {
    const detail = vi
      .spyOn(API, "getUsageRecord")
      .mockResolvedValue(
        makeUsageRecordDetail({
          id: 42,
          segment_id: "E1S10",
          segment_ref: {
            episode_title: "",
            episode_position: 1,
            item_id: "S10",
            item_position: 3,
            item_excerpt: "推门而入",
            item_thumbnail: null,
          },
        }),
      );

    renderUsageRecordsSection("section=usage&record=42");

    // 标题以位置角标开头，后接集名与正文摘录，不露条目 ID。
    const dialog = await screen.findByRole("dialog", { name: /第 1 集 · 推门而入/ });
    expect(within(dialog).getByRole("img", { name: "分镜 3" })).toBeInTheDocument();
    expect(dialog).not.toHaveAccessibleName(/S10/);
    expect(detail).toHaveBeenCalledWith(42, { signal: expect.any(AbortSignal) });
  });

  it("renders every group of a finished text call", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        media_type: "text",
        provider: "minimax",
        model: "hailuo-02",
        segment_id: null,
        purpose: "script_generation",
        prompt: "写一段月台戏",
        input_tokens: 120,
        output_tokens: 30,
        usage_tokens: 150,
        output_path: "星海列车/E1S10.txt",
        last_provider_response: { id: "resp-1" },
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByRole("heading", { name: "输入" })).toBeInTheDocument();
    expect(dialog.getByText("写一段月台戏")).toBeInTheDocument();
    expect(dialog.getByRole("heading", { name: "调用" })).toBeInTheDocument();
    expect(dialog.getByText("MiniMax")).toBeInTheDocument();
    expect(dialog.getByRole("heading", { name: "产出" })).toBeInTheDocument();
    expect(dialog.getByText("星海列车/S10.txt")).toBeInTheDocument();
    expect(dialog.getByRole("heading", { name: "用量" })).toBeInTheDocument();
    expect(dialog.getByText("150")).toBeInTheDocument();
    expect(dialog.getByText("按你配置的单价估算，只作参考")).toBeInTheDocument();
    expect(
      dialog.getByRole("button", { name: "供应商原始响应" }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("links the model to its Sheet in the provider settings", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, provider: "custom-7", model: "relay/veo-3" }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(await dialog.findByRole("link", { name: "relay/veo-3" })).toHaveAttribute(
      "href",
      expect.stringContaining("section=providers&custom=7&model=relay%2Fveo-3"),
    );
  });

  it.each(["custom-7", "agent-3", "gemini"])(
    "shows the model as plain text when provider %s is no longer in the provider settings",
    async (provider) => {
      vi.spyOn(API, "getUsageRecord").mockResolvedValue(
        makeUsageRecordDetail({ id: 42, provider, model: "relay/veo-3", provider_in_settings: false }),
      );

      renderUsageRecordsSection("section=usage&record=42");
      const dialog = within(await screen.findByRole("dialog"));

      expect(await dialog.findByText("relay/veo-3")).toBeInTheDocument();
      expect(dialog.queryByRole("link", { name: "relay/veo-3" })).not.toBeInTheDocument();
    },
  );

  it("lists the four token counts with input excluding the cached parts", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        media_type: "text",
        segment_id: null,
        purpose: "script_generation",
        input_tokens: 1_000,
        output_tokens: 30,
        cache_read_tokens: 600,
        cache_write_5m_tokens: 100,
        cache_write_1h_tokens: 50,
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    const valueOf = (label: string) =>
      (dialog.getByText(label, { selector: "span, button" }).closest("div") as HTMLElement).textContent?.replace(label, "");
    // 记录里的总输入 1000 含缓存命中 600 与缓存写入 150，「输入」只剩没缓存的 250。
    expect(valueOf("输入")).toBe("250");
    expect(valueOf("缓存命中")).toBe("600");
    expect(valueOf("缓存写入")).toBe("150");
    expect(valueOf("5 分钟")).toBe("100");
    expect(valueOf("1 小时")).toBe("50");
    expect(valueOf("输出")).toBe("30");

    await userEvent.hover(dialog.getByRole("button", { name: "输入" }));
    expect(await screen.findByText("不含缓存命中与缓存写入的部分")).toBeInTheDocument();
  });

  it("shows dashes for cache counts the provider never reported", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, media_type: "text", input_tokens: 120, output_tokens: 30 }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    const valueOf = (label: string) =>
      (dialog.getByText(label, { selector: "span, button" }).closest("div") as HTMLElement).textContent?.replace(label, "");
    expect(valueOf("输入")).toBe("120");
    expect(valueOf("缓存命中")).toBe("—");
    expect(valueOf("缓存写入")).toBe("—");
    expect(dialog.queryByText("5 分钟")).not.toBeInTheDocument();
  });

  it("shows 未定价 with its reason instead of a zero amount", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, cost_amount: null, unpriced_reason: "no_price_table" }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByText("未定价")).toBeInTheDocument();
    expect(dialog.getByText("未定价原因")).toBeInTheDocument();
    expect(dialog.getByText("模型条目没有价目表")).toBeInTheDocument();
    expect(dialog.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("shows a zero amount, with no unpriced reason, for a call priced at zero", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, cost_amount: 0 }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(await dialog.findByText("$0.00")).toBeInTheDocument();
    expect(dialog.queryByText("未定价")).not.toBeInTheDocument();
    expect(dialog.queryByText("未定价原因")).not.toBeInTheDocument();
  });

  it("resolves a project-relative image output as a thumbnail", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        project_name: "demo",
        media_type: "image",
        output_path: "storyboards/scene_E1S10.png",
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByRole("img", { name: "storyboards/scene_S10.png" })).toHaveAttribute(
      "src",
      "/api/v1/files/demo/storyboards/scene_E1S10.png?w=320",
    );
  });

  it("renders the production input field names for media calls", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        media_type: "video",
        inputs: {
          reference_images: [
            { path: "characters/hero.png", label: "主角参考", role: "array" },
          ],
          start_image: "storyboards/E1S01.png",
          end_image: "storyboards/E1S02.png",
          reference_audio: ["characters/hero.wav"],
          parameters: { service_tier: "default" },
        },
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));

    expect(dialog.getByRole("img", { name: "主角参考" })).toBeInTheDocument();
    expect(dialog.getByRole("img", { name: "首帧" })).toBeInTheDocument();
    expect(dialog.getByRole("img", { name: "尾帧" })).toBeInTheDocument();
    expect(dialog.getByText("characters/hero.wav")).toBeInTheDocument();
    expect(dialog.getByText(/"service_tier": "default"/)).toBeInTheDocument();
  });

  it("shows 无记录 for groups the record never captured", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, prompt: null, inputs: null, output_path: null }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    await screen.findByRole("dialog");

    // 输入与产出两组都无数据：显示「无记录」而不是消失或报错。
    expect(screen.getAllByText("无记录")).toHaveLength(2);
  });

  it.each([
    {
      code: "narration_tts_speed_out_of_range",
      params: { minimum: 0.7, maximum: 1.5, speed: 0.5 },
      message: "所选 TTS 模型的配音语速范围是 0.7～1.5，当前为 0.5。请调整或清空配音语速",
    },
    {
      code: "image_reference_images_exceeded",
      params: { model: "m", limit: 1, count: 2 },
      message: "模型 m 最多支持 1 张参考图，本次请求带了 2 张，未提交生成；请重新生成",
    },
    {
      code: "video_duration_not_supported",
      params: { duration: 7, supported: "5, 10" },
      message: "视频时长 7s 不在该模型支持的时长（5, 10）内",
    },
    {
      code: "video_reference_audio_duration_exceeded",
      params: { model: "m", limit: 15, total: 16.04 },
      message: "模型 m 参考音频总时长最多 15 秒，当前合计 16.0 秒；请减少参考音频段数或换用更短的音频",
    },
  ])("localizes $code with the recorded parameters", async ({ code, params, message }) => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, status: "failed", error_code: code, error_params: params, error_message: code }),
    );
    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(message)).toBeInTheDocument();
    expect(dialog.queryByText(code)).not.toBeInTheDocument();
  });

  it.each([
    {
      code: "ref_payload_floor_exceeded", params: {},
      zh: "参考图过大或过多，压缩到最低画质仍超出供应商请求体上限，请减少参考图数量或降低分辨率后重试",
      en: "Reference images are too large or too many; even compressed to the lowest quality they still exceed the provider's request size limit. Please reduce the number of reference images or their resolution and try again",
    },
    {
      code: "ark_agent_plan_tts_unreachable", params: {},
      zh: "多次重试后仍无法连接火山方舟 Agent Plan 语音合成服务，没有生成音频，也不会计入套餐用量。检查网络与代理设置后重试",
      en: "Could not connect to the Volcengine Ark Agent Plan speech synthesis service after several retries. No audio was generated, and nothing counts toward your plan usage. Check your network and proxy settings, then retry",
    },
    {
      code: "image_output_missing", params: { model: "m" },
      zh: "模型 m 没有返回图片，可能触发了内容安全过滤；请调整提示词或参考图后重试",
      en: "Model m returned no image, possibly because of content safety filtering; adjust the prompt or reference images and try again",
    },
    {
      code: "image_output_format_unsupported", params: { model: "m", media_type: "image/svg+xml" },
      zh: "模型 m 返回了 image/svg+xml 格式的图片，无法转成 PNG 使用；请换用输出位图的模型",
      en: "Model m returned an image in image/svg+xml format that cannot be converted to PNG; switch to a model that outputs raster images",
    },
    {
      code: "declarative_response_extract_failed", params: { detail: "missing output" },
      zh: "调用端点响应提取失败：missing output",
      en: "Endpoint response extraction failed: missing output",
    },
    {
      code: "declarative_response_extract_failed", params: { detail: "x".repeat(1900) },
      zh: `调用端点响应提取失败：${"x".repeat(1900)}`,
      en: `Endpoint response extraction failed: ${"x".repeat(1900)}`,
    },
    {
      code: "comfyui_output_type_mismatch", params: { filename: "result.webm", media_type: "video" },
      zh: "ComfyUI 产出的 result.webm 不是这个端点应产出的文件类型（应为 .m4v / .mov / .mp4），请把产物节点的节点绑定指到导出最终产物的那个节点，或在该保存节点上改用受支持的格式（如 SaveVideo 选 h264-mp4）",
      en: "ComfyUI produced result.webm, which is not a file type this endpoint should output (expected .m4v / .mov / .mp4); point the output node bindings at the node that exports the final artifact, or switch that save node to a supported format (for SaveVideo, pick h264-mp4)",
    },
    {
      code: "declarative_template_render_failed",
      params: { detail: { key: "val_ce_undeclared_variable", params: { name: "prompt" } } },
      zh: "调用端点请求渲染失败：占位符 prompt 引用了未声明的变量",
      en: "Endpoint request rendering failed: Placeholder prompt references a variable that is not declared",
    },
  ])("translates recorded $code when the language changes", async ({ code, params, zh, en }) => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, status: "failed", error_code: code, error_params: params, error_message: code }),
    );
    renderUsageRecordsSection("section=usage&record=42");
    await screen.findByRole("dialog");
    expect(await screen.findByText(zh)).toBeInTheDocument();
    await act(() => i18n.changeLanguage("en"));
    expect(await screen.findByText(en)).toBeInTheDocument();
    expect(screen.queryByText(code)).not.toBeInTheDocument();
  });

  it.each([
    { error_code: null, error_params: null, error_message: "narration_tts_speed_out_of_range" },
    { error_code: "future_error", error_params: {}, error_message: "original failure" },
  ])("preserves legacy or unknown failure text: $error_message", async (failure) => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({ id: 42, status: "failed", ...failure }),
    );
    renderUsageRecordsSection("section=usage&record=42");
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(failure.error_message)).toBeInTheDocument();
    expect(dialog.queryByText(/\{\{/)).not.toBeInTheDocument();
  });

  it("updates the domain reason in all four languages without refetching", async () => {
    const getRecord = vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42, status: "failed", error_code: "narration_tts_speed_out_of_range",
        error_params: { minimum: 0.7, maximum: 1.5, speed: 0.5 },
        error_message: "narration_tts_speed_out_of_range",
      }),
    );
    renderUsageRecordsSection("section=usage&record=42");
    await screen.findByRole("dialog");
    for (const [language, message] of [
      ["zh", "所选 TTS 模型的配音语速范围是 0.7～1.5，当前为 0.5。请调整或清空配音语速"],
      ["en", "The selected TTS model supports voice speeds from 0.7 to 1.5, but the current value is 0.5. Change or clear the voice speed"],
      ["vi", "Mô hình TTS đã chọn hỗ trợ tốc độ giọng đọc từ 0.7 đến 1.5, giá trị hiện tại là 0.5. Hãy thay đổi hoặc xóa tốc độ giọng đọc"],
      ["ru", "Выбранная модель TTS поддерживает скорость голоса от 0.7 до 1.5, текущее значение: 0.5. Измените или очистите скорость голоса"],
    ]) {
      await act(() => i18n.changeLanguage(language));
      expect(await screen.findByText(message)).toBeInTheDocument();
    }
    expect(getRecord).toHaveBeenCalledTimes(1);
  });

  it("renders the failure phrase above the raw message", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(
      makeUsageRecordDetail({
        id: 42,
        status: "failed",
        error_code: "rate_limited",
        error_params: { retry_after_seconds: 30 },
        error_message: "HTTP 429",
      }),
    );

    renderUsageRecordsSection("section=usage&record=42");
    await screen.findByRole("dialog");

    expect(screen.getByRole("heading", { name: "失败原因" })).toBeInTheDocument();
    expect(screen.getByText("限流")).toBeInTheDocument();
    expect(screen.getByText("重试等待")).toBeInTheDocument();
    expect(screen.getByText("30 s")).toBeInTheDocument();
    expect(screen.getByText("HTTP 429")).toBeInTheDocument();
  });

  it("does not reset pagination when opening or closing a record", async () => {
    vi.mocked(API.getUsageRecords).mockImplementation(async (query) => {
      if (query?.since === undefined) return { items: [], next_cursor: null, total: 0 };
      return query.cursor
        ? { items: [makeUsageRecord({ id: 42 })], next_cursor: null, total: 40 }
        : { items: [makeUsageRecord({ id: 1 })], next_cursor: "cursor-2", total: 40 };
    });
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(makeUsageRecordDetail({ id: 42 }));

    renderUsageRecordsSection("section=usage");
    await userEvent.click(await screen.findByRole("button", { name: "下一页" }));
    expect(await screen.findByText("21～21 / 40")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "详情" }));
    await screen.findByRole("dialog");
    expect(screen.getByText("21～21 / 40")).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("21～21 / 40")).toBeInTheDocument();
  });

  it("closes on Escape and drops the record param from the URL", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(makeUsageRecordDetail({ id: 42 }));

    const { location } = renderUsageRecordsSection("section=usage&record=42");
    await screen.findByRole("dialog");

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(location.history.at(-1)).toBe("/app/settings?section=usage"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the dialog from the row action and writes the id into the URL", async () => {
    vi.spyOn(API, "getUsageRecord").mockResolvedValue(makeUsageRecordDetail({ id: 42 }));

    const { location } = renderUsageRecordsSection("section=usage");
    await screen.findByRole("button", { name: "详情" });

    await userEvent.click(screen.getByRole("button", { name: "详情" }));

    await waitFor(() =>
      expect(location.history.at(-1)).toBe("/app/settings?section=usage&record=42"),
    );
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
