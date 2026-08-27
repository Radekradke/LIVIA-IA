# A Distância Entre Dois Encontros

Site de leitura dos três primeiros capítulos do romance de André Radke: landing
page e leitor online, com progresso salvo e download em PDF.

## Direção visual

A tese é **"17h54"** — o horário em que o capítulo 1 começa. O site é a noite
depois do expediente; a história é papel. Abrir um capítulo acende a luz: o
leitor abre em superfície clara, a landing fica escura, e uma escolha explícita
do leitor passa a valer para o site inteiro.

- **Acento único:** âmbar de lâmpada. Nada de segunda cor decorativa.
- **Tipografia:** Bodoni Moda (display, eixo `opsz`), Literata (leitura),
  Geist Mono (rótulos, horários, números).
- **Assinatura:** a **linha de cota** (`components/book/DimensionLine.tsx`).
  Ela mede o título no hero, a distância narrativa entre capítulos e o momento
  em que cada capítulo abre. O valor é sempre conteúdo real — nunca enfeite.

## Estrutura

```text
app/
  layout.tsx            fontes, metadata e o script anti-flash de superfície
  page.tsx              landing
  capitulo-[1..3]/      páginas do leitor (server components)
components/
  LandingPage.tsx       composição da home
  ChapterReader.tsx     leitura, progresso e pergunta final
  book/                 primitivos próprios do projeto
lib/
  chapters.ts           metadados dos capítulos
  chapter-content.ts    acesso ao texto integral
  canonical-chapters.generated.ts
styles/                 sistema visual em camadas
public/assets/pdf/      PDFs para download
```

### Primitivos (`components/book/`)

| Arquivo | Papel |
|---|---|
| `Button.tsx` | `ButtonLink`, `Button` e `ButtonPlaceholder`. Variantes por intenção: `primary`, `outline`, `quiet`. |
| `DimensionLine.tsx` | A linha de cota. Recebe `value` (o que se lê) e `label` (o que está sendo medido, para leitores de tela). |
| `SectionLabel.tsx` | Rótulo de seção em mono. |
| `ChapterNode.tsx` | Um capítulo como ponto medido numa linha, não como card. |
| `SurfaceToggle.tsx` | Alterna papel/noite. O rótulo diz o destino, não o estado. |
| `surface.ts` | Estado da superfície + `SURFACE_BOOT_SCRIPT`, o script inline que evita o flash de tema. |
| `reading-progress.ts` | Restaura a posição, salva enquanto rola e marca o capítulo como lido. |

Estado indisponível usa `aria-disabled`, nunca `disabled`, para o controle
continuar alcançável por teclado. Quando não há ação nenhuma, use
`ButtonPlaceholder`, que não é focável.

### Camadas de estilo (`styles/`)

Importadas nesta ordem por `app/globals.css`:

1. `tokens.css` — três níveis: **foundations** (paleta, escala tipográfica,
   espaço, movimento), **semânticos** (`--bg`, `--text`, `--accent`, `--line`…)
   e **tokens de componente** (`--btn-height`, `--node-dot`…).
2. `base.css` — reset, corpo, foco, textura e `prefers-reduced-motion`.
3. `components.css` — os primitivos.
4. `pages.css` — composição da landing e do leitor.

**Regra:** componentes consomem apenas tokens semânticos. Não escreva hex,
espaçamento avulso ou duração de transição direto no CSS de um componente — o
valor entra em `tokens.css` primeiro.

O tema claro é só uma redefinição de tokens em `[data-surface="paper"]`. Para
texto por cima do acento existe `--on-accent`, que troca junto com o tema: use
ele em vez de `--text-inverse` em qualquer coisa preenchida com `--accent`.

## Conteúdo

### Metadados dos capítulos

Em `lib/chapters.ts`: título, páginas, minutos, `anchor` (o momento em que o
capítulo abre, tirado do próprio texto) e `gapToNext` (a distância narrativa
até o próximo, que alimenta a linha de cota).

### Texto integral

O texto vive em `lib/chapter-content.ts`, separado dos metadados **de
propósito**: a landing só precisa dos metadados, e importar o conteúdo junto
colocaria o romance inteiro no bundle da home.

Para regerar a partir dos DOCX canônicos:

```bash
$CODEX_PRIMARY_RUNTIME_PYTHON scripts/extract-canonical-chapters.py \
  /caminho/CAP1.docx /caminho/CAP2.docx /caminho/CAP3.docx \
  lib/canonical-chapters.generated.ts
```

Não altere pontuação, diálogos ou palavras na conversão.

### PDFs

Coloque em `public/assets/pdf/`:

```text
capitulo-1-cinco-minutos-para-fechar.pdf
capitulo-2-o-tipo-de-mensagem-que-nao-significa-nada.pdf
capitulo-3-o-problema-dos-pedalinhos.pdf
a-distancia-entre-dois-encontros-primeiros-3-capitulos.pdf
```

Depois confira `pdf` e `completeDownloadUrl` em `lib/chapters.ts`. Enquanto um
arquivo não existir, a interface mostra "PDF em preparo" em vez de um link
quebrado.

### Imagem de compartilhamento

`public/assets/images/og.png` (1200×630) é gerada a partir do próprio sistema
visual do site. Em produção, defina a variável de ambiente `SITE_URL` (ex.:
`https://seudominio.com.br`) — alguns raspadores, o WhatsApp entre eles, só
aceitam a imagem com URL absoluta.

### Links a preencher

Em `lib/chapters.ts`: `feedbackUrl`, `nextChapterNoticeUrl` e `instagramUrl`.
Vazios, a interface avisa que o canal ainda não foi ligado.

## Rodar localmente

```bash
npm install
npm run dev
```

## Antes de publicar

```bash
npm run build     # build de produção
npm run lint
```

Ao mexer no frontend, verifique também:

- contraste nos **dois** temas (o claro é o mais fácil de quebrar);
- foco visível e ordem de tabulação;
- 390px, 1440px e zoom de 200%;
- `prefers-reduced-motion` sem esconder conteúdo — as revelações por
  `animation-timeline: view()` ficam dentro de `@supports` + `@media`, e nunca
  podem deixar texto invisível quando não animam.

## Publicação no GitHub Pages

O projeto usa rotas do Vinext/Next e está preparado para Sites. Para GitHub
Pages seria preciso exportar uma versão estática das quatro rotas antes de
publicar.

## Analytics

Os eventos passam por `trackEvent()` e aparecem no console. É o ponto único
para conectar Google Analytics, Plausible, Umami ou Cloudflare Web Analytics.
