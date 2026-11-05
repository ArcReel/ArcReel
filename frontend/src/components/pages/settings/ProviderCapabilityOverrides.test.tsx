import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "@/i18n";
import type { CapabilityOverrides, ProviderModelCapabilities } from "@/types";
import { ProviderCapabilityOverrides } from "./ProviderCapabilityOverrides";

const VIDEO: ProviderModelCapabilities = {
  media_type: "video",
  editable: ["durations", "duration_constraints", "resolutions", "last_frame", "audio_track"],
  displayed: ["durations", "duration_constraints", "resolutions", "last_frame", "audio_track"],
  base: {
    durations: [4, 8],
    duration_constraints: null,
    resolutions: null,
    last_frame: false,
    audio_track: "controllable",
  },
  overrides: {},
  effective: {},
};

function Harness({
  capabilities = VIDEO,
  initial = {},
  onValidityChange = vi.fn(),
}: {
  capabilities?: ProviderModelCapabilities;
  initial?: CapabilityOverrides;
  onValidityChange?: (key: string, valid: boolean) => void;
}) {
  const [value, setValue] = useState<CapabilityOverrides>(initial);
  return (
    <>
      <ProviderCapabilityOverrides
        capabilities={capabilities}
        value={value}
        onChange={setValue}
        onValidityChange={onValidityChange}
      />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}

const current = () => JSON.parse(screen.getByTestId("value").textContent ?? "{}") as CapabilityOverrides;

function rowOf(label: string) {
  return screen.getByText(label, { selector: "span" }).closest("li")!;
}

describe("ProviderCapabilityOverrides", () => {
  it("shows the original value of each dimension, not recorded when neither side declares it", () => {
    render(<Harness />);

    expect(within(rowOf("可选时长")).getByText("4, 8 秒")).toBeInTheDocument();
    expect(within(rowOf("分辨率")).getByText("未收录")).toBeInTheDocument();
    expect(within(rowOf("尾帧")).getByText("不支持")).toBeInTheDocument();
    expect(within(rowOf("音轨形态")).getByText("可开关")).toBeInTheDocument();
  });

  it("shows the effective capabilities of an official model read-only, in the order the server lists them", () => {
    render(
      <Harness
        capabilities={{
          media_type: "image",
          editable: [],
          displayed: ["text_to_image", "image_to_image", "max_reference_images"],
          base: { max_reference_images: null, image_to_image: true, text_to_image: true },
          overrides: {},
          effective: { max_reference_images: null, image_to_image: true, text_to_image: true },
        }}
      />,
    );

    const rows = screen.getAllByRole("listitem").map((row) => row.textContent);
    expect(rows).toEqual(["文生图支持", "图生图支持", "参考图上限未收录"]);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows a dimension that cannot be changed read-only beside the editable ones", () => {
    render(
      <Harness
        capabilities={{
          media_type: "image",
          editable: ["image_to_image"],
          displayed: ["text_to_image", "image_to_image", "max_reference_images"],
          base: { text_to_image: false, image_to_image: true, max_reference_images: 4 },
          overrides: {},
          effective: { text_to_image: false, image_to_image: true, max_reference_images: 4 },
        }}
      />,
    );

    expect(within(rowOf("参考图上限")).getByText("4")).toBeInTheDocument();
    expect(within(rowOf("参考图上限")).queryByRole("button")).not.toBeInTheDocument();
    expect(within(rowOf("文生图")).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改「图生图」" })).toBeInTheDocument();
  });

  it("replaces a whole dimension when changed, and drops it again on restore", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "修改「可选时长」" }));
    const input = screen.getByRole("textbox", { name: "「可选时长」的值" });
    expect(input).toHaveValue("4, 8");
    fireEvent.change(input, { target: { value: "5-7, 10" } });
    expect(current()).toEqual({ durations: [5, 6, 7, 10] });

    fireEvent.click(screen.getByRole("button", { name: "恢复「可选时长」" }));
    expect(current()).toEqual({});
    expect(screen.queryByRole("textbox", { name: "「可选时长」的值" })).not.toBeInTheDocument();
  });

  it("reports an invalid entry so the save can be blocked", () => {
    const onValidityChange = vi.fn();
    render(<Harness onValidityChange={onValidityChange} />);

    fireEvent.click(screen.getByRole("button", { name: "修改「可选时长」" }));
    fireEvent.change(screen.getByRole("textbox", { name: "「可选时长」的值" }), { target: { value: "8-4" } });

    expect(onValidityChange).toHaveBeenLastCalledWith("durations", false);
    expect(screen.getByRole("textbox", { name: "「可选时长」的值" })).toHaveAttribute("aria-invalid", "true");
  });

  it("stops blocking the save once an invalid dimension is restored", () => {
    const onValidityChange = vi.fn();
    render(<Harness onValidityChange={onValidityChange} />);
    fireEvent.click(screen.getByRole("button", { name: "修改「可选时长」" }));
    fireEvent.change(screen.getByRole("textbox", { name: "「可选时长」的值" }), { target: { value: "8-4" } });
    expect(onValidityChange).toHaveBeenLastCalledWith("durations", false);

    fireEvent.click(screen.getByRole("button", { name: "恢复「可选时长」" }));

    expect(onValidityChange).toHaveBeenLastCalledWith("durations", true);
    expect(current()).toEqual({});
  });

  it("drops the duration constraints of a resolution once that resolution is removed", () => {
    render(
      <Harness
        initial={{
          resolutions: ["720p", "1080p"],
          duration_constraints: { by_resolution: { "1080p": [5], "720p": [5, 10] }, with_reference_images: [8] },
        }}
      />,
    );
    expect(screen.getByRole("textbox", { name: "1080p的可选时长" })).toHaveValue("5");

    fireEvent.change(screen.getByRole("textbox", { name: "「分辨率」的值" }), { target: { value: "720p" } });

    expect(current().duration_constraints).toEqual({ by_resolution: { "720p": [5, 10] }, with_reference_images: [8] });
    expect(screen.queryByRole("textbox", { name: "1080p的可选时长" })).not.toBeInTheDocument();
  });

  it("stops blocking the save once the resolution of an invalid duration row is removed", () => {
    const onValidityChange = vi.fn();
    render(
      <Harness
        onValidityChange={onValidityChange}
        initial={{ resolutions: ["720p", "1080p"], duration_constraints: { by_resolution: {}, with_reference_images: [] } }}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "1080p的可选时长" }), { target: { value: "8-4" } });
    expect(onValidityChange).toHaveBeenLastCalledWith("duration_constraints", false);

    fireEvent.change(screen.getByRole("textbox", { name: "「分辨率」的值" }), { target: { value: "720p" } });

    expect(onValidityChange).toHaveBeenLastCalledWith("duration_constraints", true);
  });

  it.each([
    ["image", true],
    ["video", false],
  ] as const)("explains the image reference limit only on %s models", (media, shown) => {
    const base = { max_reference_images: 3 };
    render(
      <Harness
        capabilities={{ media_type: media, editable: ["max_reference_images"], displayed: ["max_reference_images"],
          base, overrides: {}, effective: base }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "修改「参考图上限」" }));

    expect(screen.getByRole("textbox", { name: "「参考图上限」的值" })).toHaveValue("3");
    expect(screen.queryByText("0 表示不接受参考图；修改上限不会开启调用端点未实现的图生图能力") !== null).toBe(shown);
  });

  it("edits the audio track as a whole dimension", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "修改「音轨形态」" }));

    expect(current()).toEqual({ audio_track: "controllable" });
    expect(screen.getByRole("combobox", { name: "「音轨形态」的值" })).toBeInTheDocument();
  });
});
