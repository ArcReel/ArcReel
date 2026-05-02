import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { EndpointDescriptor } from "@/types";
import { useEndpointCatalogStore } from "./endpoint-catalog-store";

const FIXTURE: EndpointDescriptor[] = [
  {
    key: "openai-chat",
    media_type: "text",
    family: "openai",
    display_name_key: "endpoint_openai_chat_display",
    request_method: "POST",
    request_path_template: "/v1/chat/completions",
    image_capabilities: null,
  },
  {
    key: "newapi-video",
    media_type: "video",
    family: "newapi",
    display_name_key: "endpoint_newapi_video_display",
    request_method: "POST",
    request_path_template: "/v1/video/generations",
    image_capabilities: null,
  },
  {
    key: "openai-images",
    media_type: "image",
    family: "openai",
    display_name_key: "endpoint_openai_images_display",
    request_method: "POST",
    request_path_template: "/v1/images/{generations,edits}",
    image_capabilities: ["text_to_image", "image_to_image"],
  },
  {
    key: "openai-images-generations",
    media_type: "image",
    family: "openai",
    display_name_key: "endpoint_openai_images_generations_display",
    request_method: "POST",
    request_path_template: "/v1/images/generations",
    image_capabilities: ["text_to_image"],
  },
  {
    key: "openai-images-edits",
    media_type: "image",
    family: "openai",
    display_name_key: "endpoint_openai_images_edits_display",
    request_method: "POST",
    request_path_template: "/v1/images/edits",
    image_capabilities: ["image_to_image"],
  },
];

describe("endpoint-catalog-store", () => {
  beforeEach(() => {
    useEndpointCatalogStore.setState(useEndpointCatalogStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("fetch populates endpoints + derives maps", async () => {
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: FIXTURE });

    await useEndpointCatalogStore.getState().fetch();

    const s = useEndpointCatalogStore.getState();
    expect(s.initialized).toBe(true);
    expect(s.endpoints).toEqual(FIXTURE);
    expect(s.endpointToMediaType).toEqual({
      "openai-chat": "text",
      "newapi-video": "video",
      "openai-images": "image",
      "openai-images-generations": "image",
      "openai-images-edits": "image",
    });
    expect(s.endpointPaths["openai-chat"]).toEqual({ method: "POST", path: "/v1/chat/completions" });
    expect(s.endpointPaths["openai-images-edits"]).toEqual({ method: "POST", path: "/v1/images/edits" });
  });

  it("derives endpointToImageCapabilities from catalog", async () => {
    vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: FIXTURE });

    await useEndpointCatalogStore.getState().fetch();

    const map = useEndpointCatalogStore.getState().endpointToImageCapabilities;
    expect(map["openai-images"]).toEqual(["text_to_image", "image_to_image"]);
    expect(map["openai-images-generations"]).toEqual(["text_to_image"]);
    expect(map["openai-images-edits"]).toEqual(["image_to_image"]);
    // 非 image 类不出现在 map 中
    expect(map["openai-chat"]).toBeUndefined();
    expect(map["newapi-video"]).toBeUndefined();
  });

  it("fetch short-circuits after initialized", async () => {
    const spy = vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: FIXTURE });

    await useEndpointCatalogStore.getState().fetch();
    await useEndpointCatalogStore.getState().fetch();

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("refresh re-fetches even after initialized", async () => {
    const spy = vi.spyOn(API, "listEndpointCatalog").mockResolvedValue({ endpoints: FIXTURE });

    await useEndpointCatalogStore.getState().fetch();
    await useEndpointCatalogStore.getState().refresh();

    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("fetch keeps initialized=false on transient error so it can retry", async () => {
    vi.spyOn(API, "listEndpointCatalog")
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce({ endpoints: FIXTURE });

    await useEndpointCatalogStore.getState().fetch();
    expect(useEndpointCatalogStore.getState().initialized).toBe(false);

    await useEndpointCatalogStore.getState().fetch();
    expect(useEndpointCatalogStore.getState().initialized).toBe(true);
    expect(useEndpointCatalogStore.getState().endpoints).toEqual(FIXTURE);
  });
});
