import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n";
import { SpeechRateField } from "./SpeechRateField";
import { isValidSpeechRate } from "./speech-rate";

describe("SpeechRateField", () => {
  it("shows 字/秒 for zh, 词/秒 for en / vi / ru", () => {
    const { rerender } = render(<SpeechRateField value={null} onChange={() => {}} sourceLanguage="zh" />);
    expect(screen.getByText("字/秒")).toBeInTheDocument();

    rerender(<SpeechRateField value={null} onChange={() => {}} sourceLanguage="en" />);
    expect(screen.getByText("词/秒")).toBeInTheDocument();

    rerender(<SpeechRateField value={null} onChange={() => {}} sourceLanguage="VI" />);
    expect(screen.getByText("词/秒")).toBeInTheDocument();

    rerender(<SpeechRateField value={null} onChange={() => {}} sourceLanguage="ru" />);
    expect(screen.getByText("词/秒")).toBeInTheDocument();
  });

  it("stays unit-neutral and flags the pending unit while the language is unknown", () => {
    // Concrete units promise incorrectly for unknown languages: entered characters/second become words/second after en/vi detection.
    const { rerender } = render(<SpeechRateField value={null} onChange={() => {}} sourceLanguage={null} />);
    expect(screen.getByText("字或词/秒")).toBeInTheDocument();
    expect(screen.getByText(/项目语言还未确定/)).toBeInTheDocument();

    rerender(<SpeechRateField value={null} onChange={() => {}} sourceLanguage="zh" />);
    expect(screen.queryByText(/项目语言还未确定/)).not.toBeInTheDocument();
  });

  it("reports empty input as cleared (null), not 0", () => {
    const onChange = vi.fn();
    render(<SpeechRateField value={6} onChange={onChange} />);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("flags out-of-range values inline", () => {
    const { rerender } = render(<SpeechRateField value={6} onChange={() => {}} />);
    expect(screen.getByRole("spinbutton")).not.toHaveAttribute("aria-invalid");

    rerender(<SpeechRateField value={25} onChange={() => {}} />);
    expect(screen.getByRole("spinbutton")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("keeps native constraints no stricter than isValidSpeechRate", () => {
    // min/step stricter than isValidSpeechRate would show identical values as custom-valid and browser :invalid.
    render(<SpeechRateField value={0.05} onChange={() => {}} />);
    const input = screen.getByRole("spinbutton");
    expect(input).toHaveAttribute("min", "0");
    expect(input).toHaveAttribute("step", "any");
    expect(isValidSpeechRate(0.05)).toBe(true);
    expect((input as HTMLInputElement).checkValidity()).toBe(true);
  });

  it("accepts the whole range 0 < x <= 20, empty included", () => {
    expect(isValidSpeechRate(null)).toBe(true);
    expect(isValidSpeechRate(20)).toBe(true);
    expect(isValidSpeechRate(0.1)).toBe(true);
    expect(isValidSpeechRate(0)).toBe(false);
    expect(isValidSpeechRate(-1)).toBe(false);
    expect(isValidSpeechRate(20.5)).toBe(false);
  });

  it("rejects rates below the lower bound", () => {
    // Match backend is_valid_speech_rate; subminimum speeds overflow downstream duration conversion.
    expect(isValidSpeechRate(0.001)).toBe(true);
    expect(isValidSpeechRate(0.0009)).toBe(false);
    expect(isValidSpeechRate(1e-308)).toBe(false);
    expect(isValidSpeechRate(5e-324)).toBe(false);
  });
});
