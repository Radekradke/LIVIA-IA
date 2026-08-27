import { canonicalChapters } from "@/lib/canonical-chapters.generated";

/**
 * O texto integral vive fora de `lib/chapters` de propósito: a landing só
 * precisa dos metadados, e importar o conteúdo junto colocaria o romance
 * inteiro no bundle da home.
 */
export function getChapterContent(chapterNumber: number): readonly string[] | null {
  return canonicalChapters[chapterNumber - 1]?.content ?? null;
}
