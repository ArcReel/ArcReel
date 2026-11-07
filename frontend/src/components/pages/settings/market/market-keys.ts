/** Lookup key for aggregate installation counts and ratings: slug is unique within one source. */
export function aggregateKey(sourceId: number, slug: string): string {
  return `${sourceId}/${slug}`;
}
