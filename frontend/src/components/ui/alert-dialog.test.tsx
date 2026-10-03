import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "./alert-dialog";

function DeleteDialog({ onDelete }: { onDelete: () => void }) {
  return (
    <AlertDialog>
      <AlertDialogTrigger>删除项目</AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除项目？</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <AlertDialogDescription>
            项目文件会一并删除，无法撤销。<a href="#usage">查看占用情况</a>
          </AlertDialogDescription>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={onDelete}>
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

describe("AlertDialog", () => {
  it("opens with focus on Cancel so Enter never confirms by accident", async () => {
    const onDelete = vi.fn();
    const user = userEvent.setup();
    render(<DeleteDialog onDelete={onDelete} />);

    await user.click(screen.getByRole("button", { name: "删除项目" }));

    const dialog = await screen.findByRole("alertdialog", { name: "删除项目？" });
    await waitFor(() => expect(screen.getByRole("button", { name: "取消" })).toHaveFocus());

    await user.keyboard("{Enter}");
    expect(onDelete).not.toHaveBeenCalled();
    await waitFor(() => expect(dialog).not.toBeInTheDocument());
  });
});
