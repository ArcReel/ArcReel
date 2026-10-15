import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { FetchedProviderModel, FetchProviderModelsResponse } from "@/types";
import { FetchModelsDialog, type FetchSelection } from "./FetchModelsDialog";

function fetched(overrides: Partial<FetchedProviderModel> & Pick<FetchedProviderModel, "model_id">): FetchedProviderModel {
  return {
    display_name: overrides.model_id,
    media_type: "text",
    recognized: false,
    model_key: null,
    matched_entry: null,
    endpoint: null,
    endpoint_by_media_type: null,
    is_default: false,
    ...overrides,
  };
}

const RESPONSE: FetchProviderModelsResponse = {
  not_applicable: false,
  reason: null,
  models: [
    fetched({
      model_id: "gpt-5.5",
      recognized: true,
      matched_entry: { maker: "openai", name: "gpt-5.5", media_type: "text", display_name: "GPT-5.5", priced: true },
    }),
    fetched({ model_id: "already-there", recognized: true }),
    fetched({ model_id: "relay-mystery" }),
  ],
};

function renderDialog(load: (signal: AbortSignal) => Promise<FetchProviderModelsResponse>) {
  const onAdd = vi.fn(async (selections: FetchSelection[]) => selections.map(() => null));
  const onOpenChange = vi.fn();
  render(
    <FetchModelsDialog
      open
      onOpenChange={onOpenChange}
      providerName="中转站"
      load={load}
      isAdded={(m) => m.model_id === "already-there"}
      onAdd={onAdd}
    />,
  );
  return { onAdd, onOpenChange };
}

describe("FetchModelsDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("lists only models not added yet, with recognized ones checked and unrecognized ones unchecked", async () => {
    renderDialog(async () => RESPONSE);

    const recognized = await screen.findByRole("region", { name: /已识别/ });
    const unrecognized = screen.getByRole("region", { name: /未识别/ });
    expect(within(recognized).getByRole("checkbox", { name: /gpt-5\.5/ })).toBeChecked();
    expect(within(unrecognized).getByRole("checkbox", { name: /relay-mystery/ })).not.toBeChecked();
    expect(within(unrecognized).getByText("没有这些模型的价格，调用费用会记为未定价。可以为它们选择对应模型。")).toBeInTheDocument();
    expect(screen.queryByText("already-there")).not.toBeInTheDocument();
    expect(screen.getByText("另有 1 个已添加的模型未列出。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加 1 个模型" })).toBeEnabled();
  });

  it("submits the matching model chosen for an unrecognized row as its binding", async () => {
    const user = userEvent.setup();
    vi.spyOn(API, "searchCatalogEntries").mockResolvedValue({
      entries: [{ maker: "openai", name: "gpt-image-2", media_type: "image", display_name: "GPT Image 2", discontinued: false }],
    });
    const { onAdd, onOpenChange } = renderDialog(async () => RESPONSE);

    await user.click(await screen.findByRole("button", { name: "为 relay-mystery 选择对应模型" }));
    await user.click(await screen.findByRole("option", { name: /GPT Image 2/ }));

    // 选了对应模型的行随之勾选，媒体类型取对应模型的
    expect(screen.getByRole("checkbox", { name: /relay-mystery/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "添加 2 个模型" }));

    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    const [selections] = onAdd.mock.calls[0];
    const mystery = selections.find((s) => s.model.model_id === "relay-mystery");
    expect(mystery?.binding).toMatchObject({ maker: "openai", name: "gpt-image-2" });
    expect(mystery?.mediaType).toBe("image");
    expect(selections.find((s) => s.model.model_id === "gpt-5.5")?.binding).toBeNull();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("shows why fetching failed and fetches again on retry", async () => {
    const user = userEvent.setup();
    const load = vi
      .fn<(signal: AbortSignal) => Promise<FetchProviderModelsResponse>>()
      .mockRejectedValueOnce(new Error("401 invalid api key"))
      .mockResolvedValue(RESPONSE);
    renderDialog(load);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("获取模型列表失败。请检查接口地址与密钥后重试。");
    expect(alert).toHaveTextContent("401 invalid api key");

    await user.click(within(alert).getByRole("button", { name: "重试" }));

    expect(await screen.findByRole("checkbox", { name: /gpt-5\.5/ })).toBeChecked();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("keeps a row that failed to add and says why", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn(async (selections: FetchSelection[]) =>
      selections.map((s) => (s.model.model_id === "gpt-5.5" ? "模型「gpt-5.5」已在这个供应商的模型列表中" : null)),
    );
    render(
      <FetchModelsDialog
        open
        onOpenChange={vi.fn()}
        providerName="中转站"
        load={async () => RESPONSE}
        isAdded={() => false}
        onAdd={onAdd}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "添加 2 个模型" }));

    expect(await screen.findByText("添加失败：模型「gpt-5.5」已在这个供应商的模型列表中")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /gpt-5\.5/ })).toBeChecked();
  });
});
