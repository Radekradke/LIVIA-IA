"use client";

import { useEffect, useSyncExternalStore } from "react";
import { ButtonLink, ButtonPlaceholder } from "@/components/book/Button";
import { ChapterNode } from "@/components/book/ChapterNode";
import { DimensionLine } from "@/components/book/DimensionLine";
import { SectionLabel } from "@/components/book/SectionLabel";
import { SurfaceToggle } from "@/components/book/SurfaceToggle";
import { readCompletion, SERVER_SNAPSHOT } from "@/components/book/reading-progress";
import { chapters, completeDownloadUrl, instagramUrl, totalMinutes } from "@/lib/chapters";

function trackEvent(name: string) {
  console.info(`[reading-event] ${name}`);
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

export function LandingPage() {
  const completion = useSyncExternalStore(
    subscribe,
    () => readCompletion(chapters.length),
    () => SERVER_SNAPSHOT,
  );

  const isRead = (index: number) => completion[index] === "1";
  const next = chapters.find((_, index) => !isRead(index)) ?? chapters[0];
  const started = completion.includes("1");

  useEffect(() => {
    trackEvent("page_view");
  }, []);

  return (
    <div className="page">
      <header className="topbar">
        <div className="shell topbar-inner">
          <a className="brand" href="#conteudo">
            <span className="brand-time" aria-hidden="true">
              17h54
            </span>
            <span>A Distância Entre Dois Encontros</span>
          </a>
          <div className="topbar-actions">
            <nav className="topnav" aria-label="Seções">
              <a href="#historia">A história</a>
              <a href="#capitulos">Capítulos</a>
              <a href="#combinado">O combinado</a>
            </nav>
            <SurfaceToggle />
          </div>
        </div>
      </header>

      <main id="conteudo">
        <section className="shell hero" aria-labelledby="titulo">
          <div className="hero-head u-enter">
            <SectionLabel>Romance · três capítulos abertos</SectionLabel>

            <h1 className="hero-title" id="titulo">
              <span>A distância</span>
              <span className="t-2">entre dois</span>
              <span>encontros</span>
            </h1>

            <DimensionLine
              className="hero-dim"
              value={`≈ ${totalMinutes} minutos de leitura`}
              label="Extensão dos três capítulos"
            />

            <p className="hero-lede">
              Faltavam seis minutos para as seis quando alguém entrou na gráfica
              precisando imprimir um trabalho. <em>Não aconteceu nada. É exatamente
              esse o ponto.</em>
            </p>

            <div className="hero-actions">
              <ButtonLink
                href={next.href}
                variant="primary"
                arrow="→"
                onClick={() => trackEvent(`chapter_${next.number}_started`)}
              >
                {started ? `Continuar no capítulo ${next.number}` : "Começar a ler"}
              </ButtonLink>

              {completeDownloadUrl ? (
                <ButtonLink
                  href={completeDownloadUrl}
                  download
                  variant="quiet"
                  onClick={() => trackEvent("download_all")}
                >
                  ↓ Baixar em PDF
                </ButtonLink>
              ) : null}
            </div>

            <p className="hero-facts">
              <span>Sem cadastro</span>
              <span>Sem e-mail</span>
              <span>Sem paywall</span>
            </p>
          </div>
        </section>

        <section className="section" id="historia" aria-labelledby="historia-titulo">
          <div className="shell split">
            <div>
              <SectionLabel tick={false}>01</SectionLabel>
              <p className="aside-note">
                Nota
                <br />
                do autor
              </p>
            </div>
            <div>
              <h2 className="lead-h" id="historia-titulo">
                Nem toda história começa quando duas pessoas se conhecem.{" "}
                <em>Algumas começam depois.</em>
              </h2>
              <div className="body-copy u-reveal">
                <p>
                  Gustavo atende o balcão da Prisma, a gráfica da família. Numa
                  quinta-feira, seis minutos antes de fechar, entra uma impressão de
                  última hora e, junto com ela, Lívia.
                </p>
                <p>
                  O que vem depois são mensagens que não significam nada até
                  significarem, um sábado num pedalinho e a distância curta e
                  impossível entre estar perto de alguém e dizer isso em voz alta.
                </p>
                <p>
                  Estes três capítulos estão aqui na íntegra, como estão no
                  manuscrito. O livro ainda está sendo escrito — e o que você
                  responder no fim do capítulo 3 muda o que vem depois.
                </p>
                <span className="signature">André Radke</span>
              </div>
            </div>
          </div>
        </section>

        <section className="section" id="capitulos" aria-labelledby="capitulos-titulo">
          <div className="shell split">
            <div>
              <SectionLabel tick={false}>02</SectionLabel>
              <p className="aside-note">
                Os
                <br />
                capítulos
              </p>
            </div>
            <div>
              <h2 className="lead-h" id="capitulos-titulo">
                Três encontros, na ordem em que aconteceram.
              </h2>
              <p className="body-copy">
                A leitura acontece aqui mesmo e a página guarda onde você parou. Cada
                capítulo também tem PDF, se preferir levar.
              </p>
            </div>
          </div>

          <div className="shell chapter-list">
            {chapters.map((chapter, index) => (
              <ChapterNode
                key={chapter.number}
                chapter={chapter}
                isRead={isRead(index)}
                onOpen={() => trackEvent(`chapter_${chapter.number}_started`)}
                onDownload={() => trackEvent(`download_chapter_${chapter.number}`)}
              />
            ))}
          </div>
        </section>

        <section className="section band" aria-labelledby="pdf-titulo">
          <div className="shell split">
            <div>
              <SectionLabel tick={false}>03</SectionLabel>
              <p className="aside-note">
                Leitura
                <br />
                offline
              </p>
            </div>
            <div>
              <h2 id="pdf-titulo">Os três de uma vez, em PDF.</h2>
              <div className="band-body">
                <p>
                  Um arquivo só, com a diagramação do livro. Serve para o Kindle, para
                  o metrô sem sinal ou para imprimir — inclusive numa gráfica.
                </p>
                {completeDownloadUrl ? (
                  <ButtonLink
                    href={completeDownloadUrl}
                    download
                    variant="outline"
                    onClick={() => trackEvent("download_all")}
                  >
                    ↓ Baixar os 3 capítulos
                  </ButtonLink>
                ) : (
                  <ButtonPlaceholder>Arquivo em preparo</ButtonPlaceholder>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="section closing" id="combinado" aria-labelledby="combinado-titulo">
          <div className="shell split">
            <div>
              <SectionLabel tick={false}>04</SectionLabel>
              <p className="aside-note">
                O
                <br />
                combinado
              </p>
            </div>
            <div>
              <h2 id="combinado-titulo">
                Leia sem pressa. No fim, <em>diga se continuaria.</em>
              </h2>
              <p>
                Não é pesquisa de marketing e não existe resposta certa. A pergunta no
                fim do capítulo 3 é literal: eu preciso saber se a história prende, e
                onde ela ainda não prende.
              </p>
              <div className="hero-actions">
                <ButtonLink
                  href={next.href}
                  variant="primary"
                  arrow="→"
                  onClick={() => trackEvent(`chapter_${next.number}_started`)}
                >
                  {started ? `Continuar no capítulo ${next.number}` : "Começar pelo capítulo 1"}
                </ButtonLink>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="shell footer">
        <div>
          <p className="footer-title">A Distância Entre Dois Encontros</p>
          <p>© 2026 André Radke</p>
        </div>
        {instagramUrl ? <a href={instagramUrl}>Instagram ↗</a> : <span>Instagram em breve</span>}
      </footer>
    </div>
  );
}
