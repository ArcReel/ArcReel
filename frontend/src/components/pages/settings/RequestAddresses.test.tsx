import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import "@/i18n";
import type { EndpointRequest, PathOverrides } from "@/types";
import { RequestAddresses } from "./RequestAddresses";

const SUBMIT: EndpointRequest = {
  name: "submit",
  display_name_key: "cp_request_submit",
  default_path: "/v1/videos/{{ model }}",
  placeholders: ["model"],
  base_url_rule: "strip_version",
};
const POLL: EndpointRequest = {
  name: "poll",
  display_name_key: "cp_request_poll",
  default_path: "/v1/videos/{{ task_id }}",
  placeholders: ["model", "task_id"],
  base_url_rule: "strip_version",
};

function Harness({ requests, initial }: { requests: EndpointRequest[]; initial: PathOverrides | null }) {
  const [overrides, setOverrides] = useState(initial);
  return (
    <>
      <RequestAddresses
        requests={requests}
        baseUrl="https://relay.test/api/v1/"
        modelId="veo-3"
        overrides={overrides}
        onChange={setOverrides}
      />
      <output data-testid="overrides">{JSON.stringify(overrides)}</output>
    </>
  );
}

const overrides = () => JSON.parse(screen.getByTestId("overrides").textContent ?? "null");
const pathButton = (path: string) => screen.getByRole("button", { name: `请求路径 ${path}` });

describe("RequestAddresses", () => {
  it("每条请求标出显示名，模型名占位符显示为模型 ID，任务 id 占位符原样保留", () => {
    render(<Harness requests={[SUBMIT, POLL]} initial={null} />);

    expect(screen.getByText("提交任务")).toBeInTheDocument();
    expect(screen.getByText("查询进度")).toBeInTheDocument();
    expect(pathButton("/v1/videos/veo-3")).toBeInTheDocument();
    expect(pathButton("/v1/videos/{{ task_id }}")).toBeInTheDocument();
    // 端点剥掉了版本段，灰显部分是归一化后的接口地址
    expect(screen.getAllByText("https://relay.test/api")).toHaveLength(2);
  });

  it("就地修改的路径成为修改，缺少开头的 / 时自动补上，并显示默认路径", async () => {
    const user = userEvent.setup();
    render(<Harness requests={[SUBMIT, POLL]} initial={null} />);

    await user.click(pathButton("/v1/videos/{{ task_id }}"));
    const input = screen.getByRole("textbox", { name: "请求路径" });
    expect(input).toHaveValue("/v1/videos/{{ task_id }}");
    await user.clear(input);
    await user.type(input, "api/tasks/{{{{ task_id }}{Enter}");

    expect(overrides()).toEqual({ poll: "/api/tasks/{{ task_id }}" });
    // 有修改的请求相对原样接口地址，不再剥版本段
    expect(screen.getByText("https://relay.test/api/v1")).toBeInTheDocument();
    expect(screen.getByText("默认：/v1/videos/{{ task_id }}")).toBeInTheDocument();
  });

  it("空值不算修改；接口地址不经归一化时，与默认相同的值也不算", async () => {
    const user = userEvent.setup();
    const asIs = { ...SUBMIT, base_url_rule: "as_is" } as const;
    render(<Harness requests={[asIs, POLL]} initial={{ submit: "/custom", poll: "/api/{{ task_id }}" }} />);

    await user.click(pathButton("/custom"));
    const input = screen.getByRole("textbox", { name: "请求路径" });
    await user.clear(input);
    await user.type(input, "/v1/videos/{{{{ model }}");
    await user.tab();
    expect(overrides()).toEqual({ poll: "/api/{{ task_id }}" });

    await user.click(pathButton("/api/{{ task_id }}"));
    await user.clear(screen.getByRole("textbox", { name: "请求路径" }));
    await user.keyboard("{Enter}");
    expect(overrides()).toBeNull();
  });

  it("接口地址经过归一化时，与默认同文本的路径仍是修改，用来去掉自动补上或剥掉的部分", async () => {
    const user = userEvent.setup();
    render(<Harness requests={[POLL]} initial={null} />);

    await user.click(pathButton("/v1/videos/{{ task_id }}"));
    const input = screen.getByRole("textbox", { name: "请求路径" });
    await user.clear(input);
    await user.type(input, "/v1/videos/{{{{ task_id }}{Enter}");

    expect(overrides()).toEqual({ poll: "/v1/videos/{{ task_id }}" });
    expect(screen.getByText("https://relay.test/api/v1")).toBeInTheDocument();
  });

  it("点开路径不改动就失焦，不产生修改", async () => {
    const user = userEvent.setup();
    render(<Harness requests={[POLL]} initial={null} />);

    await user.click(pathButton("/v1/videos/{{ task_id }}"));
    await user.tab();

    expect(overrides()).toBeNull();
  });

  it("Esc 取消修改", async () => {
    const user = userEvent.setup();
    render(<Harness requests={[POLL]} initial={null} />);

    await user.click(pathButton("/v1/videos/{{ task_id }}"));
    await user.type(screen.getByRole("textbox", { name: "请求路径" }), "/extra{Escape}");

    expect(overrides()).toBeNull();
    expect(pathButton("/v1/videos/{{ task_id }}")).toBeInTheDocument();
  });

  it("恢复默认移除这条请求的修改", async () => {
    const user = userEvent.setup();
    render(<Harness requests={[POLL]} initial={{ poll: "/api/{{ task_id }}" }} />);

    await user.click(screen.getByRole("button", { name: "恢复默认" }));

    expect(overrides()).toBeNull();
    expect(screen.queryByText(/^默认：/)).not.toBeInTheDocument();
  });

  it("没有修改时，自动补上的 /v1 单独标出", () => {
    render(
      <RequestAddresses
        requests={[{ ...POLL, base_url_rule: "append_v1", default_path: "/videos/{{ task_id }}" }]}
        baseUrl="https://relay.test"
        modelId="veo-3"
        overrides={null}
        onChange={() => {}}
      />,
    );

    expect(screen.getByText("/v1")).toBeInTheDocument();
  });

  it.each([
    ["ensure_api_v3", "https://relay.test", "https://relay.test/api/v3"],
    ["ensure_api_v3", "https://relay.test/api/v3", "https://relay.test/api/v3"],
    ["ensure_ent_v2", "https://relay.test", "https://relay.test/ent/v2"],
    ["dashscope_api_v1", "https://relay.test/compatible-mode/v1", "https://relay.test/api/v1"],
    ["strip_messages", "https://relay.test/anthropic/v1/messages/", "https://relay.test/anthropic"],
    ["strip_messages", "https://relay.test/anthropic/v1", "https://relay.test/anthropic/v1"],
  ] as const)("没有修改时按 %s 还原 %s 的接口地址", (rule, baseUrl, base) => {
    render(
      <RequestAddresses
        requests={[{ ...POLL, base_url_rule: rule }]}
        baseUrl={baseUrl}
        modelId="m"
        overrides={null}
        onChange={() => {}}
      />,
    );

    expect(screen.getByText(base)).toBeInTheDocument();
  });
});
