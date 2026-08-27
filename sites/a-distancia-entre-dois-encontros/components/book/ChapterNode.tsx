import { ButtonLink, ButtonPlaceholder } from "./Button";
import { DimensionLine } from "./DimensionLine";
import type { Chapter } from "@/lib/chapters";

/**
 * Um capítulo como ponto medido numa linha, não como card.
 * O ponto preenchido indica capítulo já lido.
 */
export function ChapterNode({
  chapter,
  isRead,
  onOpen,
  onDownload,
}: {
  chapter: Chapter;
  isRead: boolean;
  onOpen: () => void;
  onDownload: () => void;
}) {
  const titleId = `cap-${chapter.number}-titulo`;

  return (
    <article className="node u-reveal" data-read={isRead} aria-labelledby={titleId}>
      <div className="node-mark">
        <span className="node-dot" aria-hidden="true" />
        <span className="node-index">{chapter.numberLabel}</span>
      </div>

      <div className="node-body">
        <p className="node-anchor">{chapter.anchor}</p>
        <h3 className="node-title" id={titleId}>
          {chapter.title}
        </h3>

        <div className="node-lede">
          {chapter.description.map((line, index) => (
            <p key={line}>{index === 0 ? <em>{line}</em> : line}</p>
          ))}
        </div>

        <div className="node-actions">
          <ButtonLink
            href={chapter.href}
            variant={chapter.number === 1 && !isRead ? "primary" : "outline"}
            size="sm"
            arrow="→"
            onClick={onOpen}
          >
            {isRead ? "Ler de novo" : "Ler capítulo"}
          </ButtonLink>

          {chapter.pdf ? (
            <ButtonLink
              href={chapter.pdf}
              download
              variant="quiet"
              size="sm"
              onClick={onDownload}
            >
              ↓ PDF
            </ButtonLink>
          ) : (
            <ButtonPlaceholder size="sm">PDF em preparo</ButtonPlaceholder>
          )}
        </div>

        {chapter.gapToNext ? (
          <DimensionLine
            className="u-reveal"
            value={chapter.gapToNext}
            label={`Distância até o capítulo ${chapter.number + 1}`}
          />
        ) : null}
      </div>

      <p className="node-meta">
        <span>{chapter.pages} páginas</span>
        <span aria-hidden="true">·</span>
        <span>{chapter.minutes} min</span>
        {isRead ? (
          <>
            <span aria-hidden="true">·</span>
            <span className="node-read-flag">Lido</span>
          </>
        ) : null}
      </p>
    </article>
  );
}
