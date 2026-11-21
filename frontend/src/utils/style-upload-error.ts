import { ApiRequestError } from "@/api/errors";

export function styleUploadFailureReason(err: unknown, transportFallback: string): string {
  if (!(err instanceof ApiRequestError) || err.status === undefined || err.status < 400 || err.status >= 500) {
    return "";
  }
  const reason = err.message.trim().replace(/[。．.]+$/u, "");
  return reason === transportFallback ? "" : reason;
}
