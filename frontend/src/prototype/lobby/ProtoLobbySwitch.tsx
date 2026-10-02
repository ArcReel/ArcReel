// PROTOTYPE — #2979：/app/projects 按 ?variant= 渲染现状大厅或原型大厅。
import { useEffect } from "react";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import { useProjectsStore } from "@/stores/projects-store";
import { ProtoLobby } from "./ProtoLobby";
import { ProtoWizard } from "./ProtoWizard";
import { setProtoParam, useProtoParams } from "./proto-params";

export function ProtoLobbySwitch() {
  const p = useProtoParams();
  const setShowCreateModal = useProjectsStore((s) => s.setShowCreateModal);
  // 向导选「现状」时，「打开向导」走现状的弹窗
  useEffect(() => {
    if (p.open && p.wizard === "A") {
      setShowCreateModal(true);
      setProtoParam("open", null);
    }
  }, [p.open, p.wizard, setShowCreateModal]);

  if (p.variant !== "A") return <ProtoLobby variant={p.variant} />;
  return (
    <>
      <ProjectsPage />
      {p.open && p.wizard !== "A" && (
        <ProtoWizard variant={p.wizard} height={p.wizardHeight} onClose={() => setProtoParam("open", null)} />
      )}
    </>
  );
}
