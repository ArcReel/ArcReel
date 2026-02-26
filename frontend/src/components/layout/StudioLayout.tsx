import { useLocation } from "wouter";
import { GlobalHeader } from "./GlobalHeader";
import { AssetSidebar } from "./AssetSidebar";
import { AgentCopilot } from "@/components/copilot/AgentCopilot";

// ---------------------------------------------------------------------------
// StudioLayout — three-column studio workspace shell
// ---------------------------------------------------------------------------

interface StudioLayoutProps {
  children: React.ReactNode;
}

export function StudioLayout({ children }: StudioLayoutProps) {
  const [, setLocation] = useLocation();

  return (
    <div className="flex h-screen flex-col bg-gray-950 text-gray-100">
      <GlobalHeader onNavigateBack={() => setLocation("/app/projects")} />
      <div className="flex flex-1 overflow-hidden">
        <AssetSidebar className="w-[15%] min-w-50 border-r border-gray-800" />
        <main className="flex-1 overflow-auto">
          {children}
        </main>
        <div className="w-[40%] min-w-90 border-l border-gray-800 bg-gray-900">
          <AgentCopilot />
        </div>
      </div>
    </div>
  );
}
