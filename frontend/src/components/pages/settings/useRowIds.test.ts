import { useState } from "react";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useNamedRowIds, useRowIds } from "./useRowIds";

describe("useRowIds", () => {
  // 调用方在同一个处理函数里改行与改 id，两者在同一次渲染里生效
  function useRows(initial: number) {
    const [count, setCount] = useState(initial);
    return { ...useRowIds(count), setCount };
  }

  it("keeps ids through its own add and remove, and rebuilds them all when the row count changes from outside", () => {
    const { result } = renderHook(() => useRows(3));
    const [first, , third] = result.current.ids;

    act(() => {
      result.current.remove(1);
      result.current.setCount(2);
    });
    expect(result.current.ids).toEqual([first, third]);

    act(() => {
      result.current.append();
      result.current.setCount(3);
    });
    expect(result.current.ids.slice(0, 2)).toEqual([first, third]);
    expect(new Set(result.current.ids).size).toBe(3);

    // 外部整体换值（采用官方值、放弃修改）
    act(() => result.current.setCount(1));
    expect(result.current.ids).toHaveLength(1);
    expect([first, third]).not.toContain(result.current.ids[0]);
  });
});

describe("useNamedRowIds", () => {
  it("moves the id with a rename and issues ids to names it has not seen", () => {
    const { result, rerender } = renderHook(({ names }) => useNamedRowIds(names), {
      initialProps: { names: ["a", "b"] },
    });
    const idOfB = result.current.idOf("b");

    act(() => result.current.rename("b", "2"));
    rerender({ names: ["2", "a"] });
    expect(result.current.idOf("2")).toBe(idOfB);

    rerender({ names: ["2", "a", "c"] });
    expect(new Set(["2", "a", "c"].map((name) => result.current.idOf(name))).size).toBe(3);
  });
});
