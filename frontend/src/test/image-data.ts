export function base64OfSize(bytes: number): string {
  return "A".repeat(Math.ceil(bytes / 3) * 4);
}
