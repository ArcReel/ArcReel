import { useLocation } from "wouter";
import { Bot } from "lucide-react";
import { GlobalHeader } from "./GlobalHeader";
import { AssetSidebar } from "./AssetSidebar";

// ---------------------------------------------------------------------------
// AgentCopilotPlaceholder — will be replaced by real AgentCopilot in Phase 3
// ---------------------------------------------------------------------------

function AgentCopilotPlaceholder() {
  return (
    <aside className="flex flex-col">
      <div className="flex h-10 items-center border-b border-gray-800 px-4">
        <Bot className="mr-2 h-4 w-4 text-indigo-400" />
        <span className="text-sm font-medium text-gray-300">AI 副驾驶</span>
      </div>
      <div className="flex flex-1 items-center justify-center text-sm text-gray-600">
        Phase 3 将接入完整 AI 助手
      </div>
    </aside>
  );
}

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
          <AgentCopilotPlaceholder />
        </div>
      </div>
    </div>
  );
}
