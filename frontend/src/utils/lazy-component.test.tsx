import { Component, Suspense, type ReactNode } from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lazyNamed } from "./lazy-component";

function Greeting({ name }: { name: string }) {
  return <p>hello {name}</p>;
}

class CatchError extends Component<{ children: ReactNode }, { message: string | null }> {
  state = { message: null as string | null };
  static getDerivedStateFromError(err: unknown) {
    return { message: err instanceof Error ? err.message : String(err) };
  }
  render() {
    return this.state.message === null ? this.props.children : <p role="alert">{this.state.message}</p>;
  }
}

/** Simulate genuine page-departing reloads through unprevented beforeunload events. */
function unloadingReload() {
  return vi.fn(() => {
    window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
  });
}

function renderLazy(node: ReactNode) {
  return render(
    <CatchError>
      <Suspense fallback={<p>loading</p>}>{node}</Suspense>
    </CatchError>,
  );
}

describe("lazyNamed", () => {
  afterEach(() => {
    vi.useRealTimers();
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders the named export once its module arrives", async () => {
    const LazyGreeting = lazyNamed(async () => ({ Greeting, other: 1 }), "Greeting");

    renderLazy(<LazyGreeting name="arc" />);

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(await screen.findByText("hello arc")).toBeInTheDocument();
  });

  it("reloads the page once and keeps the fallback while the page unloads", async () => {
    vi.useFakeTimers();
    // No leave guards: beforeunload unprevented, pages departing.
    const reload = unloadingReload();
    const load = vi.fn().mockRejectedValue(new Error("chunk 404"));
    const LazyGreeting = lazyNamed<"Greeting", { name: string }>(load, "Greeting", {
      reload,
      reloadBlockedAfterMs: 10,
    });

    renderLazy(<LazyGreeting name="arc" />);

    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    // Advance blocked-reload detection waits, confirming placeholders persist during departure.
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("surfaces the error and clears the mark when a leave prompt keeps the page", async () => {
    // Unsaved edits: leave guards preventDefault, users choose Stay in browser prompts.
    const keepPage = (event: Event) => event.preventDefault();
    window.addEventListener("beforeunload", keepPage);
    try {
      const reload = unloadingReload();
      const LazyGreeting = lazyNamed<"Greeting", { name: string }>(
        () => Promise.reject(new Error("chunk 404")),
        "Greeting",
        { reload, reloadBlockedAfterMs: 10 },
      );
      vi.spyOn(console, "error").mockImplementation(() => {});

      renderLazy(<LazyGreeting name="arc" />);

      expect(await screen.findByRole("alert")).toHaveTextContent("chunk 404");
      expect(reload).toHaveBeenCalledTimes(1);
      expect(sessionStorage.getItem("arcreel:lazy-chunk-reload")).toBeNull();
    } finally {
      window.removeEventListener("beforeunload", keepPage);
    }
  });

  it("surfaces the error instead of reloading again after a reload in the same session", async () => {
    const reload = unloadingReload();
    const FirstAttempt = lazyNamed<"Greeting", { name: string }>(
      () => Promise.reject(new Error("chunk 404")),
      "Greeting",
      { reload },
    );
    const { unmount } = renderLazy(<FirstAttempt name="arc" />);
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    unmount();

    // Reloaded new pages fail again within the same session.
    const LazyGreeting = lazyNamed<"Greeting", { name: string }>(
      () => Promise.reject(new Error("chunk still 404")),
      "Greeting",
      { reload },
    );
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderLazy(<LazyGreeting name="arc" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("chunk still 404");
    expect(reload).toHaveBeenCalledTimes(1);
  });

  /** Failed loads trigger reload/unmount, simulating refreshed pages. */
  async function failOnceAndReload(reload: () => void, name: "Greeting" | "Farewell" = "Greeting") {
    const Broken = lazyNamed<typeof name, { name: string }>(() => Promise.reject(new Error("chunk 404")), name, {
      reload,
    });
    const { unmount } = renderLazy(<Broken name="arc" />);
    await vi.waitFor(() => expect(reload).toHaveBeenCalled());
    unmount();
  }

  it("allows another reload once the chunk that failed loads successfully", async () => {
    const reload = unloadingReload();
    await failOnceAndReload(reload);

    const Ok = lazyNamed(async () => ({ Greeting }), "Greeting", { reload });
    const { unmount } = renderLazy(<Ok name="ok" />);
    expect(await screen.findByText("hello ok")).toBeInTheDocument();
    unmount();

    await failOnceAndReload(reload);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("keeps the reload guard when only a different chunk loads", async () => {
    // Other chunks such as onboarding load after refresh, but failed routes remain unavailable; never refresh again.
    const reload = unloadingReload();
    await failOnceAndReload(reload);

    const Farewell = ({ name }: { name: string }) => <p>bye {name}</p>;
    const Other = lazyNamed(async () => ({ Farewell }), "Farewell", { reload });
    const { unmount } = renderLazy(<Other name="ok" />);
    expect(await screen.findByText("bye ok")).toBeInTheDocument();
    unmount();

    const StillBroken = lazyNamed<"Greeting", { name: string }>(
      () => Promise.reject(new Error("chunk still 404")),
      "Greeting",
      { reload },
    );
    vi.spyOn(console, "error").mockImplementation(() => {});
    renderLazy(<StillBroken name="arc" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("chunk still 404");
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("shares one request between preload and render", async () => {
    const load = vi.fn(async () => ({ Greeting }));
    const LazyGreeting = lazyNamed(load, "Greeting");

    LazyGreeting.preload();
    renderLazy(<LazyGreeting name="arc" />);

    expect(await screen.findByText("hello arc")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
  });
});
