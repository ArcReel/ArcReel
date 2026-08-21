import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";
import { WelcomeCanvas } from "@/components/canvas/WelcomeCanvas";
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";

describe("WelcomeCanvas", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows the project title instead of the internal project name", async () => {
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });

    render(
      <WelcomeCanvas
        projectName="halou-92d19a04"
        projectTitle="哈喽项目"
      />,
    );

    expect(await screen.findByText("欢迎来到 哈喽项目！")).toBeInTheDocument();
    expect(screen.queryByText("欢迎来到 halou-92d19a04！")).not.toBeInTheDocument();
  });
});

function renderWelcome(props: Partial<Parameters<typeof WelcomeCanvas>[0]>) {
  return render(
    <I18nextProvider i18n={i18n}>
      <WelcomeCanvas
        projectName="p"
        onUpload={props.onUpload ?? vi.fn().mockResolvedValue(undefined)}
        onAnalyze={props.onAnalyze ?? vi.fn().mockResolvedValue(undefined)}
        {...props}
      />
    </I18nextProvider>,
  );
}

describe("WelcomeCanvas manual analysis", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });
  });

  it("waits for an explicit click after the first upload", async () => {
    vi.mocked(API.listFiles)
      .mockResolvedValueOnce({ files: { source: [] } })
      .mockResolvedValue({
        files: { source: [{ name: "novel.txt", size: 1, url: "/novel" }] },
      });
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onAnalyze = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload, onAnalyze });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(["x"], "novel.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file));
    expect(onAnalyze).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: "开始 AI 分析" }));
    await waitFor(() => expect(onAnalyze).toHaveBeenCalledTimes(1));
  });

  it("does NOT auto-trigger analyze when uploading from has_sources", async () => {
    vi.spyOn(API, "listFiles").mockResolvedValue({
      files: { source: [{ name: "existing.txt", size: 10, url: "/x" }] },
    });
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onAnalyze = vi.fn();
    renderWelcome({ onUpload, onAnalyze });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(["x"], "second.docx");
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalled());
    expect(onAnalyze).not.toHaveBeenCalled();
  });
});

describe("WelcomeCanvas source deletion", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles")
      .mockResolvedValueOnce({
        files: {
          source: [
            { name: "first.txt", size: 10, url: "/first" },
            { name: "second.txt", size: 20, url: "/second" },
          ],
        },
      })
      .mockResolvedValue({
        files: { source: [{ name: "second.txt", size: 20, url: "/second" }] },
      });
  });

  it("deletes a source file from the remove button at the end of its row", async () => {
    const deleteSpy = vi.spyOn(API, "deleteSourceFile").mockResolvedValue({ success: true });
    renderWelcome({});

    fireEvent.click(await screen.findByRole("button", { name: "删除源文件 first.txt" }));

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith("p", "first.txt"));
    await waitFor(() => expect(screen.queryByText("first.txt")).not.toBeInTheDocument());
    expect(screen.getByText("second.txt")).toBeInTheDocument();
  });
});

describe("WelcomeCanvas accept extension", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });
  });

  it("accepts .docx, .epub, .pdf in input accept attribute", async () => {
    renderWelcome({});
    const input = (await screen.findByLabelText(/upload|上传/i)) as HTMLInputElement;
    expect(input.accept).toContain(".docx");
    expect(input.accept).toContain(".epub");
    expect(input.accept).toContain(".pdf");
  });
});

describe("WelcomeCanvas unsupported file validation", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });
  });

  it("shows an error and does not upload when an unsupported file is dropped", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload });

    const dropZone = (await screen.findByText("拖拽文件到此处")).closest("button");
    expect(dropZone).not.toBeNull();
    const file = new File(["x"], "cover.png", { type: "image/png" });
    fireEvent.drop(dropZone as HTMLElement, { dataTransfer: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("不支持的文件类型：cover.png");
    expect(onUpload).not.toHaveBeenCalled();
  });

  it("shows an error and does not upload when an unsupported file is picked", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(["x"], "cover.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("不支持的文件类型：cover.png");
    expect(onUpload).not.toHaveBeenCalled();
  });

  it("uploads when a supported file is dropped", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload });

    const dropZone = (await screen.findByText("拖拽文件到此处")).closest("button");
    const file = new File(["x"], "novel.txt", { type: "text/plain" });
    fireEvent.drop(dropZone as HTMLElement, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file));
  });
});
