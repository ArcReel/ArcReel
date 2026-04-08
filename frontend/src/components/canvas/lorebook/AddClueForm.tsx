import { useState } from "react";
import { X, Loader2 } from "lucide-react";

interface AddClueFormProps {
  onSubmit: (name: string, clueType: string, description: string, importance: string) => Promise<void>;
  onCancel: () => void;
}

export function AddClueForm({ onSubmit, onCancel }: AddClueFormProps) {
  const [name, setName] = useState("");
  const [clueType, setClueType] = useState<"prop" | "location">("prop");
  const [description, setDescription] = useState("");
  const [importance, setImportance] = useState<"major" | "minor">("major");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !description.trim()) return;
    setSubmitting(true);
    try {
      await onSubmit(name.trim(), clueType, description.trim(), importance);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="mt-4 rounded-xl border border-indigo-500/30 bg-gray-900 p-4"
      data-workspace-editing="true"
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-200">Add Clue</h3>
        <button
          type="button"
          onClick={onCancel}
          className="rounded p-1 text-gray-400 hover:bg-gray-800 hover:text-gray-200"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">
            Name <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Clue name"
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500"
            autoFocus
          />
        </div>

        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-400 mb-1">Type</label>
            <div className="flex gap-2">
              <label className={`flex-1 cursor-pointer rounded-lg border px-3 py-1.5 text-center text-xs transition-colors ${
                clueType === "prop"
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                  : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
              }`}>
                <input type="radio" name="clueType" value="prop" checked={clueType === "prop"} onChange={() => setClueType("prop")} className="sr-only" />
                Prop
              </label>
              <label className={`flex-1 cursor-pointer rounded-lg border px-3 py-1.5 text-center text-xs transition-colors ${
                clueType === "location"
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                  : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
              }`}>
                <input type="radio" name="clueType" value="location" checked={clueType === "location"} onChange={() => setClueType("location")} className="sr-only" />
                Location
              </label>
            </div>
          </div>

          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-400 mb-1">Importance</label>
            <div className="flex gap-2">
              <label className={`flex-1 cursor-pointer rounded-lg border px-3 py-1.5 text-center text-xs transition-colors ${
                importance === "major"
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                  : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
              }`}>
                <input type="radio" name="importance" value="major" checked={importance === "major"} onChange={() => setImportance("major")} className="sr-only" />
                Major
              </label>
              <label className={`flex-1 cursor-pointer rounded-lg border px-3 py-1.5 text-center text-xs transition-colors ${
                importance === "minor"
                  ? "border-indigo-500 bg-indigo-500/10 text-indigo-300"
                  : "border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600"
              }`}>
                <input type="radio" name="importance" value="minor" checked={importance === "minor"} onChange={() => setImportance("minor")} className="sr-only" />
                Minor
              </label>
            </div>
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">
            Description <span className="text-red-400">*</span>
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Clue appearance, characteristics, significance, etc..."
            rows={3}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-1.5 text-sm text-gray-200 placeholder-gray-500 outline-none focus:border-indigo-500 resize-none"
          />
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting || !name.trim() || !description.trim()}
            className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Adding...
              </span>
            ) : (
              "Add"
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
