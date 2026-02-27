import { useState, useEffect, useRef, useCallback } from "react";
import { useLocation } from "wouter";
import {
  ChevronRight,
  ChevronDown,
  FileText,
  Users,
  Puzzle,
  Film,
  Circle,
  User,
  LayoutDashboard,
  Upload,
  X,
} from "lucide-react";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { API } from "@/api";

// ---------------------------------------------------------------------------
// CollapsibleSection — reusable accordion primitive
// ---------------------------------------------------------------------------

function CollapsibleSection({
  title,
  icon: Icon,
  children,
  defaultOpen = true,
  action,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  defaultOpen?: boolean;
  action?: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section>
      <div className="flex w-full items-center">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex flex-1 items-center gap-1.5 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-gray-500 transition-colors hover:text-gray-400"
        >
          {open ? (
            <ChevronDown className="h-3 w-3 shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0" />
          )}
          <Icon className="h-3.5 w-3.5 shrink-0" />
          <span>{title}</span>
        </button>
        {action && <div className="pr-2">{action}</div>}
      </div>
      {open && <div className="pb-1">{children}</div>}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Status dot color mapping
// ---------------------------------------------------------------------------

const STATUS_DOT_CLASSES: Record<string, string> = {
  draft: "text-gray-500",
  in_production: "text-amber-500",
  completed: "text-emerald-500",
  missing: "text-red-500",
};

// ---------------------------------------------------------------------------
// CharacterThumbnail — round avatar with fallback
// ---------------------------------------------------------------------------

function CharacterThumbnail({
  name,
  sheetPath,
  projectName,
}: {
  name: string;
  sheetPath: string | undefined;
  projectName: string;
}) {
  const [imgError, setImgError] = useState(false);

  if (!sheetPath || imgError) {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-700 text-gray-400">
        <User className="h-3.5 w-3.5" />
      </span>
    );
  }

  return (
    <img
      src={API.getFileUrl(projectName, sheetPath)}
      alt={name}
      className="h-6 w-6 shrink-0 rounded-full object-cover"
      onError={() => setImgError(true)}
    />
  );
}

// ---------------------------------------------------------------------------
// ClueThumbnail — square icon with fallback
// ---------------------------------------------------------------------------

function ClueThumbnail({
  name,
  sheetPath,
  projectName,
}: {
  name: string;
  sheetPath: string | undefined;
  projectName: string;
}) {
  const [imgError, setImgError] = useState(false);

  if (!sheetPath || imgError) {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-gray-700 text-gray-400">
        <Puzzle className="h-3.5 w-3.5" />
      </span>
    );
  }

  return (
    <img
      src={API.getFileUrl(projectName, sheetPath)}
      alt={name}
      className="h-6 w-6 shrink-0 rounded object-cover"
      onError={() => setImgError(true)}
    />
  );
}

// ---------------------------------------------------------------------------
// EmptyState — shared empty placeholder
// ---------------------------------------------------------------------------

function EmptyState({ text }: { text: string }) {
  return (
    <p className="px-3 py-1.5 text-xs italic text-gray-600">{text}</p>
  );
}

// ---------------------------------------------------------------------------
// AssetSidebar
// ---------------------------------------------------------------------------

interface AssetSidebarProps {
  className?: string;
}

export function AssetSidebar({ className }: AssetSidebarProps) {
  const { currentProjectData, currentProjectName } = useProjectsStore();
  const sourceFilesVersion = useAppStore((s) => s.sourceFilesVersion);
  const [location, setLocation] = useLocation();

  const characters = currentProjectData?.characters ?? {};
  const clues = currentProjectData?.clues ?? {};
  const episodes = currentProjectData?.episodes ?? [];
  const projectName = currentProjectName ?? "";

  // 源文件列表
  const [sourceFiles, setSourceFiles] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadSourceFiles = useCallback(() => {
    if (!projectName) {
      setSourceFiles([]);
      return;
    }
    API.listFiles(projectName)
      .then((res) => {
        const raw = res.files as unknown;
        if (Array.isArray(raw)) {
          setSourceFiles(raw);
        } else if (raw && typeof raw === "object") {
          const grouped = raw as Record<string, Array<{ name: string }>>;
          setSourceFiles((grouped.source ?? []).map((f) => f.name));
        }
      })
      .catch(() => {
        setSourceFiles([]);
      });
  }, [projectName]);

  useEffect(() => {
    loadSourceFiles();
  }, [loadSourceFiles, sourceFilesVersion]);

  // 上传源文件
  const handleUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !projectName) return;
    try {
      await API.uploadFile(projectName, "source", file);
      loadSourceFiles();
      useAppStore.getState().invalidateSourceFiles();
    } catch {
      // 静默失败
    }
    // 重置 input 以允许再次选择同一文件
    e.target.value = "";
  }, [projectName, loadSourceFiles]);

  // 删除源文件
  const handleDeleteFile = useCallback(async (filename: string) => {
    if (!projectName) return;
    if (!confirm(`确定要删除 "${filename}" 吗？`)) return;
    try {
      await API.deleteSourceFile(projectName, filename);
      loadSourceFiles();
      useAppStore.getState().invalidateSourceFiles();
      // 如果当前正在查看该文件，返回概览
      if (location === `/source/${encodeURIComponent(filename)}`) {
        setLocation("/");
      }
    } catch {
      // 静默失败
    }
  }, [projectName, loadSourceFiles, location, setLocation]);

  const characterEntries = Object.entries(characters);
  const clueEntries = Object.entries(clues);

  // Check if a path is active (matches current nested location)
  const isActive = (path: string) => location === path;

  return (
    <aside
      className={`flex flex-col overflow-y-auto bg-gray-900 ${className ?? ""}`}
    >
      {/* ---- Project Overview nav item ---- */}
      <button
        type="button"
        onClick={() => setLocation("/")}
        className={`flex w-full items-center gap-2 px-3 py-2.5 text-sm transition-colors ${
          isActive("/")
            ? "bg-gray-800 text-white"
            : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
        }`}
      >
        <LayoutDashboard className="h-4 w-4 shrink-0 text-indigo-400" />
        <span className="font-medium">项目概览</span>
      </button>

      {/* ---- Divider ---- */}
      <div className="mx-3 border-t border-gray-800" />

      {/* ---- Section 1: Source Files ---- */}
      <CollapsibleSection
        title="源文件"
        icon={FileText}
        action={
          <>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
              title="上传源文件"
            >
              <Upload className="h-3.5 w-3.5" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.doc,.docx"
              onChange={handleUpload}
              className="hidden"
            />
          </>
        }
      >
        {sourceFiles.length === 0 ? (
          <EmptyState text="暂无文件" />
        ) : (
          <ul>
            {sourceFiles.map((name) => {
              const filePath = `/source/${encodeURIComponent(name)}`;
              const active = isActive(filePath);
              return (
                <li key={name}>
                  <div
                    className={`group flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors ${
                      active
                        ? "bg-gray-800 text-white"
                        : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => setLocation(filePath)}
                      className="flex flex-1 items-center gap-2 truncate text-left"
                    >
                      <FileText className="h-3.5 w-3.5 shrink-0 text-gray-500" />
                      <span className="truncate">{name}</span>
                    </button>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); handleDeleteFile(name); }}
                      className="shrink-0 rounded p-0.5 text-gray-600 opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                      title="删除文件"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CollapsibleSection>

      {/* ---- Divider ---- */}
      <div className="mx-3 border-t border-gray-800" />

      {/* ---- Section 2: Lorebook (Characters + Clues) ---- */}
      <CollapsibleSection title="设定集" icon={Users} defaultOpen={true}>
        {/* Characters sub-section */}
        <div className="mb-1">
          <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-gray-600">
            <Users className="h-3 w-3" />
            <span>角色</span>
          </div>
          {characterEntries.length === 0 ? (
            <EmptyState text="暂无角色" />
          ) : (
            <ul>
              {characterEntries.map(([name, char]) => (
                <li key={name}>
                  <button
                    type="button"
                    onClick={() => setLocation("/characters")}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors ${
                      isActive("/characters")
                        ? "bg-gray-800 text-white"
                        : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
                    }`}
                  >
                    <CharacterThumbnail
                      name={name}
                      sheetPath={char.character_sheet}
                      projectName={projectName}
                    />
                    <span className="truncate">{name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Clues sub-section */}
        <div>
          <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-gray-600">
            <Puzzle className="h-3 w-3" />
            <span>线索</span>
          </div>
          {clueEntries.length === 0 ? (
            <EmptyState text="暂无线索" />
          ) : (
            <ul>
              {clueEntries.map(([name, clue]) => (
                <li key={name}>
                  <button
                    type="button"
                    onClick={() => setLocation("/clues")}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors ${
                      isActive("/clues")
                        ? "bg-gray-800 text-white"
                        : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
                    }`}
                  >
                    <ClueThumbnail
                      name={name}
                      sheetPath={clue.clue_sheet}
                      projectName={projectName}
                    />
                    <span className="truncate">{name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CollapsibleSection>

      {/* ---- Divider ---- */}
      <div className="mx-3 border-t border-gray-800" />

      {/* ---- Section 3: Episodes ---- */}
      <CollapsibleSection title="剧集" icon={Film}>
        {episodes.length === 0 ? (
          <EmptyState text="暂无剧集" />
        ) : (
          <ul>
            {episodes.map((ep) => {
              const episodePath = `/episodes/${ep.episode}`;
              const active = isActive(episodePath);
              const statusClass =
                STATUS_DOT_CLASSES[ep.status ?? "draft"] ??
                STATUS_DOT_CLASSES.draft;

              return (
                <li key={ep.episode}>
                  <button
                    type="button"
                    onClick={() => setLocation(episodePath)}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors ${
                      active
                        ? "bg-gray-800 text-white"
                        : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
                    }`}
                  >
                    <Circle
                      className={`h-2.5 w-2.5 shrink-0 fill-current ${statusClass}`}
                    />
                    <span className="truncate">
                      E{ep.episode}: {ep.title}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </CollapsibleSection>
    </aside>
  );
}
