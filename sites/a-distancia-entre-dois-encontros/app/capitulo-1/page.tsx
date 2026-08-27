import { ChapterReader } from "@/components/ChapterReader";
import { getChapterContent } from "@/lib/chapter-content";
import { getChapter } from "@/lib/chapters";

export default function Chapter1Page() {
  return <ChapterReader chapter={getChapter(1)!} content={getChapterContent(1)} />;
}
