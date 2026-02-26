import { Zap } from "lucide-react";
import { useAssistantStore } from "@/stores/assistant-store";

export function SkillPills({ onSendCommand }: { onSendCommand: (cmd: string) => void }) {
  const { skills } = useAssistantStore();

  // Filter to show most relevant skills (up to 4)
  const displaySkills = skills.slice(0, 4);

  if (displaySkills.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5 px-3 py-2">
      {displaySkills.map(skill => (
        <button
          key={skill.name}
          onClick={() => onSendCommand(`/${skill.name}`)}
          className="flex items-center gap-1 rounded-full bg-gradient-to-r from-indigo-600/20 to-fuchsia-600/20 px-2.5 py-1 text-[11px] text-indigo-300 transition-colors hover:from-indigo-600/30 hover:to-fuchsia-600/30"
        >
          <Zap className="h-3 w-3" />
          /{skill.name}
        </button>
      ))}
    </div>
  );
}
