import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import "@/i18n";
import type { MediaType } from "@/types/custom-provider";
import { PriceEditor } from "./PriceEditor";
import { isPriceTableValid, priceForSave, type PriceTable } from "./priceTable";

function Harness({
  mediaType,
  initial = { currency: "USD", meters: [] },
  official = null,
  resolutions,
}: {
  mediaType: MediaType;
  initial?: PriceTable;
  official?: PriceTable | null;
  resolutions?: string[];
}) {
  const [value, setValue] = useState<PriceTable>(initial);
  return (
    <>
      <PriceEditor value={value} onChange={setValue} mediaType={mediaType} official={official} resolutions={resolutions} />
      <output data-testid="saved">{JSON.stringify(priceForSave(value))}</output>
      <output data-testid="valid">{String(isPriceTableValid(value))}</output>
    </>
  );
}

const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as unknown;

async function pickMenuItem(trigger: string | RegExp, item: string) {
  fireEvent.click(screen.getByRole("button", { name: trigger }));
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

function card(name: string) {
  return screen.getByRole("region", { name });
}

describe("PriceEditor", () => {
  it("says an empty price table is unpriced", () => {
    render(<Harness mediaType="video" />);

    expect(screen.getByText("不填价格时，调用费用记为未定价。")).toBeInTheDocument();
    expect(saved()).toBeNull();
  });

  it("builds a conditional meter with a free allowance into the price table structure", async () => {
    render(<Harness mediaType="video" resolutions={["720p", "1080p"]} />);

    await pickMenuItem("添加计费项", "视频时长");
    await pickMenuItem(/按条件分价/, "分辨率");
    fireEvent.change(screen.getByRole("textbox", { name: "视频时长的单价（分辨率：720p）" }), { target: { value: "0.4" } });
    fireEvent.click(within(card("视频时长")).getByRole("button", { name: "添加一档" }));
    // 新的一档沿用上一档的条件，点选条件标签改成 1080p
    const tags = within(card("视频时长")).getAllByRole("button", { name: /修改条件：分辨率：720p/ });
    fireEvent.click(tags[1]);
    fireEvent.click(screen.getByRole("button", { name: "1080p" }));
    fireEvent.change(screen.getByRole("textbox", { name: "视频时长的单价（分辨率：1080p）" }), { target: { value: "0.8" } });

    await pickMenuItem("添加计费项", "参考图");
    fireEvent.click(within(card("参考图")).getByRole("button", { name: "添加免费额度" }));
    fireEvent.change(screen.getByRole("textbox", { name: "参考图 · 每次请求的免费额度" }), { target: { value: "2" } });
    fireEvent.change(screen.getByRole("textbox", { name: "参考图的单价" }), { target: { value: "0.05" } });

    expect(saved()).toEqual({
      currency: "USD",
      meters: [
        {
          meter: "output_seconds",
          dims: ["resolution"],
          rows: [
            { when: { resolution: "720p" }, price: 0.4 },
            { when: { resolution: "1080p" }, price: 0.8 },
          ],
          defaults: { resolution: "720p" },
        },
        { meter: "input_images", dims: [], rows: [{ when: {}, price: 0.05 }], free_allowance: 2 },
      ],
    });
    expect(screen.getByTestId("valid")).toHaveTextContent("true");

    fireEvent.click(screen.getByRole("button", { name: "删除参考图的免费额度" }));
    expect(JSON.stringify(saved())).not.toContain("free_allowance");
  });

  it("lists only the meters and conditions available for the media type", async () => {
    const { unmount } = render(<Harness mediaType="audio" />);

    fireEvent.click(screen.getByRole("button", { name: "添加计费项" }));
    const audioMeters = (await screen.findAllByRole("menuitem")).map((item) => item.textContent);
    expect(audioMeters).toEqual(["输入", "文字", "输出（音频）", "调用次数"]);
    unmount();

    render(<Harness mediaType="video" />);
    await pickMenuItem("添加计费项", "视频时长");
    fireEvent.click(screen.getByRole("button", { name: "按条件分价" }));
    const videoDims = (await screen.findAllByRole("menuitem")).map((item) => item.textContent);
    expect(videoDims).toEqual(["分辨率", "声音", "服务档", "时长", "输入视频", "参考生视频"]);
  });

  it("keeps a condition once it has been added", async () => {
    render(<Harness mediaType="video" />);
    await pickMenuItem("添加计费项", "视频时长");
    await pickMenuItem("按条件分价", "声音");

    const meter = card("视频时长");
    expect(within(meter).queryByRole("button", { name: /(移除|删除)条件/ })).not.toBeInTheDocument();
    expect(within(meter).getByText(/加上的条件不能移除/)).toBeInTheDocument();
    // 已经加过的条件不再出现在选项里
    fireEvent.click(within(meter).getByRole("button", { name: "按条件分价" }));
    expect((await screen.findAllByRole("menuitem")).map((item) => item.textContent)).not.toContain("声音");
  });

  it("switches the currency and hides the official comparison when it differs", () => {
    const official: PriceTable = {
      currency: "USD",
      meters: [{ meter: "output_seconds", dims: [], rows: [{ when: {}, price: 0.5 }] }],
    };
    render(<Harness mediaType="video" official={official} initial={{ currency: "USD", meters: [] }} />);

    expect(screen.getByText(/官方价格：/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "人民币" }));
    expect(screen.queryByText(/官方价格：/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "人民币" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "美元" }));
    fireEvent.click(screen.getByRole("button", { name: "采用官方价格" }));
    expect(saved()).toEqual(official);
  });

  it("flags rows with repeated conditions and unfilled prices as not savable", async () => {
    render(<Harness mediaType="text" />);
    await pickMenuItem("添加计费项", "输入");
    await pickMenuItem("按条件分价", "服务档");
    fireEvent.click(within(card("输入")).getByRole("button", { name: "添加一档" }));

    // 新的一档自动取了下一个服务档，两档都还没填单价
    expect(screen.getAllByText("请填写单价。")).toHaveLength(2);
    expect(screen.getByTestId("valid")).toHaveTextContent("false");

    fireEvent.click(within(card("输入")).getAllByRole("button", { name: /修改条件：服务档：/ })[1]);
    fireEvent.click(screen.getByRole("button", { name: "标准" }));
    expect(screen.getByText("与前面某一档的条件重复，请修改条件。")).toBeInTheDocument();
  });

  it("flags context tiers that do not start at 0 or differ between meters as not savable", () => {
    const tiered = (meter: string, tiers: number[]) => ({
      meter,
      dims: ["context_tier"],
      rows: tiers.map((tier) => ({ when: { context_tier: tier }, price: 1 })),
    });
    const { unmount } = render(
      <Harness
        mediaType="text"
        initial={{ currency: "USD", meters: [tiered("uncached_input_tokens", [100_000, 200_000])] }}
      />,
    );
    expect(within(card("输入")).getByText("输入长度的第一档要从 0 起。")).toBeInTheDocument();
    expect(screen.getByTestId("valid")).toHaveTextContent("false");
    unmount();

    render(
      <Harness
        mediaType="text"
        initial={{
          currency: "USD",
          meters: [tiered("uncached_input_tokens", [0, 200_000]), tiered("output_tokens", [0, 128_000])],
        }}
      />,
    );
    expect(within(card("输出")).getByText(/各计费项按输入长度分档的位置要相同/)).toBeInTheDocument();
    expect(within(card("输入")).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("valid")).toHaveTextContent("false");
  });

  it("offers the service tiers as standard, Flex and Fast", async () => {
    render(<Harness mediaType="text" />);
    await pickMenuItem("添加计费项", "输入");
    await pickMenuItem("按条件分价", "服务档");

    fireEvent.click(within(card("输入")).getAllByRole("button", { name: /修改条件：服务档：标准/ })[0]);
    const options = within(screen.getByRole("group", { name: "服务档" })).getAllByRole("button");
    expect(options.map((option) => option.textContent)).toEqual(["不限", "标准", "Flex", "Fast"]);
  });
});
