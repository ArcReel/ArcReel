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

describe("WelcomeCanvas auto-analyze on first upload", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });
  });

  it("triggers onAnalyze automatically after first upload from idle", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onAnalyze = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload, onAnalyze });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(["x"], "novel.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file));
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
