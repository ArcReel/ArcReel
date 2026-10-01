import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Router } from "wouter";
import { API, ApiRequestError } from "@/api";
import { useAdScriptStore } from "@/stores/ad-script-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useWorkflowStore } from "@/stores/workflow-store";
import { makeContent, makePlan, makeStatus, makeTask } from "@/test/factories";
import type { ScriptOverwrite } from "@/types";
import type { WorkflowOperation } from "@/types/workflow";
import { AdScriptHost, AdScriptProgress } from "./AdScriptDialog";
import { NoScriptBlankState } from "./StartBlankScriptButton";

const ACCEPTED = { batch: { members: [] } } as unknown as Awaited<ReturnType<typeof API.generateAdScript>>;

function admitGenerateScript(operation: WorkflowOperation) {
  const status = makeStatus({
    content: makeContent({ ad_inputs: operation.state === "admitted" ? "present" : "absent" }),
    operations: { generate_script: operation },
  });
  useWorkflowStore.setState({ plan: makePlan({ status }), planKey: "proj::1" });
}

function renderBlankState() {
  return render(
    <Router>
      <NoScriptBlankState projectName="proj" episode={1} />
      <AdScriptHost projectName="proj" episode={1} />
    </Router>,
  );
}

beforeEach(() => {
  useTasksStore.getState().setTasks([]);
  useAdScriptStore.getState().close();
  useAssistantStore.getState().setInput("");
});

afterEach(() => {
  vi.restoreAllMocks();
  useWorkflowStore.getState().resetTarget();
});

describe("没有正式脚本时的广告空状态", () => {
  it("AI 生成脚本与从空白开始并排，提交后直接按整份生成入队", async () => {
    admitGenerateScript({ state: "admitted", reason: null });
    const submit = vi.spyOn(API, "generateAdScript").mockResolvedValue(ACCEPTED);
    renderBlankState();

    expect(screen.getByRole("button", { name: "从空白开始" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "AI 生成脚本" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "节奏更快" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "AI 生成脚本" }));

    await waitFor(() =>
      expect(submit).toHaveBeenCalledWith("proj", 1, {
        instructions: "节奏更快",
        regenerate: false,
        overwrite_revision: null,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("缺创作灵感与商品时 AI 生成脚本置灰，说明原因并给出去填写的链接", () => {
    admitGenerateScript({ state: "refused", reason: "ad_brief_and_products_missing" });
    renderBlankState();

    const entry = screen.getByRole("button", { name: "AI 生成脚本" });
    expect(entry).toBeDisabled();
    expect(entry).toHaveAttribute("title", "请先填写创作灵感或添加商品");
    expect(screen.getByText(/请先填写创作灵感或添加商品/, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去填写" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("button", { name: "从空白开始" })).toBeEnabled();
  });
});

describe("重新生成脚本", () => {
  const overwrite: ScriptOverwrite = {
    revision: "rev-1",
    entries: [],
    storyboard_count: 2,
    video_count: 1,
    narration_audio_count: 0,
    end_frame_count: 0,
    grid_member_count: 0,
    grid_count: 0,
    text: "将移除 E1S01、E1S02，以及 2 张分镜图、1 个视频。",
  };

  it("先列出将被移除的条目与产物，确认后带着清单版本重新提交", async () => {
    const submit = vi
      .spyOn(API, "generateAdScript")
      .mockRejectedValueOnce(new ApiRequestError("需要确认覆盖", { script_overwrite: overwrite }, 409))
      .mockResolvedValueOnce(ACCEPTED);
    useAdScriptStore.getState().open({ projectName: "proj", episode: 1, regenerate: true });
    render(<AdScriptHost projectName="proj" episode={1} />);

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "重新生成脚本" }));

    expect(await screen.findByText("替换本集的正式脚本？")).toBeInTheDocument();
    expect(screen.getByText(/2 张分镜图、1 个视频/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "替换并重新生成" }));

    await waitFor(() =>
      expect(submit).toHaveBeenLastCalledWith("proj", 1, {
        instructions: null,
        regenerate: true,
        overwrite_revision: "rev-1",
      }),
    );
    expect(submit).toHaveBeenNthCalledWith(1, "proj", 1, {
      instructions: null,
      regenerate: true,
      overwrite_revision: null,
    });
  });

  it("交给 Agent 预填整份重做的请求与附加指令", async () => {
    useAdScriptStore.getState().open({ projectName: "proj", episode: 1, regenerate: true });
    render(<AdScriptHost projectName="proj" episode={1} />);

    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "换成夜景" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "交给 Agent" }));

    const input = useAssistantStore.getState().input;
    expect(input).toContain("整份重新生成");
    expect(input).toContain("换成夜景");
  });
});

describe("有正式脚本时的生成结果", () => {
  it("整份生成完成后列出本次登记的待生成资产", () => {
    useTasksStore.getState().setTasks([
      makeTask({
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "succeeded",
        result: {
          message: "已生成",
          new_assets: [
            { type: "character", name: "阿杰" },
            { type: "scene", name: "街角" },
          ],
        },
      }),
    ]);
    render(<AdScriptProgress projectName="proj" episode={1} noScript={false} />);

    expect(screen.getByRole("status")).toHaveTextContent("登记了 2 项待生成的资产：阿杰、街角");
    fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("在跑的提示词编写任务不当作整份生成呈现", () => {
    useTasksStore.getState().setTasks([
      makeTask({
        task_type: "text_episode_script",
        resource_id: "episode-1",
        status: "running",
        payload: { entry_ids: ["E1S01"] },
      }),
    ]);
    const { container } = render(<AdScriptProgress projectName="proj" episode={1} noScript={false} />);

    expect(container).toBeEmptyDOMElement();
  });
});
