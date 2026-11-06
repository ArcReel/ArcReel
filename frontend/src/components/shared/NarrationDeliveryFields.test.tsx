import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import "@/i18n";
import { API } from "@/api";
import { NarrationDeliveryFields } from "./NarrationDeliveryFields";
import type { TtsModelCapabilitiesResponse } from "@/types";
import type { NarrationDeliveryValue } from "./narration-delivery";

const ttsProject: NarrationDeliveryValue = {
  delivery: "use_tts",
  audioBackend: "dashscope/qwen3-tts-flash",
  narrationVoice: "Cherry",
  narrationSpeed: 1.2,
};

function renderFields(value: NarrationDeliveryValue, onChange = vi.fn()) {
  render(
    <NarrationDeliveryFields
      value={value}
      onChange={onChange}
      audioBackends={["dashscope/qwen3-tts-flash", "openai/tts-1"]}
      providerNames={{ dashscope: "DashScope", openai: "OpenAI" }}
      modelNames={{}}
    />,
  );
  return onChange;
}

describe("NarrationDeliveryFields", () => {
  it("switching delivery keeps the TTS snapshot", () => {
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true, voices: [] });
    const onChange = renderFields(ttsProject);

    fireEvent.click(screen.getByRole("radio", { name: "后期配音" }));

    expect(onChange).toHaveBeenCalledWith({ ...ttsProject, delivery: "post_production" });
  });

  it("hides the TTS snapshot for post-production projects", () => {
    renderFields({ ...ttsProject, delivery: "post_production" });

    expect(screen.queryByLabelText("旁白音色 ID")).not.toBeInTheDocument();
    expect(screen.getByText(/ArcReel 不生成旁白配音/)).toBeInTheDocument();
  });

  it("disables the speed input with a reason when the model cannot take a speed", async () => {
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: false, voices: [] });
    renderFields(ttsProject);

    await waitFor(() => expect(screen.getByLabelText("配音语速（可选）")).toBeDisabled());
    expect(screen.getByText("所选 TTS 模型不支持调节语速，这里的设置不会生效")).toBeInTheDocument();
    expect(API.getTtsModelCapabilities).toHaveBeenCalledWith("dashscope/qwen3-tts-flash", expect.anything());
  });

  it("keeps the speed input editable for models that take a speed", async () => {
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true, voices: [] });
    renderFields(ttsProject);

    await waitFor(() => expect(API.getTtsModelCapabilities).toHaveBeenCalled());
    expect(screen.getByLabelText("配音语速（可选）")).toBeEnabled();
  });

  it("ignores a late capability answer for a model that is no longer selected", async () => {
    let answerPrevious: (value: TtsModelCapabilitiesResponse) => void = () => {};
    vi.spyOn(API, "getTtsModelCapabilities").mockImplementation((backend) =>
      backend === "openai/tts-1"
        ? new Promise((resolve) => {
            answerPrevious = resolve;
          })
        : Promise.resolve({ supports_speed: false, voices: [] }),
    );
    const props = {
      onChange: vi.fn(),
      audioBackends: ["dashscope/qwen3-tts-flash", "openai/tts-1"],
      providerNames: { dashscope: "DashScope", openai: "OpenAI" },
      modelNames: {},
    };
    const { rerender } = render(
      <NarrationDeliveryFields {...props} value={{ ...ttsProject, audioBackend: "openai/tts-1" }} />,
    );
    await waitFor(() => expect(API.getTtsModelCapabilities).toHaveBeenCalledWith("openai/tts-1", expect.anything()));

    rerender(<NarrationDeliveryFields {...props} value={ttsProject} />);
    await waitFor(() => expect(screen.getByLabelText("配音语速（可选）")).toBeDisabled());
    await act(async () => answerPrevious({ supports_speed: true, voices: [] }));

    expect(screen.getByLabelText("配音语速（可选）")).toBeDisabled();
  });

  it("lists the voices of the selected model's endpoint as suggestions while keeping free text", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({
      supports_speed: true,
      voices: [
        { id: "longanhuan_v3.1", label: "龙安欢 · 女声" },
        { id: "xunanchuan_v3.1", label: "许南川 · 男声" },
      ],
    });
    const onChange = vi.fn();
    function Form() {
      const [value, setValue] = useState({ ...ttsProject, audioBackend: "dashscope/qwen-audio-3.1-tts-plus" });
      return (
        <NarrationDeliveryFields
          value={value}
          onChange={(next) => { setValue(next); onChange(next); }}
          audioBackends={[value.audioBackend]}
          providerNames={{ dashscope: "DashScope" }}
          modelNames={{}}
        />
      );
    }
    render(<Form />);

    const input = screen.getByLabelText("旁白音色 ID");
    await screen.findByText("从列表选择音色，或照所选供应商文档填写音色 id");
    await user.click(input);
    await user.clear(input);
    await user.click(input);
    expect(await screen.findByRole("option", { name: "xunanchuan_v3.1 · 许南川 · 男声" })).toBeVisible();
    await user.click(screen.getByRole("option", { name: "longanhuan_v3.1 · 龙安欢 · 女声" }));
    expect(onChange).toHaveBeenCalledWith({
      ...ttsProject,
      audioBackend: "dashscope/qwen-audio-3.1-tts-plus",
      narrationVoice: "longanhuan_v3.1",
    });

    fireEvent.change(input, { target: { value: "my-cloned-voice" } });
    expect(onChange).toHaveBeenCalledWith({
      ...ttsProject,
      audioBackend: "dashscope/qwen-audio-3.1-tts-plus",
      narrationVoice: "my-cloned-voice",
    });
  });

  it("offers no suggestions when the model has no voice list", async () => {
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true, voices: [] });
    renderFields(ttsProject);

    await waitFor(() => expect(API.getTtsModelCapabilities).toHaveBeenCalled());
    await userEvent.click(screen.getByLabelText("旁白音色 ID"));
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByText("照所选供应商文档填写音色 id")).toBeInTheDocument();
  });

  it("flags a TTS project without a voice", () => {
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true, voices: [] });
    renderFields({ ...ttsProject, narrationVoice: "  " });

    expect(screen.getByLabelText("旁白音色 ID")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("选择 TTS 配音时必须填写音色")).toBeInTheDocument();
  });
});
