import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AssetFormModal } from "./AssetFormModal";

// Mock i18next to return keys as values
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (opts) {
        let result = key;
        for (const [k, v] of Object.entries(opts)) {
          result = result.replace(`{{${k}}}`, String(v));
        }
        return result;
      }
      return key;
    },
  }),
}));

describe("AssetFormModal", () => {
  it("create mode renders empty fields and calls onSubmit", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AssetFormModal type="character" mode="create"
        onClose={() => {}} onSubmit={onSubmit} />
    );
    fireEvent.change(screen.getByLabelText(/field\.name/), { target: { value: "王小明" } });
    fireEvent.click(screen.getByRole("button", { name: /create/ }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ name: "王小明" })));
  });

  it("edit mode prefills fields", () => {
    render(
      <AssetFormModal
        type="scene" mode="edit"
        initialData={{ name: "庙宇", description: "阴森" }}
        onClose={() => {}} onSubmit={vi.fn()}
      />
    );
    expect(screen.getByDisplayValue("庙宇")).toBeInTheDocument();
    expect(screen.getByDisplayValue("阴森")).toBeInTheDocument();
  });

  it("import mode with conflict shows warning", () => {
    render(
      <AssetFormModal
        type="character" mode="import"
        initialData={{ name: "王", description: "" }}
        conflictWith={{ id: "1", type: "character", name: "王", description: "", voice_style: "", image_path: null, audio_path: null, source_project: null, updated_at: null }}
        onClose={() => {}} onSubmit={vi.fn()}
      />
    );
    expect(screen.getByText(/conflict_warning/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /overwrite_existing/ })).toBeInTheDocument();
  });

  it("shows voice_style field only for character type", () => {
    const { rerender } = render(
      <AssetFormModal type="character" mode="create"
        onClose={() => {}} onSubmit={vi.fn()} />
    );
    expect(screen.getByLabelText(/field\.voice_style/)).toBeInTheDocument();

    rerender(
      <AssetFormModal type="scene" mode="create"
        onClose={() => {}} onSubmit={vi.fn()} />
    );
    expect(screen.queryByLabelText(/field\.voice_style/)).not.toBeInTheDocument();
  });

  it("lets a synced character choose primary image and reference voice", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AssetFormModal
        type="character"
        mode="edit"
        initialData={{
          name: "鳄鱼爸爸",
          resources: [
            { id: "img-1", key: "avatarUrl", origin: "catalog", media_type: "image", mime_type: "image/png", path: "avatar.png", byte_size: 1, is_primary: true },
            { id: "img-2", key: "fullBodyImageUrl", origin: "catalog", media_type: "image", mime_type: "image/png", path: "full.png", byte_size: 1, is_primary: false },
            { id: "audio-1", key: "voice1", origin: "catalog", media_type: "audio", mime_type: "audio/wav", path: "voice1.wav", byte_size: 1, is_primary: true },
            { id: "audio-2", key: "voice2", origin: "catalog", media_type: "audio", mime_type: "audio/wav", path: "voice2.wav", byte_size: 1, is_primary: false },
          ],
        }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );

    fireEvent.change(screen.getByLabelText("field.primary_image"), { target: { value: "img-2" } });
    fireEvent.change(screen.getByLabelText("field.primary_audio"), { target: { value: "audio-2" } });
    fireEvent.click(screen.getByRole("button", { name: "save" }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      primary_image_resource_id: "img-2",
      primary_audio_resource_id: "audio-2",
    })));
  });
});
