
import { useState, useEffect, useRef, useCallback } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
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
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
// ---------------------------------------------------------------------------
// Sidebar Dot Status mapping
// ---------------------------------------------------------------------------

const STATUS_DOT_CLASSES: Record<string, string> = {
  draft: "text-gray-600",
  scripted: "text-indigo-500",
  in_production: "text-amber-500",
  completed: "text-emerald-500",
};

// ---------------------------------------------------------------------------
// CollapsibleSection — sub-component for sidebar groups
// ---------------------------------------------------------------------------

function CollapsibleSection({
  title,
  icon: Icon,
  children,
  action,
  defaultOpen = false,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  action?: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div className="flex flex-col">
      <div className="group flex items-center justify-between px-3 py-2 text-xs font-semibold uppercase tracking-wider text-gray-500 transition-colors hover:text-gray-300">
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="flex flex-1 items-center gap-2 focus-ring rounded"
        >
          {isOpen ? (
            <ChevronDown className="h-3 w-3 shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0" />
          )}
          <Icon className="h-3.5 w-3.5 shrink-0" />
          <span>{title}</span>
        </button>
        {action && (
          <div className="opacity-0 group-hover:opacity-100 transition-opacity">
            {action}
          </div>
        )}
      </div>
      {isOpen && <div className="pb-2">{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AssetThumbnail — shared thumbnail for characters (circle) and clues (square)
// ---------------------------------------------------------------------------

function AssetThumbnail({
  name,
  sheetPath,
  projectName,
  shape,
  FallbackIcon,
}: {
  name: string;
  sheetPath: string | undefined;
  projectName: string;
  shape: "circle" | "square";
  FallbackIcon: React.ComponentType<{ className?: string }>;
}) {
  const sheetFp = useProjectsStore((s) =>
    sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const [imgError, setImgError] = useState(false);

  useEffect(() => {
    setImgError(false);
  }, [sheetFp, sheetPath]);

  const roundedClass = shape === "circle" ? "rounded-full" : "rounded";

  if (!sheetPath || imgError) {
    return (
      <span className={`flex h-6 w-6 shrink-0 items-center justify-center ${roundedClass} bg-gray-700 text-gray-400`}>
        <FallbackIcon className="h-3.5 w-3.5" />
      </span>
    );
  }

  return (
    <img
      src={API.getFileUrl(projectName, sheetPath, sheetFp)}
      alt={name}
      className={`h-6 w-6 shrink-0 ${roundedClass} object-cover`}
      onError={() => setImgError(true)}
    />
  );
}

// ---------------------------------------------------------------------------
// EmptyState — shared empty placeholder
// ---------------------------------------------------------------------------

function EmptyState({ text }: { text: string }) {
  return (
    <div className="px-8 py-3 text-[11px] italic text-gray-600">
      {text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AssetSidebar
// ---------------------------------------------------------------------------

interface AssetSidebarProps {
  className?: string;
}

export function AssetSidebar({ className }: AssetSidebarProps) {
  const { t } = useTranslation(["common", "dashboard"]);
  const tRef = useRef(t);
  tRef.current = t;
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
    if (!confirm(tRef.current("dashboard:confirm_delete_file", { name: filename }))) return;
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
        className={`flex w-full items-center gap-2 px-3 py-2.5 text-sm transition-colors focus-ring ${
          isActive("/")
            ? "bg-gray-800 text-white"
            : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
        }`}
      >
        <LayoutDashboard className="h-4 w-4 shrink-0 text-indigo-400" />
        <span className="font-medium">{t("dashboard:project_overview")}</span>
      </button>

      {/* ---- Divider ---- */}
      <div className="mx-3 border-t border-gray-800" />

      {/* ---- Section 1: Source Files ---- */}
      <CollapsibleSection
        title={t("dashboard:source_files")}
        icon={FileText}
        action={
          <>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300 focus-ring"
              title={t("dashboard:upload_source_files")}
            >
              <Upload className="h-3.5 w-3.5" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.doc,.docx"
              aria-label={t("dashboard:upload_asset_file_aria")}
              onChange={handleUpload}
              className="hidden"
            />
          </>
        }
      >
        {sourceFiles.length === 0 ? (
          <EmptyState text={t("dashboard:no_files_yet")} />
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
                      className="flex flex-1 items-center gap-2 truncate text-left focus-ring rounded"
                    >
                      <FileText className="h-3.5 w-3.5 shrink-0 text-gray-500" />
                      <span className="truncate">{name}</span>
                    </button>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); handleDeleteFile(name); }}
                      className="shrink-0 rounded p-0.5 text-gray-600 opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100 focus-ring focus-visible:opacity-100"
                      title={t("dashboard:delete_file")}
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
      <CollapsibleSection title={t("dashboard:lorebook")} icon={Users} defaultOpen={true}>
        {/* Characters sub-section */}
        <div className="mb-1">
          <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-gray-600">
            <Users className="h-3 w-3" />
            <span>{t("dashboard:characters")}</span>
          </div>
          {characterEntries.length === 0 ? (
            <EmptyState text={t("dashboard:no_characters_hint")} />
          ) : (
            <ul>
              {characterEntries.map(([name, char]) => (
                <li key={name}>
                  <button
                    type="button"
                    onClick={() => setLocation("/characters")}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors focus-ring ${
                      isActive("/characters")
                        ? "bg-gray-800 text-white"
                        : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
                    }`}
                  >
                    <AssetThumbnail
                      name={name}
                      sheetPath={char.character_sheet}
                      projectName={projectName}
                      shape="circle"
                      FallbackIcon={User}
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
            <span>{t("dashboard:clues")}</span>
          </div>
          {clueEntries.length === 0 ? (
            <EmptyState text={t("dashboard:no_clues_hint")} />
          ) : (
            <ul>
              {clueEntries.map(([name, clue]) => (
                <li key={name}>
                  <button
                    type="button"
                    onClick={() => setLocation("/clues")}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors focus-ring ${
                      isActive("/clues")
                        ? "bg-gray-800 text-white"
                        : "text-gray-300 hover:bg-gray-800/50 hover:text-white"
                    }`}
                  >
                    <AssetThumbnail
                      name={name}
                      sheetPath={clue.clue_sheet}
                      projectName={projectName}
                      shape="square"
                      FallbackIcon={Puzzle}
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
      <CollapsibleSection title={t("dashboard:episodes")} icon={Film}>
        {episodes.length === 0 ? (
          <EmptyState text={t("dashboard:no_episodes_yet")} />
        ) : (
          <ul>
            {episodes.map((ep) => {
              const episodePath = `/episodes/${ep.episode}`;
              const active = isActive(episodePath);
              const isSegmented = ep.script_status === "segmented";
              const statusClass =
                STATUS_DOT_CLASSES[isSegmented ? "draft" : (ep.status ?? "draft")] ??
                STATUS_DOT_CLASSES.draft;

              return (
                <li key={ep.episode}>
                  <button
                    type="button"
                    onClick={() => setLocation(episodePath)}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors focus-ring ${
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
                    {isSegmented && !ep.scenes_count && (
                      <span className="ml-auto shrink-0 rounded bg-indigo-950 px-1.5 py-0.5 text-[10px] text-indigo-400">
                        {t("dashboard:preprocessing")}
                      </span>
                    )}
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
