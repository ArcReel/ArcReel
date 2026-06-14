import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { getCustomProviderModels, getProviderModels } from "./provider-models";

describe("provider-models fetchers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // 供应商配置可变（用户在设置页编辑模型 supported_durations），前端不得持久缓存它——
  // 每次消费都必须重拉，否则项目设置/向导读到的时长集会陈旧（ADR 0035）。
  it("getCustomProviderModels re-fetches on every call (no persistent cache)", async () => {
    const spy = vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });

    await getCustomProviderModels();
    await getCustomProviderModels();

    expect(spy).toHaveBeenCalledTimes(2);
  });

  // 内置供应商缓存同理：status/enabled 等可变项陈旧会让模型选择器漏显刚配好的供应商。
  it("getProviderModels re-fetches on every call (no persistent cache)", async () => {
    const spy = vi.spyOn(API, "getProviders").mockResolvedValue({ providers: [] });

    await getProviderModels();
    await getProviderModels();

    expect(spy).toHaveBeenCalledTimes(2);
  });
});
