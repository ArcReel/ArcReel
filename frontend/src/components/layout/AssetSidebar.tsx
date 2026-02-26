import { useState, useEffect } from "react";
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
} from "lucide-react";
import { useProjectsStore } from "@/stores/projects-store";
import { API } from "@/api";

// ---------------------------------------------------------------------------
// CollapsibleSection — reusable accordion primitive
// ---------------------------------------------------------------------------

function CollapsibleSection({
  title,
  icon: Icon,
  children,
  defaultOpen = true,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-gray-500 transition-colors hover:text-gray-400"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        <Icon className="h-3.5 w-3.5 shrink-0" />
        <span>{title}</span>
      </button>
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
  completed: "text-green-500",
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
    // Fallback: show a placeholder icon
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
  const [location, setLocation] = useLocation();

  const characters = currentProjectData?.characters ?? {};
  const clues = currentProjectData?.clues ?? {};
  const episodes = currentProjectData?.episodes ?? [];
  const projectName = currentProjectName ?? "";

  // 源文件列表
  const [sourceFiles, setSourceFiles] = useState<string[]>([]);

  useEffect(() => {
    if (!projectName) {
      setSourceFiles([]);
      return;
    }
    let cancelled = false;
    API.listFiles(projectName)
      .then((res) => {
        if (cancelled) return;
        // 后端返回 { files: { source: [...], ... } } 或 { files: string[] }
        const raw = res.files as unknown;
        if (Array.isArray(raw)) {
          setSourceFiles(raw);
        } else if (raw && typeof raw === "object") {
          const grouped = raw as Record<string, Array<{ name: string }>>;
          setSourceFiles((grouped.source ?? []).map((f) => f.name));
        }
      })
      .catch(() => {
        if (!cancelled) setSourceFiles([]);
      });
    return () => { cancelled = true; };
  }, [projectName]);

  const characterEntries = Object.entries(characters);
  const clueEntries = Object.entries(clues);

  // Check if a path is active (matches current nested location)
  const isActive = (path: string) => location === path;

  return (
    <aside
      className={`flex flex-col overflow-y-auto bg-gray-900 ${className ?? ""}`}
    >
      {/* ---- Section 1: Source Files ---- */}
      <CollapsibleSection title="源文件" icon={FileText}>
        {sourceFiles.length === 0 ? (
          <EmptyState text="暂无文件" />
        ) : (
          <ul>
            {sourceFiles.map((name) => (
              <li key={name}>
                <div className="flex w-full items-center gap-2 px-3 py-1.5 text-sm text-gray-300">
                  <FileText className="h-3.5 w-3.5 shrink-0 text-gray-500" />
                  <span className="truncate">{name}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CollapsibleSection>

      {/* ---- Divider ---- */}
      <div className="mx-3 border-t border-gray-800" />

      {/* ---- Section 2: Lorebook (Characters + Clues) ---- */}
      <CollapsibleSection title="设定集" icon={Users} defaultOpen={true}>
        {/* Characters sub-section */}
        <div className="mb-1">
          <div className="flex items-center gap-1.5 px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-gray-600">
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
                    onClick={() => setLocation("/lorebook")}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors ${
                      isActive("/lorebook")
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
          <div className="flex items-center gap-1.5 px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-gray-600">
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
                    onClick={() => setLocation("/lorebook")}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm transition-colors ${
                      isActive("/lorebook")
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
