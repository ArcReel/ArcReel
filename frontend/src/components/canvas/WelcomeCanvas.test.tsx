import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { WelcomeCanvas } from "@/components/canvas/WelcomeCanvas";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";

type WelcomeProps = Parameters<typeof WelcomeCanvas>[0];

/** 概览页持有上传对话框的开关状态，这里用同样的受控方式挂载。 */
function ControlledWelcome(props: Omit<WelcomeProps, "uploadFiles" | "onUploadFilesChange">) {
  const [uploadFiles, setUploadFiles] = useState<File[] | null>(null);
  return <WelcomeCanvas {...props} uploadFiles={uploadFiles} onUploadFilesChange={setUploadFiles} />;
}

function renderWelcome(props: Partial<Omit<WelcomeProps, "uploadFiles" | "onUploadFilesChange">> = {}) {
  return render(
    <ControlledWelcome
      projectName="p"
      wholeSourceFiles={[]}
      onAnalyze={props.onAnalyze ?? vi.fn().mockResolvedValue(undefined)}
      {...props}
    />,
  );
}

function dropOnZone(files: File[]) {
  const dropZone = screen.getByText("拖拽文件到此处").closest("button");
  expect(dropZone).not.toBeNull();
  fireEvent.drop(dropZone as HTMLElement, { dataTransfer: { files } });
}

describe("WelcomeCanvas", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("success");
  });

  it("shows the project title instead of the internal project name", () => {
    renderWelcome({ projectName: "halou-92d19a04", projectTitle: "哈喽项目" });

    expect(screen.getByText("欢迎来到 哈喽项目！")).toBeInTheDocument();
    expect(screen.queryByText("欢迎来到 halou-92d19a04！")).not.toBeInTheDocument();
  });

  it("opens the upload dialog with the dropped files", () => {
    renderWelcome();

    dropOnZone([new File(["x"], "novel.txt", { type: "text/plain" })]);

    expect(screen.getByRole("dialog", { name: "上传原文" })).toBeInTheDocument();
    expect(screen.getByTitle("novel.txt")).toBeInTheDocument();
  });

  it("starts the analysis after the first whole-source upload", async () => {
    vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "source/novel.txt", filename: "novel.txt" });
    const onAnalyze = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onAnalyze });

    dropOnZone([new File(["x"], "novel.txt", { type: "text/plain" })]);
    fireEvent.click(screen.getByRole("button", { name: "上传 1 个文件" }));

    await waitFor(() => expect(onAnalyze).toHaveBeenCalledTimes(1));
  });

  it("only adds files when the project already has a whole source", async () => {
    const upload = vi
      .spyOn(API, "uploadFile")
      .mockResolvedValue({ success: true, path: "source/second.txt", filename: "second.txt" });
    const onAnalyze = vi.fn();
    renderWelcome({ onAnalyze, wholeSourceFiles: ["source/first.txt"] });

    expect(screen.getByText("first.txt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /添加更多文件/ }));
    fireEvent.change(screen.getByLabelText("选择文件"), {
      target: { files: [new File(["x"], "second.docx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "上传 1 个文件" }));

    await waitFor(() => expect(upload).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onAnalyze).not.toHaveBeenCalled();
  });
});
