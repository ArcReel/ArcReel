import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n";
import { SpeechRateField, isValidSpeechRate } from "./SpeechRateField";

describe("SpeechRateField", () => {
  it("shows 字/秒 for zh and unknown languages, 词/秒 for en / vi", () => {
    const { rerender } = render(<SpeechRateField value={null} onChange={() => {}} sourceLanguage="zh" />);
    expect(screen.getByText("字/秒")).toBeInTheDocument();

    rerender(<SpeechRateField value={null} onChange={() => {}} sourceLanguage={null} />);
    expect(screen.getByText("字/秒")).toBeInTheDocument();

    rerender(<SpeechRateField value={null} onChange={() => {}} sourceLanguage="en" />);
    expect(screen.getByText("词/秒")).toBeInTheDocument();

    rerender(<SpeechRateField value={null} onChange={() => {}} sourceLanguage="VI" />);
    expect(screen.getByText("词/秒")).toBeInTheDocument();
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
    // min / step 若比 isValidSpeechRate 严，同一个值会同时显示自定义有效与浏览器 :invalid
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

  it("rejects subnormal rates that overflow the duration estimate", () => {
    // 与后端 is_valid_speech_rate 同步：大于 0 但倒数为 Infinity 的语速不可提交
    expect(isValidSpeechRate(5e-324)).toBe(false);
    expect(isValidSpeechRate(1e-320)).toBe(false);
  });
});
