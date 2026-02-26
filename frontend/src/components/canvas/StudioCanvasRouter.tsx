import { Route, Switch } from "wouter";
import { useProjectsStore } from "@/stores/projects-store";
import { LorebookGallery } from "./lorebook/LorebookGallery";
import { TimelineCanvas } from "./timeline/TimelineCanvas";
import { OverviewCanvas } from "./OverviewCanvas";

// ---------------------------------------------------------------------------
// StudioCanvasRouter — reads Zustand store data and renders the correct
// canvas view based on the nested route within /app/projects/:projectName.
// ---------------------------------------------------------------------------

export function StudioCanvasRouter() {
  const { currentProjectData, currentProjectName, currentScripts } =
    useProjectsStore();

  if (!currentProjectName) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        加载中...
      </div>
    );
  }

  return (
    <Switch>
      <Route path="/">
        <OverviewCanvas
          projectName={currentProjectName}
          projectData={currentProjectData}
        />
      </Route>

      <Route path="/lorebook">
        <LorebookGallery
          projectName={currentProjectName}
          characters={currentProjectData?.characters ?? {}}
          clues={currentProjectData?.clues ?? {}}
          onUpdateCharacter={() => {}}
          onUpdateClue={() => {}}
          onGenerateCharacter={() => {}}
          onGenerateClue={() => {}}
        />
      </Route>

      <Route path="/episodes/:episodeId">
        {(params) => {
          const epNum = parseInt(params.episodeId, 10);
          const episode = currentProjectData?.episodes?.find(
            (e) => e.episode === epNum,
          );
          const scriptFile = episode?.script_file;
          const script = scriptFile
            ? (currentScripts[scriptFile] ?? null)
            : null;

          return (
            <TimelineCanvas
              projectName={currentProjectName}
              episodeScript={script}
              projectData={currentProjectData}
            />
          );
        }}
      </Route>
    </Switch>
  );
}
