import { ApiRequestError } from "@/api";

export function diagnosticCode(err: unknown): string | null {
  if (!(err instanceof ApiRequestError)) return null;
  const diagnostic = err.diagnostic;
  if (diagnostic == null || typeof diagnostic !== "object") return null;
  const code = (diagnostic as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}
