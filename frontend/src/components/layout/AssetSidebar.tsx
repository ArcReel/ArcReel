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
          className="flex flex-1 items-center gap-1.5 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-gray-500 transition-colors hover:text-gray-400 focus-ring rounded"
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
  const sheetFp = useProjectsStore((s) =>
    sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const [imgError, setImgError] = useState(false);

  useEffect(() => {
    setImgError(false);
  }, [sheetFp, sheetPath]);

  if (!sheetPath || imgError) {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-700 text-gray-400">
        <User className="h-3.5 w-3.5" />
      </span>
    );
  }

  return (
    <img
      src={API.getFileUrl(projectName, sheetPath, sheetFp)}
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
  const sheetFp = useProjectsStore((s) =>
    sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const [imgError, setImgError] = useState(false);

  useEffect(() => {
    setImgError(false);
  }, [sheetFp, sheetPath]);

  if (!sheetPath || imgError) {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-gray-700 text-gray-400">
        <Puzzle className="h-3.5 w-3.5" />
      </span>
    );
  }

  return (
    <img
      src={API.getFileUrl(projectName, sheetPath, sheetFp)}
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

  // Source file list
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

  // Upload source file
  const handleUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !projectName) return;
    try {
      await API.uploadFile(projectName, "source", file);
      loadSourceFiles();
      useAppStore.getState().invalidateSourceFiles();
    } catch {
      // Fail silently
    }
    // Reset input to allow selecting the same file again
    e.target.value = "";
  }, [projectName, loadSourceFiles]);

  // Delete source file
  const handleDeleteFile = useCallback(async (filename: string) => {
    if (!projectName) return;
    if (!confirm(`Are you sure you want to delete "${filename}"?`)) return;
    try {
      await API.deleteSourceFile(projectName, filename);
      loadSourceFiles();
      useAppStore.getState().invalidateSourceFiles();
      // If currently viewing this file, return to overview
      if (location === `/source/${encodeURIComponent(filename)}`) {
        setLocation("/");
      }
    } catch {
      // Fail silently
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
        <span className="font-medium">Project Overview</span>
      </button>

      {/* ---- Divider ---- */}
      <div className="mx-3 border-t border-gray-800" />

      {/* ---- Section 1: Source Files ---- */}
      <CollapsibleSection
        title="Source Files"
        icon={FileText}
        action={
          <>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded p-1 text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300 focus-ring"
              title="Upload source file"
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
          <EmptyState text="No files" />
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
                      title="Delete file"
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
      <CollapsibleSection title="Lorebook" icon={Users} defaultOpen={true}>
        {/* Characters sub-section */}
        <div className="mb-1">
          <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-gray-600">
            <Users className="h-3 w-3" />
            <span>Characters</span>
          </div>
          {characterEntries.length === 0 ? (
            <EmptyState text="No characters" />
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
            <span>Clues</span>
          </div>
          {clueEntries.length === 0 ? (
            <EmptyState text="No clues" />
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
      <CollapsibleSection title="Episodes" icon={Film}>
        {episodes.length === 0 ? (
          <EmptyState text="No episodes" />
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
                        Pre-processing
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
