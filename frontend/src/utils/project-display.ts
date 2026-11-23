export function getProjectDisplayName(
  rawTitle: string | null | undefined,
  untitledLabel: string,
): string {
  const trimmed = rawTitle?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : untitledLabel;
}
