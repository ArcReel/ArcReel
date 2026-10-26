import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import i18n from "@/i18n";

import { ItemPositionBadge } from "./ItemPositionBadge";

describe("ItemPositionBadge", () => {
  it("shows only the number and names the item type in its accessible name", () => {
    render(
      <>
        <ItemPositionBadge position={5} kind="shot" />
        <ItemPositionBadge position={5} kind="unit" />
      </>,
    );
    expect(screen.getByRole("img", { name: "分镜 5" })).toHaveTextContent(/^5$/);
    expect(screen.getByRole("img", { name: "视频单元 5" })).toHaveTextContent(/^5$/);
  });

  it("says deleted instead of falling back to the item ID", () => {
    render(<ItemPositionBadge position={null} kind="shot" />);
    expect(screen.getByRole("img", { name: "已删除" })).toHaveTextContent("已删除");
  });

  it.each([
    ["en", "Shot 5", "Video unit 5", "Deleted"],
    ["vi", "Phân cảnh 5", "Đơn vị video 5", "Đã xóa"],
  ])("names the item in %s", async (language, shot, unit, deleted) => {
    try {
      await act(async () => {
        await i18n.changeLanguage(language);
      });
      render(
        <>
          <ItemPositionBadge position={5} kind="shot" />
          <ItemPositionBadge position={5} kind="unit" />
          <ItemPositionBadge position={null} kind="unit" />
        </>,
      );
      expect(screen.getByRole("img", { name: shot })).toBeInTheDocument();
      expect(screen.getByRole("img", { name: unit })).toBeInTheDocument();
      expect(screen.getByRole("img", { name: deleted })).toBeInTheDocument();
    } finally {
      await act(async () => {
        await i18n.changeLanguage("zh");
      });
    }
  });
});
