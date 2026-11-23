export function aggregateKey(sourceId: number, slug: string): string {
  return `${sourceId}/${slug}`;
}
