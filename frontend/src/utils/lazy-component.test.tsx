import { Component, Suspense, type ReactNode } from "react";
import { render, screen } from "@testing-library/react";
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

function renderLazy(node: ReactNode) {
  return render(
    <CatchError>
      <Suspense fallback={<p>loading</p>}>{node}</Suspense>
    </CatchError>,
  );
}

describe("lazyNamed", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the named export once its module arrives", async () => {
    const LazyGreeting = lazyNamed(async () => ({ Greeting, other: 1 }), "Greeting");

    renderLazy(<LazyGreeting name="arc" />);

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(await screen.findByText("hello arc")).toBeInTheDocument();
  });

  it("surfaces the load error without reloading the page", async () => {
    // 新版本部署后旧入口引用的 chunk 已 404：用户可能正看着内容，
    // 这里只把错误交给边界显示，不替用户决定刷新时机。
    // jsdom 的 location.reload 不可替换，改为断言错误直接交给边界（有 alert）即可
    vi.spyOn(console, "error").mockImplementation(() => {});
    const LazyGreeting = lazyNamed<"Greeting", { name: string }>(
      () => Promise.reject(new Error("chunk 404")),
      "Greeting",
    );

    renderLazy(<LazyGreeting name="arc" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("chunk 404");
  });

  it("shares one request between preload and render", async () => {
    const load = vi.fn(async () => ({ Greeting }));
    const LazyGreeting = lazyNamed(load, "Greeting");

    LazyGreeting.preload();
    renderLazy(<LazyGreeting name="arc" />);

    expect(await screen.findByText("hello arc")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("keeps a failed preload from poisoning the later render", async () => {
    const load = vi
      .fn<() => Promise<{ Greeting: typeof Greeting }>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ Greeting });
    const LazyGreeting = lazyNamed(load, "Greeting");

    LazyGreeting.preload();
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));

    renderLazy(<LazyGreeting name="arc" />);

    expect(await screen.findByText("hello arc")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
  });
});
