import { ChapterReader } from "@/components/ChapterReader";
import { getChapterContent } from "@/lib/chapter-content";
import { getChapter } from "@/lib/chapters";

export default function Chapter3Page() {
  return <ChapterReader chapter={getChapter(3)!} content={getChapterContent(3)} />;
}
