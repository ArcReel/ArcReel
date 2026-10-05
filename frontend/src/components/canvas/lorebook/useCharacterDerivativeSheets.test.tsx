import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCharacterDerivativeSheets } from "./useCharacterDerivativeSheets";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";

function Probe({ revision = "r1", enabled = true }: { revision?: string; enabled?: boolean }) {
  const { statuses } = useCharacterDerivativeSheets("demo", "阿岚", revision, enabled);
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

  it("does not ask while the character has no derivatives", () => {
    const spy = stubSheets(false);
    render(<Probe enabled={false} />);

    expect(spy).not.toHaveBeenCalled();
  });

  it("loads the statuses on mount and re-reads once the registration changes", async () => {
    const spy = stubSheets(true);
    const { rerender } = render(<Probe />);
    await waitFor(() => expect(screen.getByTestId("stale")).toHaveTextContent("true"));

    spy.mockResolvedValue({
      success: true,
      derivatives: { 战斗装: { description: "换上银色轻甲", character_sheet: "characters/derivatives/阿岚/战斗装.png", stale: false } },
    });
    rerender(<Probe revision="r2" />);

    await waitFor(() => expect(screen.getByTestId("stale")).toHaveTextContent("false"));
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("re-reads when a derivative generation finishes", async () => {
    const spy = stubSheets(true);
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "character_derivative", resource_id: "阿岚/战斗装" })],
      optimisticActive: new Set(),
    });
    render(<Probe />);
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
