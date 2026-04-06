import { useState, useRef } from "react";
import { useLocation } from "wouter";
import { X, Loader2, Upload } from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";

const STYLE_OPTIONS = [
  { value: "Photographic", label: "Nhiếp ảnh chân thực" },
  { value: "Anime", label: "Phong cách anime" },
  { value: "3D Animation", label: "Hoạt hình 3D" },
] as const;

export function CreateProjectModal() {
  const [, navigate] = useLocation();
  const { setShowCreateModal, setCreatingProject, creatingProject } =
    useProjectsStore();

  const [title, setTitle] = useState("");
  const [contentMode, setContentMode] = useState<"narration" | "drama">("narration");
  const [style, setStyle] = useState("Photographic");
  const [titleError, setTitleError] = useState("");
  const [styleImageFile, setStyleImageFile] = useState<File | null>(null);
  const [styleImagePreview, setStyleImagePreview] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleStyleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setStyleImageFile(file);
    // Tạo URL xem trước
    const url = URL.createObjectURL(file);
    setStyleImagePreview(url);
  };

  const clearStyleImage = () => {
    setStyleImageFile(null);
    if (styleImagePreview) {
      URL.revokeObjectURL(styleImagePreview);
      setStyleImagePreview(null);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!title.trim()) {
      setTitleError("Tiêu đề dự án không được để trống");
      return;
    }

    setCreatingProject(true);
    try {
      const response = await API.createProject(title.trim(), style, contentMode);
      const projectName = response.name;

      // Nếu người dùng chọn ảnh tham chiếu phong cách, tải lên sau khi tạo dự án
      if (styleImageFile) {
        try {
          await API.uploadStyleImage(projectName, styleImageFile);
        } catch {
          // Tải ảnh phong cách thất bại không chặn việc tạo dự án
          useAppStore.getState().pushToast(
            "Tải lên ảnh tham chiếu phong cách thất bại, có thể tải lên lại sau trong cài đặt dự án",
            "warning"
          );
        }
      }

      setShowCreateModal(false);
      navigate(`/app/projects/${projectName}`);
    } catch (err) {
      useAppStore.getState().pushToast(
        `Tạo dự án thất bại: ${(err as Error).message}`,
        "error"
      );
    } finally {
      setCreatingProject(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-xl border border-gray-700 bg-gray-900 p-6 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold text-gray-100">Tạo dự án mới</h2>
          <button
            type="button"
            onClick={() => setShowCreateModal(false)}
            className="rounded p-1 text-gray-400 hover:bg-gray-800 hover:text-gray-200"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Title */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Tiêu đề dự án <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                setTitleError("");
              }}
              placeholder="Ví dụ: Tái sinh chi Hoàng hậu uy vũ"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500"
            />
            {titleError && (
              <p className="mt-1 text-xs text-red-400">{titleError}</p>
            )}
            <p className="mt-1 text-xs text-gray-600">
              Hệ thống sẽ tự động tạo mã định danh dự án nội bộ dùng cho URL và lưu trữ tệp
            </p>
          </div>

          {/* Content Mode */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              Chế độ nội dung
            </label>
            <div className="flex gap-3">
              <label className={`flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors ${
                contentMode === "narration"
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                  : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
              }`}>
                <input
                  type="radio"
                  name="contentMode"
                  value="narration"
                  checked={contentMode === "narration"}
                  onChange={() => setContentMode("narration")}
                  className="sr-only"
                />
                说书+画面
              </label>
              <label className={`flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors ${
                contentMode === "drama"
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                  : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
              }`}>
                <input
                  type="radio"
                  name="contentMode"
                  value="drama"
                  checked={contentMode === "drama"}
                  onChange={() => setContentMode("drama")}
                  className="sr-only"
                />
                剧集动画
              </label>
            </div>
          </div>

          {/* Style — fixed radio options */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              视觉风格
            </label>
            <div className="flex gap-2">
              {STYLE_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={`flex-1 cursor-pointer rounded-lg border px-3 py-2 text-center text-sm transition-colors ${
                    style === opt.value
                      ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                      : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
                  }`}
                >
                  <input
                    type="radio"
                    name="style"
                    value={opt.value}
                    checked={style === opt.value}
                    onChange={() => setStyle(opt.value)}
                    className="sr-only"
                  />
                  {opt.label}
                </label>
              ))}
            </div>
          </div>

          {/* Style reference image */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              风格参考图 <span className="text-xs text-gray-600 font-normal">（可选）</span>
            </label>
            {styleImagePreview ? (
              <div className="relative rounded-lg border border-gray-700 overflow-hidden">
                <img
                  src={styleImagePreview}
                  alt="风格参考图预览"
                  className="w-full h-32 object-cover"
                />
                <button
                  type="button"
                  onClick={clearStyleImage}
                  className="absolute top-1.5 right-1.5 rounded-full bg-gray-900/80 p-1 text-gray-300 hover:bg-gray-900 hover:text-white transition-colors"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-gray-700 bg-gray-800/50 px-3 py-4 text-sm text-gray-500 transition-colors hover:border-gray-500 hover:text-gray-300"
              >
                <Upload className="h-4 w-4" />
                上传参考图片
              </button>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept=".png,.jpg,.jpeg,.webp"
              onChange={handleStyleImageChange}
              className="hidden"
            />
            <p className="mt-1 text-xs text-gray-600">
              上传后将自动分析风格特征，用于生成一致的画面
            </p>
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={creatingProject || !title.trim()}
            className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {creatingProject ? (
              <span className="inline-flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                创建中...
              </span>
            ) : (
              "创建项目"
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
