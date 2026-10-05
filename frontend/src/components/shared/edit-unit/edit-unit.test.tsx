import { useState, type ReactNode } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, Route, Router, Switch, useLocation } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { useAppStore } from "@/stores/app-store";
import { createDeferred } from "@/test/deferred";

import { LeaveGuardProvider, useConfirmLeave } from "./LeaveGuard";
import { SaveBar } from "./SaveBar";
import { UnsavedChangesBar } from "./UnsavedChangesBar";
import { PartialSaveError, useEditUnit, type SaveAndGenerateOptions } from "./useEditUnit";

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
        <Link href="/notes">当前页</Link>
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

  it("部分保存：已落盘的部分成为已保存内容，放弃修改回到它，再次保存以它为基准", async () => {
    const user = userEvent.setup();
    const save = vi
      .fn<SaveNote>()
      .mockRejectedValueOnce(new PartialSaveError("附件上传失败", { saved: "原始备注，补充" }))
      .mockResolvedValue(undefined);
    render(<NoteEditor source="原始备注" save={save} />);

    await user.type(note(), "，补充");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存失败：附件上传失败");
    await user.type(note(), "！");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(save).toHaveBeenLastCalledWith("原始备注，补充！", "原始备注，补充");

    await user.type(note(), "？");
    await user.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(note()).toHaveValue("原始备注，补充！");
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

  it("跳到当前地址时不询问，修改保留", async () => {
    const user = userEvent.setup();
    const location = renderNotePage(<NoteEditor source="原始备注" save={vi.fn<SaveNote>()} />);
    await user.type(note(), "，补充");

    await user.click(screen.getByRole("link", { name: "当前页" }));

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/notes");
    expect(note()).toHaveValue("原始备注，补充");
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

  it("保存在途时离开：等保存成功后直接离开，不询问也不重复提交", async () => {
    const user = userEvent.setup();
    const saving = createDeferred<string | void>();
    const save = vi.fn<SaveNote>().mockReturnValue(saving.promise);
    const location = renderNotePage(<NoteEditor source="原始备注" save={save} />);
    await user.type(note(), "，补充");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await user.click(screen.getByRole("link", { name: "去别处" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/notes");

    await act(async () => saving.resolve(undefined));

    expect(await screen.findByText("别处的页面")).toBeInTheDocument();
    expect(location.history.at(-1)).toBe("/elsewhere");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("保存在途时离开：保存失败后再询问，修改与错误保留", async () => {
    const user = userEvent.setup();
    const saving = createDeferred<string | void>();
    const location = renderNotePage(<NoteEditor source="原始备注" save={() => saving.promise} />);
    await user.type(note(), "，补充");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await user.click(screen.getByRole("link", { name: "去别处" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await act(async () => saving.reject(new Error("备注过长")));

    await user.click(within(await leaveDialog()).getByRole("button", { name: "继续编辑" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(location.history.at(-1)).toBe("/notes");
    expect(screen.getByRole("alert")).toHaveTextContent("保存失败：备注过长");
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

  describe("包住会落定的异步动作（如删除）", () => {
    function DeletableNote({ action }: { action: (navigate: (to: string) => void) => Promise<boolean> }) {
      const confirmLeave = useConfirmLeave();
      const [, navigate] = useLocation();
      return (
        <>
          <button type="button" onClick={() => confirmLeave(() => action(navigate))}>
            删除这条备注
          </button>
          <NoteEditor source="原始备注" save={vi.fn<SaveNote>()} inline />
        </>
      );
    }

    async function discardForAction(user: ReturnType<typeof userEvent.setup>) {
      await user.type(note(), "，补充");
      await user.click(screen.getByRole("button", { name: "删除这条备注" }));
      await user.click(within(await leaveDialog()).getByRole("button", { name: "放弃修改" }));
    }

    it("动作失败时修改原样保留", async () => {
      const user = userEvent.setup();
      const action = vi.fn(async () => false);
      renderNotePage(<DeletableNote action={action} />);

      await discardForAction(user);

      await waitFor(() => expect(action).toHaveBeenCalledTimes(1));
      await act(async () => {});
      expect(note()).toHaveValue("原始备注，补充");
      expect(screen.getByText("有未保存的修改")).toBeInTheDocument();
    });

    it("动作成功后才丢弃修改，在途期间修改仍可见", async () => {
      const user = userEvent.setup();
      const deleting = createDeferred<boolean>();
      renderNotePage(<DeletableNote action={() => deleting.promise} />);

      await discardForAction(user);

      expect(note()).toHaveValue("原始备注，补充");
      await act(async () => deleting.resolve(true));
      expect(note()).toHaveValue("原始备注");
    });

    it("动作在途期间自己发起的跳转不再询问", async () => {
      const user = userEvent.setup();
      const deleting = createDeferred<void>();
      const location = renderNotePage(
        <DeletableNote
          action={async (navigate) => {
            await deleting.promise;
            navigate("/elsewhere");
            return true;
          }}
        />,
      );

      await discardForAction(user);
      await act(async () => deleting.resolve());

      expect(await screen.findByText("别处的页面")).toBeInTheDocument();
      expect(location.history.at(-1)).toBe("/elsewhere");
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
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

    /** 用浏览器地址渲染，模拟依次访问 visited 中的页面后进入备注页。 */
    function renderInBrowser(visited = ["/elsewhere"], save: SaveNote = vi.fn<SaveNote>()) {
      window.history.replaceState(null, "", visited[0]);
      for (const path of visited.slice(1)) window.history.pushState(null, "", path);
      window.history.pushState(null, "", "/notes");
      render(
        <LeaveGuardProvider>
          <Switch>
            <Route path="/notes">
              <NoteEditor source="原始备注" save={save} />
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

    it("保存在途时把字段改回原值再后退：不放行，等保存落定后再询问", async () => {
      const user = userEvent.setup();
      const saving = createDeferred<string | void>();
      renderInBrowser(["/elsewhere"], () => saving.promise);
      await user.type(note(), "，补充");
      await user.click(screen.getByRole("button", { name: "保存" }));
      await user.clear(note());
      await user.type(note(), "原始备注");
      await goBack();
      // 被截停时没有可等的界面变化：等 jsdom 在后续任务里派发完 popstate
      await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

      expect(screen.queryByText("别处的页面")).not.toBeInTheDocument();
      expect(window.location.pathname).toBe("/notes");

      await act(async () => saving.resolve(undefined));
      await user.click(within(await leaveDialog()).getByRole("button", { name: "继续编辑" }));

      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(window.location.pathname).toBe("/notes");
      expect(note()).toHaveValue("原始备注");
    });

    it("放行后退时回到原有的历史记录，再后退不会回到已离开的页面", async () => {
      const user = userEvent.setup();
      renderInBrowser(["/start", "/elsewhere"]);
      await user.type(note(), "，补充");

      await goBack();
      await user.click(within(await leaveDialog()).getByRole("button", { name: "放弃修改" }));
      expect(await screen.findByText("别处的页面")).toBeInTheDocument();

      await act(() => window.history.back());
      await waitFor(() => expect(window.location.pathname).toBe("/start"));
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
