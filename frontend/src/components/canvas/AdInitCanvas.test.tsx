import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { AdInitCanvas } from "./AdInitCanvas";
import { useAppStore } from "@/stores/app-store";

function makeFile(name: string): File {
  return new File(["img"], name, { type: "image/jpeg" });
}

describe("AdInitCanvas", () => {
  const onDone = vi.fn();

  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    onDone.mockReset();
  });

  it("renders product upload, description, brief and sheet checkbox", () => {
    render(<AdInitCanvas projectName="ad-demo" onDone={onDone} />);
    expect(screen.getByLabelText("产品名称")).toBeInTheDocument();
    expect(screen.getByLabelText("产品图")).toBeInTheDocument();
    expect(screen.getByLabelText("产品描述")).toBeInTheDocument();
    expect(screen.getByLabelText("创作 Brief")).toBeInTheDocument();
    expect(screen.getByLabelText("生成标准产品参考图")).toBeInTheDocument();
  });

  it("creates product, uploads images, saves brief and enqueues sheet generation", async () => {
    vi.spyOn(API, "addProjectProduct").mockResolvedValue({ success: true } as never);
    vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "p", url: "u" } as never);
    vi.spyOn(API, "updateProject").mockResolvedValue({ success: true } as never);
    vi.spyOn(API, "generateProjectProduct").mockResolvedValue({
      success: true,
      task_id: "t1",
      message: "ok",
    });

    render(<AdInitCanvas projectName="ad-demo" onDone={onDone} />);

    fireEvent.change(screen.getByLabelText("产品名称"), { target: { value: "保温杯" } });
    fireEvent.change(screen.getByLabelText("产品描述"), { target: { value: "不锈钢保温杯" } });
    fireEvent.change(screen.getByLabelText("创作 Brief"), { target: { value: "突出保温 12 小时" } });
    fireEvent.change(screen.getByLabelText("产品图"), {
      target: { files: [makeFile("front.jpg"), makeFile("back.jpg")] },
    });
    fireEvent.click(screen.getByLabelText("生成标准产品参考图"));

    fireEvent.click(screen.getByRole("button", { name: "开始创作" }));

    await waitFor(() => {
      expect(onDone).toHaveBeenCalled();
    });
    expect(API.addProjectProduct).toHaveBeenCalledWith("ad-demo", "保温杯", "不锈钢保温杯");
    expect(API.uploadFile).toHaveBeenCalledTimes(2);
    expect(API.uploadFile).toHaveBeenCalledWith(
      "ad-demo",
      "product_ref",
      expect.anything(),
      "保温杯",
    );
    expect(API.updateProject).toHaveBeenCalledWith("ad-demo", { brief: "突出保温 12 小时" });
    expect(API.generateProjectProduct).toHaveBeenCalledWith("ad-demo", "保温杯", "不锈钢保温杯");
  });

  it("supports brief-only flow without products", async () => {
    const addSpy = vi.spyOn(API, "addProjectProduct");
    vi.spyOn(API, "updateProject").mockResolvedValue({ success: true } as never);

    render(<AdInitCanvas projectName="ad-demo" onDone={onDone} />);

    fireEvent.change(screen.getByLabelText("创作 Brief"), { target: { value: "通用短片" } });
    fireEvent.click(screen.getByRole("button", { name: "开始创作" }));

    await waitFor(() => {
      expect(onDone).toHaveBeenCalled();
    });
    expect(addSpy).not.toHaveBeenCalled();
    expect(API.updateProject).toHaveBeenCalledWith("ad-demo", { brief: "通用短片" });
  });

  it("disables submit until brief or complete product info is provided", () => {
    render(<AdInitCanvas projectName="ad-demo" onDone={onDone} />);
    const submit = screen.getByRole("button", { name: "开始创作" });
    expect(submit).toBeDisabled();

    // 只有名称没有描述仍不可提交
    fireEvent.change(screen.getByLabelText("产品名称"), { target: { value: "保温杯" } });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("产品描述"), { target: { value: "不锈钢" } });
    expect(submit).toBeEnabled();
  });

  it("blocks brief-only submit while the product section is partially filled", () => {
    render(<AdInitCanvas projectName="ad-demo" onDone={onDone} />);
    const submit = screen.getByRole("button", { name: "开始创作" });

    // brief 已填 + 产品区部分填写（只有名称）：不可提交并出现提示，防止静默丢弃产品信息
    fireEvent.change(screen.getByLabelText("创作 Brief"), { target: { value: "通用短片" } });
    expect(submit).toBeEnabled();
    fireEvent.change(screen.getByLabelText("产品名称"), { target: { value: "保温杯" } });
    expect(submit).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("产品信息不完整");

    // 只选了图片同样视为产品区已填写
    fireEvent.change(screen.getByLabelText("产品名称"), { target: { value: "" } });
    expect(submit).toBeEnabled();
    fireEvent.change(screen.getByLabelText("产品图"), {
      target: { files: [makeFile("front.jpg")] },
    });
    expect(submit).toBeDisabled();
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // 补全产品信息后恢复可提交，提示消失
    fireEvent.change(screen.getByLabelText("产品名称"), { target: { value: "保温杯" } });
    fireEvent.change(screen.getByLabelText("产品描述"), { target: { value: "不锈钢" } });
    expect(submit).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
