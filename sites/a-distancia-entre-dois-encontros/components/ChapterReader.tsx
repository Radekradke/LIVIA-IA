"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ButtonLink, Button } from "@/components/book/Button";
import { DimensionLine } from "@/components/book/DimensionLine";
import { SectionLabel } from "@/components/book/SectionLabel";
import { SurfaceToggle } from "@/components/book/SurfaceToggle";
import { useReadingProgress } from "@/components/book/reading-progress";
import { chapters, feedbackUrl, nextChapterNoticeUrl, type Chapter } from "@/lib/chapters";

function trackEvent(name: string) {
  console.info(`[reading-event] ${name}`);
}

type Answer = "yes" | "no" | null;

export function ChapterReader({
  chapter,
  content,
}: {
  chapter: Chapter;
  content: readonly string[] | null;
}) {
  const progress = useReadingProgress(chapter.number);
  const [answer, setAnswer] = useState<Answer>(null);
  const [shareNote, setShareNote] = useState<string | null>(null);

  const nextChapter = useMemo(
    () => chapters.find((item) => item.number === chapter.number + 1),
    [chapter.number],
  );

  const answerQuestion = (choice: Exclude<Answer, null>) => {
    setAnswer(choice);
    trackEvent(choice === "yes" ? "feedback_yes" : "feedback_no");
  };

  const share = async () => {
    const url = window.location.origin;
    const data = {
      title: "A Distância Entre Dois Encontros",
      text: "Os três primeiros capítulos estão abertos para leitura.",
      url,
    };
    try {
      if (navigator.share) {
        await navigator.share(data);
        return;
      }
      await navigator.clipboard.writeText(url);
      setShareNote("Link copiado.");
    } catch {
      setShareNote(`Copie o endereço: ${url}`);
    }
  };

  return (
    <div className="reader">
      <div className="reader-bar">
        <div
          className="reader-progress"
          style={{ width: `${progress * 100}%` }}
          role="progressbar"
          aria-label={`Progresso no capítulo ${chapter.number}`}
          aria-valuenow={Math.round(progress * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        />
        <div className="shell reader-bar-inner">
          <Link href="/" className="reader-back">
            <span aria-hidden="true">←</span> Todos os capítulos
          </Link>
          <div className="reader-tools">
            <span className="reader-count">
              {chapter.numberLabel} / {String(chapters.length).padStart(2, "0")}
            </span>
            <SurfaceToggle />
          </div>
        </div>
      </div>

      <article className="reader-article">
        <header className="chapter-head">
          <SectionLabel>Capítulo {chapter.number}</SectionLabel>
          <h1>{chapter.title}</h1>
          <DimensionLine
            value={`${chapter.anchor} · ${chapter.minutes} min`}
            label="Onde e quando este capítulo começa"
          />
        </header>

        {content ? (
          <div className="prose">
            {content.map((paragraph, index) =>
              paragraph === "•••" ? (
                <div className="prose-break" key={`b-${index}`} aria-hidden="true" />
              ) : (
                <p key={`${index}-${paragraph.slice(0, 16)}`}>{paragraph}</p>
              ),
            )}
          </div>
        ) : (
          <div className="ask-response" role="note">
            <p>
              O texto integral deste capítulo ainda não foi anexado. A página já está
              pronta para recebê-lo sem alterar nenhuma frase.
            </p>
          </div>
        )}
      </article>

      <section className="reader-end" aria-labelledby="fim-titulo">
        <div className="end-inner">
          <SectionLabel id="fim-titulo">Fim do capítulo {chapter.number}</SectionLabel>

          {nextChapter ? (
            <div>
              {chapter.gapToNext ? (
                <DimensionLine
                  value={chapter.gapToNext}
                  label={`Distância até o capítulo ${nextChapter.number}`}
                />
              ) : null}

              <Link
                href={nextChapter.href}
                className="next-link"
                onClick={() => trackEvent(`chapter_${nextChapter.number}_started`)}
              >
                <span className="u-mono">Capítulo {nextChapter.number} →</span>
                <strong>{nextChapter.title}</strong>
              </Link>

              <div className="node-actions">
                {chapter.pdf ? (
                  <ButtonLink
                    href={chapter.pdf}
                    download
                    variant="quiet"
                    size="sm"
                    onClick={() => trackEvent(`download_chapter_${chapter.number}`)}
                  >
                    ↓ PDF deste capítulo
                  </ButtonLink>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="ask">
              <h2 id="pergunta">Você chegou até aqui.</h2>
              <p className="ask-q">Você leria o capítulo 4?</p>

              <div className="ask-options" role="group" aria-labelledby="pergunta">
                <Button
                  variant={answer === "yes" ? "primary" : "outline"}
                  aria-pressed={answer === "yes"}
                  onClick={() => answerQuestion("yes")}
                >
                  Sim, eu continuaria
                </Button>
                <Button
                  variant={answer === "no" ? "primary" : "outline"}
                  aria-pressed={answer === "no"}
                  onClick={() => answerQuestion("no")}
                >
                  Não me pegou
                </Button>
              </div>

              {answer === "yes" ? (
                <div className="ask-response" role="status">
                  <p>Então o teste funcionou. Obrigado por ter lido até o fim.</p>
                  {nextChapterNoticeUrl ? (
                    <ButtonLink href={nextChapterNoticeUrl} variant="quiet" size="sm">
                      Me avisa quando sair o próximo →
                    </ButtonLink>
                  ) : (
                    <p className="u-mono">O aviso do próximo capítulo ainda será ligado.</p>
                  )}
                </div>
              ) : null}

              {answer === "no" ? (
                <div className="ask-response" role="status">
                  <p>
                    Obrigado por ter chegado até aqui mesmo assim. Se puder dizer em que
                    ponto a história te perdeu, isso vale mais que um elogio.
                  </p>
                  {feedbackUrl ? (
                    <ButtonLink href={feedbackUrl} variant="quiet" size="sm">
                      Contar onde travou →
                    </ButtonLink>
                  ) : (
                    <p className="u-mono">O canal de feedback ainda será conectado.</p>
                  )}
                </div>
              ) : null}

              <div className="node-actions">
                <Button variant="quiet" size="sm" onClick={share}>
                  Compartilhar com alguém →
                </Button>
              </div>

              {shareNote ? (
                <p className="u-mono" role="status">
                  {shareNote}
                </p>
              ) : null}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
