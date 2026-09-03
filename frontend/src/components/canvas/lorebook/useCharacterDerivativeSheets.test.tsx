import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCharacterDerivativeSheets } from "./useCharacterDerivativeSheets";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";

function Probe({ enabled }: { enabled: boolean }) {
  const { statuses } = useCharacterDerivativeSheets("demo", "阿岚", enabled);
  return <span data-testid="stale">{String(statuses["战斗装"]?.stale ?? "none")}</span>;
}

function stubSheets(stale: boolean) {
  return vi.spyOn(API, "getCharacterDerivativeSheets").mockResolvedValue({
    success: true,
    derivatives: {
      战斗装: { description: "换上黑色重甲", character_sheet: "characters/derivatives/阿岚/战斗装.png", stale },
    },
  });
}

describe("useCharacterDerivativeSheets", () => {
  beforeEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    vi.restoreAllMocks();
  });

  it("stays quiet until the panel is open", () => {
    const spy = stubSheets(false);
    render(<Probe enabled={false} />);

    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByTestId("stale")).toHaveTextContent("none");
  });

  it("loads the statuses once the panel opens", async () => {
    stubSheets(true);
    render(<Probe enabled />);

    await waitFor(() => expect(screen.getByTestId("stale")).toHaveTextContent("true"));
  });

  it("re-reads when a derivative generation finishes", async () => {
    const spy = stubSheets(true);
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "character_derivative", resource_id: "阿岚/战斗装" })],
      optimisticActive: new Set(),
    });
    render(<Probe enabled />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    // 任务离开占用集即代表刚有一次生成结束：重新取一次才能拿到新图与新的过期判定。
    spy.mockResolvedValue({
      success: true,
      derivatives: {
        战斗装: {
          description: "换上黑色重甲",
          character_sheet: "characters/derivatives/阿岚/战斗装.png",
          stale: false,
        },
      },
    });
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("stale")).toHaveTextContent("false"));
  });
});
