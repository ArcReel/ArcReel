import { useState, useRef } from "react";
import { useLocation } from "wouter";
import { X, Loader2, Upload } from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";

const NAME_REGEX = /^[a-zA-Z0-9_-]+$/;

const STYLE_OPTIONS = [
  { value: "Photographic", label: "写实摄影" },
  { value: "Anime", label: "动漫风格" },
  { value: "3D Animation", label: "3D 动画" },
] as const;

export function CreateProjectModal() {
  const [, navigate] = useLocation();
  const { setShowCreateModal, setCreatingProject, creatingProject } =
    useProjectsStore();

  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [contentMode, setContentMode] = useState<"narration" | "drama">("narration");
  const [style, setStyle] = useState("Photographic");
  const [nameError, setNameError] = useState("");
  const [styleImageFile, setStyleImageFile] = useState<File | null>(null);
  const [styleImagePreview, setStyleImagePreview] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleStyleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setStyleImageFile(file);
    // 创建预览 URL
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

    if (!name.trim()) {
      setNameError("项目标识不能为空");
      return;
    }
    if (!NAME_REGEX.test(name.trim())) {
      setNameError("仅允许英文字母、数字、下划线和中划线");
      return;
    }
    if (!title.trim()) return;

    setCreatingProject(true);
    try {
      const projectName = name.trim();
      await API.createProject(projectName, title.trim(), style, contentMode);

      // 如果用户选择了风格参考图，在项目创建后上传
      if (styleImageFile) {
        try {
          await API.uploadStyleImage(projectName, styleImageFile);
        } catch {
          // 风格图上传失败不阻塞项目创建
          useAppStore.getState().pushToast(
            "风格参考图上传失败，可稍后在项目设置中重新上传",
            "warning"
          );
        }
      }

      setShowCreateModal(false);
      navigate(`/app/projects/${projectName}`);
    } catch (err) {
      useAppStore.getState().pushToast(
        `创建项目失败: ${(err as Error).message}`,
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
          <h2 className="text-lg font-semibold text-gray-100">新建项目</h2>
          <button
            type="button"
            onClick={() => setShowCreateModal(false)}
            className="rounded p-1 text-gray-400 hover:bg-gray-800 hover:text-gray-200"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              项目标识 <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => { setName(e.target.value); setNameError(""); }}
              placeholder="my-project"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500"
            />
            {nameError && (
              <p className="mt-1 text-xs text-red-400">{nameError}</p>
            )}
            <p className="mt-1 text-xs text-gray-600">
              用于 URL 和文件系统，仅允许英文、数字、下划线、中划线
            </p>
          </div>

          {/* Title */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              项目标题 <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="我的视频项目"
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500"
            />
          </div>

          {/* Content Mode */}
          <div>
            <label className="block text-sm font-medium text-gray-400 mb-1">
              内容模式
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
            disabled={creatingProject || !name.trim() || !title.trim()}
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
