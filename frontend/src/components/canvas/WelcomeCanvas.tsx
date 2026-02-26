import { useState, useRef, useCallback } from "react";
import { Upload, FileText, Sparkles } from "lucide-react";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WelcomeCanvasProps {
  projectName: string;
  onUpload?: (file: File) => void;
}

// ---------------------------------------------------------------------------
// WelcomeCanvas — shown when a project has no source files yet.
// Provides a drag-and-drop upload zone and a welcoming message.
// ---------------------------------------------------------------------------

export function WelcomeCanvas({ projectName, onUpload }: WelcomeCanvasProps) {
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file && (file.name.endsWith(".txt") || file.name.endsWith(".md"))) {
        onUpload?.(file);
      }
    },
    [onUpload],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) onUpload?.(file);
    },
    [onUpload],
  );

  return (
    <div className="flex h-full flex-col items-center justify-center p-8">
      <div className="max-w-lg text-center space-y-6">
        {/* Welcome heading */}
        <div>
          <Sparkles className="mx-auto mb-3 h-10 w-10 text-indigo-400" />
          <h1 className="text-2xl font-bold text-gray-100">
            欢迎来到 {projectName}！
          </h1>
          <p className="mt-2 text-sm text-gray-400">
            请拖拽或上传您的小说源文件（txt/md），AI 将为您拆解设定。
          </p>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          className={`cursor-pointer rounded-xl border-2 border-dashed p-12 transition-colors ${
            isDragging
              ? "border-indigo-500 bg-indigo-500/10"
              : "border-gray-700 hover:border-gray-600 hover:bg-gray-900/50"
          }`}
        >
          <Upload
            className={`mx-auto h-8 w-8 ${isDragging ? "text-indigo-400" : "text-gray-500"}`}
          />
          <p className="mt-3 text-sm text-gray-300">拖拽文件到此处</p>
          <p className="mt-1 text-xs text-gray-500">
            或点击选择文件（支持 .txt / .md）
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.md"
            className="hidden"
            onChange={handleFileSelect}
          />
        </div>

        {/* Quick tips */}
        <div className="text-left space-y-2">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            接下来会发生什么？
          </p>
          <div className="space-y-1.5 text-xs text-gray-400">
            <div className="flex items-start gap-2">
              <FileText className="mt-0.5 h-3.5 w-3.5 text-gray-500 shrink-0" />
              <span>AI 将分析您的小说，提取角色、线索和世界观设定</span>
            </div>
            <div className="flex items-start gap-2">
              <Sparkles className="mt-0.5 h-3.5 w-3.5 text-gray-500 shrink-0" />
              <span>自动生成项目概述，然后您可以开始创建剧本和分镜</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
