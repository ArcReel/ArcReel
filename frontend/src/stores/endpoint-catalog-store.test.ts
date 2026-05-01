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
  },
  {
    key: "newapi-video",
    media_type: "video",
    family: "newapi",
    display_name_key: "endpoint_newapi_video_display",
    request_method: "POST",
    request_path_template: "/v1/video/generations",
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
    });
    expect(s.endpointPaths).toEqual({
      "openai-chat": { method: "POST", path: "/v1/chat/completions" },
      "newapi-video": { method: "POST", path: "/v1/video/generations" },
    });
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
