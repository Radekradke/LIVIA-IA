import { ChapterReader } from "@/components/ChapterReader";
import { getChapterContent } from "@/lib/chapter-content";
import { getChapter } from "@/lib/chapters";

export default function Chapter2Page() {
  return <ChapterReader chapter={getChapter(2)!} content={getChapterContent(2)} />;
}
