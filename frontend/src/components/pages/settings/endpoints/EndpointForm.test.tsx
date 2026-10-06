import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "@/i18n";
import type { EndpointDefinition } from "@/types";
import { EndpointForm } from "./EndpointForm";

const IMAGE_DEFINITION: EndpointDefinition = {
  kind: "declarative",
  schema_version: "1.2.0",
  media_type: "image",
  meta: { name: "Async Image", author: "ArcReel", version: "1.0.0" },
  auth: { headers: { Authorization: "Bearer {{ api_key }}" } },
  submit: {
    method: "POST",
    url: "{{ base_url }}/v1/images/generations",
    body: { model: "{{ model }}", prompt: "{{ prompt }}" },
    extract: { task_id: ["$.data[0].task_id"] },
  },
  poll: {
    method: "GET",
    url: "{{ base_url }}/v1/tasks/{{ task_id }}",
    extract: { status: ["$.data.status"], image_url: ["$.data.result.images[0].url[0]"] },
  },
  status_map: { completed: "succeeded" },
  capabilities: { text_to_image: true },
};

describe("EndpointForm", () => {
  it("edits the image URL path of an image definition and offers no video-only fields", () => {
    const onChange = vi.fn();
    render(<EndpointForm definition={IMAGE_DEFINITION} onChange={onChange} readOnly={false} />);

    fireEvent.change(screen.getByLabelText("图片地址 1"), { target: { value: "$.data.url" } });

    const next: EndpointDefinition = onChange.mock.calls[0][0];
    expect(next.media_type).toBe("image");
    expect(next.poll.extract).toEqual({ status: ["$.data.status"], image_url: ["$.data.url"] });
    expect(screen.queryByText("视频地址")).not.toBeInTheDocument();
    expect(screen.queryByText("文生视频")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "文生图" })).toBeChecked();
  });

  it("declares image-to-image with its reference limit and adds a reference image input", () => {
    const onChange = vi.fn();
    render(<EndpointForm definition={IMAGE_DEFINITION} onChange={onChange} readOnly={false} />);

    fireEvent.click(screen.getByRole("checkbox", { name: "图生图" }));
    expect(onChange.mock.calls[0][0].capabilities).toEqual({ text_to_image: true, image_to_image: true });

    fireEvent.change(screen.getByLabelText("参考图上限"), { target: { value: "4" } });
    expect(onChange.mock.calls[1][0].capabilities).toEqual({ text_to_image: true, max_reference_images: 4 });

    fireEvent.click(screen.getByRole("button", { name: "添加素材" }));
    expect(onChange.mock.calls[2][0].inputs).toEqual({ "": { source: "reference_images", encoding: "data_uri" } });
  });

  it("sets request paths, headers, value paths and the body in monospace, and leaves the base URL proportional", () => {
    render(<EndpointForm definition={IMAGE_DEFINITION} onChange={vi.fn()} readOnly={false} />);

    const mono = [
      screen.getByLabelText(/^请求地址/),
      screen.getByLabelText(/^查询地址/),
      screen.getByLabelText("请求头名称"),
      screen.getByLabelText("请求头内容"),
      screen.getByLabelText("任务 ID 1"),
      screen.getByLabelText("请求体（JSON）"),
      screen.getByLabelText("端点状态值"),
    ];
    for (const field of mono) expect(field).toHaveClass("font-mono");
    // 标签里连带着说明文字，按开头匹配。
    for (const label of [/^默认接口地址/, /^版本/, /^名称/]) {
      expect(screen.getByLabelText(label).closest(".font-mono")).toBeNull();
    }
  });
});
