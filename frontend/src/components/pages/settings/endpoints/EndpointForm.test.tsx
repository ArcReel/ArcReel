import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "@/i18n";
import type { EndpointDefinition } from "@/types";
import { EndpointForm } from "./EndpointForm";

const IMAGE_DEFINITION: EndpointDefinition = {
  kind: "declarative",
  schema_version: "1.1.0",
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
});
