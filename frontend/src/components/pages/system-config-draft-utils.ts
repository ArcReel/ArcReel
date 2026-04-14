export function mergeServerDraftPreservingDirty<T extends object>(
  currentDraft: T,
  previousSavedDraft: T,
  nextSavedDraft: T,
): T {
  const mergedDraft = { ...nextSavedDraft };

  for (const key of Object.keys(nextSavedDraft) as (keyof T)[]) {
    if (currentDraft[key] !== previousSavedDraft[key]) {
      mergedDraft[key] = currentDraft[key];
    }
  }

  return mergedDraft;
}
