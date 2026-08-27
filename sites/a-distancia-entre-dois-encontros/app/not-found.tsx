import Link from "next/link";
import { ButtonLink } from "@/components/book/Button";
import { DimensionLine } from "@/components/book/DimensionLine";
import { SectionLabel } from "@/components/book/SectionLabel";
import { chapters } from "@/lib/chapters";

export default function NotFound() {
  return (
    <div className="page">
      <header className="topbar">
        <div className="shell topbar-inner">
          <Link className="brand" href="/">
            <span className="brand-time" aria-hidden="true">
              17h54
            </span>
            <span>A Distância Entre Dois Encontros</span>
          </Link>
        </div>
      </header>

      <main className="shell section closing">
        <SectionLabel>Erro 404</SectionLabel>
        <h1 id="erro-titulo">
          Esta página <em>ainda não foi escrita.</em>
        </h1>
        <p>
          O livro tem três capítulos publicados até agora. O endereço que você
          abriu não é um deles.
        </p>

        <DimensionLine
          value={`${chapters.length} capítulos disponíveis`}
          label="O que existe hoje"
        />

        <div className="hero-actions">
          <ButtonLink href="/" variant="primary" arrow="→">
            Voltar para o começo
          </ButtonLink>
          <ButtonLink href={chapters[0].href} variant="quiet">
            Ler o capítulo 1
          </ButtonLink>
        </div>
      </main>
    </div>
  );
}
