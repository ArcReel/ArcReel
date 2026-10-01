import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { NarrationDeliveryFields, type NarrationDeliveryValue } from "./NarrationDeliveryFields";

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
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true });
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
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: false });
    renderFields(ttsProject);

    await waitFor(() => expect(screen.getByLabelText("配音语速（可选）")).toBeDisabled());
    expect(screen.getByText("所选 TTS 模型不支持调节语速，这里的设置不会生效")).toBeInTheDocument();
    expect(API.getTtsModelCapabilities).toHaveBeenCalledWith("dashscope/qwen3-tts-flash", expect.anything());
  });

  it("keeps the speed input editable for models that take a speed", async () => {
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true });
    renderFields(ttsProject);

    await waitFor(() => expect(API.getTtsModelCapabilities).toHaveBeenCalled());
    expect(screen.getByLabelText("配音语速（可选）")).toBeEnabled();
  });

  it("ignores a late capability answer for a model that is no longer selected", async () => {
    let answerPrevious: (value: { supports_speed: boolean }) => void = () => {};
    vi.spyOn(API, "getTtsModelCapabilities").mockImplementation((backend) =>
      backend === "openai/tts-1"
        ? new Promise((resolve) => {
            answerPrevious = resolve;
          })
        : Promise.resolve({ supports_speed: false }),
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
    await act(async () => answerPrevious({ supports_speed: true }));

    expect(screen.getByLabelText("配音语速（可选）")).toBeDisabled();
  });

  it("flags a TTS project without a voice", () => {
    vi.spyOn(API, "getTtsModelCapabilities").mockResolvedValue({ supports_speed: true });
    renderFields({ ...ttsProject, narrationVoice: "  " });

    expect(screen.getByLabelText("旁白音色 ID")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("选择 TTS 配音时必须填写音色")).toBeInTheDocument();
  });
});
