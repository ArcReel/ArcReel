import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WelcomeCanvas } from "@/components/canvas/WelcomeCanvas";
import { API } from "@/api";
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
        projectName="hello-92d19a04"
        projectTitle="Hello Project"
      />,
    );

    expect(await screen.findByText("Welcome to Hello Project!")).toBeInTheDocument();
    expect(screen.queryByText("Welcome to hello-92d19a04!")).not.toBeInTheDocument();
  });
});
