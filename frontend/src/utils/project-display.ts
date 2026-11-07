// Project display-name resolution: use the original value if nonempty after trim; otherwise use the caller's i18n fallback.
// Extract this helper because fallback display recurs in multiple places, and earlier exposure of internal slug-style
// project_name as a display name caused reports when NFKD processing left Chinese titles as pure hex.
export function getProjectDisplayName(
  rawTitle: string | null | undefined,
  untitledLabel: string,
): string {
  const trimmed = rawTitle?.trim();
  return trimmed && trimmed.length > 0 ? trimmed : untitledLabel;
}
