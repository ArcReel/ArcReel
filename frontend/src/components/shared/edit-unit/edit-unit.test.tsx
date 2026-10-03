import { useState, type ReactNode } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, Route, Router, Switch } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { useAppStore } from "@/stores/app-store";
import { createDeferred } from "@/test/deferred";

import { LeaveGuardProvider, useConfirmLeave } from "./LeaveGuard";
import { SaveBar } from "./SaveBar";
import { UnsavedChangesBar } from "./UnsavedChangesBar";
import { useEditUnit, type SaveAndGenerateOptions } from "./useEditUnit";

type SaveNote = (value: string) => Promise<string | void>;

interface NoteEditorProps {
  source: string;
  save: SaveNote;
  inline?: boolean;
  generate?: () => void;
  confirm?: SaveAndGenerateOptions["confirm"];
}

function NoteEditor({ source, save, inline = false, generate, confirm }: NoteEditorProps) {
  const unit = useEditUnit({ source, save });
  return (
    <>
      <label>
        备注
        <textarea value={unit.value} onChange={(event) => unit.setValue(event.target.value)} />
      </label>
      {inline ? <UnsavedChangesBar unit={unit} /> : <SaveBar unit={unit} />}
      {generate ? (
        <button type="button" onClick={() => void unit.saveAndGenerate(generate, { confirm })}>
          {unit.dirty ? "保存并生成" : "生成"}
        </button>
      ) : null}
    </>
  );
}

const note = () => screen.getByRole("textbox", { name: "备注" });
const leaveDialog = () => screen.findByRole("alertdialog", { name: "有未保存的修改" });

/** 备注页与另一个页面之间的应用内跳转。 */
function renderNotePage(editor: ReactNode) {
  const location = memoryLocation({ path: "/notes", record: true });
  render(
    <Router hook={location.hook}>
      <LeaveGuardProvider>
        <Link href="/elsewhere">去别处</Link>
        <Switch>
          <Route path="/notes">{editor}</Route>
          <Route path="/elsewhere">别处的页面</Route>
        </Switch>
      </LeaveGuardProvider>
    </Router>,
  );
  return location;
}

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
});

describe("保存栏", () => {
  it("放弃修改恢复已保存的内容，保存栏回到置灰", async () => {
    const user = userEvent.setup();
    render(<NoteEditor source="原始备注" save={vi.fn<SaveNote>()} />);

    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    await user.type(note(), "，补充");
    expect(screen.getByText("有未保存的修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "放弃修改" }));

    expect(note()).toHaveValue("原始备注");
    expect(screen.queryByText("有未保存的修改")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "放弃修改" })).toBeDisabled();
  });

  it("保存成功时提交修改并在栏内显示「已保存」，不弹提示", async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveNote>().mockResolvedValue(undefined);
    render(<NoteEditor source="原始备注" save={save} />);

    await user.type(note(), "，补充");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(save).toHaveBeenCalledWith("原始备注，补充", "原始备注");
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("保存失败时在栏内显示错误并保留修改", async () => {
    const user = userEvent.setup();
    render(<NoteEditor source="原始备注" save={vi.fn<SaveNote>().mockRejectedValue(new Error("备注过长"))} />);

    await user.type(note(), "，补充");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败：备注过长");
    expect(note()).toHaveValue("原始备注，补充");
    expect(screen.getByRole("button", { name: "保存" })).toBeEnabled();
  });
});

describe("离开拦截", () => {
  it("没有未保存修改时直接跳转", async () => {
    const user = userEvent.setup();
    const location = renderNotePage(<NoteEditor source="原始备注" save={vi.fn<SaveNote>()} />);

    await user.click(screen.getByRole("link", { name: "去别处" }));

    expect(location.history.at(-1)).toBe("/elsewhere");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("继续编辑：留在原处，修改保留", async () => {
    const user = userEvent.setup();
    const location = renderNotePage(<NoteEditor source="原始备注" save={vi.fn<SaveNote>()} />);
    await user.type(note(), "，补充");

    await user.click(screen.getByRole("link", { name: "去别处" }));
    await user.click(within(await leaveDialog()).getByRole("button", { name: "继续编辑" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(location.history.at(-1)).toBe("/notes");
    expect(note()).toHaveValue("原始备注，补充");
  });

  it("放弃修改：不保存，直接离开", async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveNote>();
    const location = renderNotePage(<NoteEditor source="原始备注" save={save} />);
    await user.type(note(), "，补充");

    await user.click(screen.getByRole("link", { name: "去别处" }));
    await user.click(within(await leaveDialog()).getByRole("button", { name: "放弃修改" }));

    expect(await screen.findByText("别处的页面")).toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/elsewhere");
    expect(save).not.toHaveBeenCalled();
  });

  it("保存并离开：保存成功后离开", async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveNote>().mockResolvedValue(undefined);
    const location = renderNotePage(<NoteEditor source="原始备注" save={save} />);
    await user.type(note(), "，补充");

    await user.click(screen.getByRole("link", { name: "去别处" }));
    await user.click(within(await leaveDialog()).getByRole("button", { name: "保存并离开" }));

    expect(await screen.findByText("别处的页面")).toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/elsewhere");
    expect(save).toHaveBeenCalledWith("原始备注，补充", "原始备注");
  });

  it("保存并离开：保存失败时留在原处，修改与错误保留", async () => {
    const user = userEvent.setup();
    const location = renderNotePage(
      <NoteEditor source="原始备注" save={vi.fn<SaveNote>().mockRejectedValue(new Error("备注过长"))} />,
    );
    await user.type(note(), "，补充");

    await user.click(screen.getByRole("link", { name: "去别处" }));
    await user.click(within(await leaveDialog()).getByRole("button", { name: "保存并离开" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败：备注过长");
    expect(location.history.at(-1)).toBe("/notes");
    expect(note()).toHaveValue("原始备注，补充");
  });

  it("主从布局切换选中项时同样拦截，第三个按钮文案可以覆盖", async () => {
    const save = vi.fn<SaveNote>().mockResolvedValue(undefined);

    function NoteList() {
      const [selected, setSelected] = useState("备注一");
      const confirmLeave = useConfirmLeave();
      return (
        <>
          <button type="button" onClick={() => confirmLeave(() => setSelected("备注二"), { saveLabel: "保存并切换" })}>
            打开备注二
          </button>
          <p>当前：{selected}</p>
          <NoteEditor key={selected} source={`${selected}的内容`} save={save} />
        </>
      );
    }

    const user = userEvent.setup();
    renderNotePage(<NoteList />);
    await user.type(note(), "，补充");

    await user.click(screen.getByRole("button", { name: "打开备注二" }));
    await user.click(within(await leaveDialog()).getByRole("button", { name: "保存并切换" }));

    expect(await screen.findByText("当前：备注二")).toBeInTheDocument();
    expect(save).toHaveBeenCalledWith("备注一的内容，补充", "备注一的内容");
    expect(note()).toHaveValue("备注二的内容");
  });

  describe("关闭标签页或刷新", () => {
    const unload = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event;
    };

    it("有未保存修改时请求浏览器原生提示，放弃后不再提示", async () => {
      const user = userEvent.setup();
      renderNotePage(<NoteEditor source="原始备注" save={vi.fn<SaveNote>()} />);

      expect(unload().defaultPrevented).toBe(false);
      await user.type(note(), "，补充");
      expect(unload().defaultPrevented).toBe(true);

      await user.click(screen.getByRole("button", { name: "放弃修改" }));
      expect(unload().defaultPrevented).toBe(false);
    });
  });

  describe("浏览器前进后退", () => {
    afterEach(() => {
      window.history.replaceState(null, "", "/");
    });

    /** 用浏览器地址渲染，模拟从 /elsewhere 进入备注页。 */
    function renderInBrowser() {
      window.history.replaceState(null, "", "/elsewhere");
      window.history.pushState(null, "", "/notes");
      render(
        <LeaveGuardProvider>
          <Switch>
            <Route path="/notes">
              <NoteEditor source="原始备注" save={vi.fn<SaveNote>()} />
            </Route>
            <Route path="/elsewhere">别处的页面</Route>
          </Switch>
        </LeaveGuardProvider>,
      );
    }

    // jsdom 异步派发 popstate；拦截时它被守卫截停，只能等对话框出现
    const goBack = () => act(() => window.history.back());

    it("有未保存修改时后退先拦截，继续编辑则回到原地址", async () => {
      const user = userEvent.setup();
      renderInBrowser();
      await user.type(note(), "，补充");

      await goBack();
      await user.click(within(await leaveDialog()).getByRole("button", { name: "继续编辑" }));

      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(window.location.pathname).toBe("/notes");
      expect(note()).toHaveValue("原始备注，补充");
    });

    it("放弃修改后前往后退的目标", async () => {
      const user = userEvent.setup();
      renderInBrowser();
      await user.type(note(), "，补充");

      await goBack();
      await user.click(within(await leaveDialog()).getByRole("button", { name: "放弃修改" }));

      expect(await screen.findByText("别处的页面")).toBeInTheDocument();
      expect(window.location.pathname).toBe("/elsewhere");
    });
  });
});

describe("保存并生成", () => {
  it("取消下游失效确认时什么都不保存、不生成", async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveNote>();
    const generate = vi.fn();
    render(<NoteEditor source="原始备注" save={save} generate={generate} confirm={() => false} />);

    await user.type(note(), "，补充");
    await user.click(screen.getByRole("button", { name: "保存并生成" }));

    expect(save).not.toHaveBeenCalled();
    expect(generate).not.toHaveBeenCalled();
    expect(note()).toHaveValue("原始备注，补充");
  });

  it("保存失败时不生成，修改与错误保留", async () => {
    const user = userEvent.setup();
    const generate = vi.fn();
    render(
      <NoteEditor
        source="原始备注"
        save={vi.fn<SaveNote>().mockRejectedValue(new Error("备注过长"))}
        generate={generate}
        confirm={() => true}
      />,
    );

    await user.type(note(), "，补充");
    await user.click(screen.getByRole("button", { name: "保存并生成" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败：备注过长");
    expect(generate).not.toHaveBeenCalled();
    expect(note()).toHaveValue("原始备注，补充");
  });

  it("确认后先保存修改，保存完成才提交生成", async () => {
    const user = userEvent.setup();
    const saving = createDeferred<string | void>();
    const save = vi.fn<SaveNote>().mockReturnValue(saving.promise);
    const generate = vi.fn();
    render(<NoteEditor source="原始备注" save={save} generate={generate} confirm={() => true} />);

    await user.type(note(), "，补充");
    await user.click(screen.getByRole("button", { name: "保存并生成" }));

    expect(save).toHaveBeenCalledWith("原始备注，补充", "原始备注");
    expect(generate).not.toHaveBeenCalled();
    await act(async () => saving.resolve(undefined));
    expect(generate).toHaveBeenCalledTimes(1);
  });
});

describe("外部更新", () => {
  it("没有未保存修改时直接采用新内容", () => {
    const save = vi.fn<SaveNote>();
    const { rerender } = render(<NoteEditor source="原始备注" save={save} inline />);

    rerender(<NoteEditor source="Agent 改写的备注" save={save} inline />);

    expect(note()).toHaveValue("Agent 改写的备注");
    expect(screen.queryByText("此内容已被 Agent 更新")).not.toBeInTheDocument();
  });

  it("有未保存修改时保留修改并提示，可以采用新内容", async () => {
    const user = userEvent.setup();
    const save = vi.fn<SaveNote>();
    const { rerender } = render(<NoteEditor source="原始备注" save={save} inline />);
    await user.type(note(), "，补充");

    rerender(<NoteEditor source="Agent 改写的备注" save={save} inline />);

    expect(note()).toHaveValue("原始备注，补充");
    expect(screen.getByText("此内容已被 Agent 更新")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "采用新内容" }));

    expect(note()).toHaveValue("Agent 改写的备注");
    expect(screen.queryByText("此内容已被 Agent 更新")).not.toBeInTheDocument();
  });
});
