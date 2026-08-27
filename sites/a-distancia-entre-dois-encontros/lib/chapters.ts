export type Chapter = {
  number: 1 | 2 | 3;
  numberLabel: string;
  title: string;
  pages: number;
  minutes: number;
  /** Momento em que o capítulo abre, tirado do próprio texto. */
  anchor: string;
  /** Distância até o capítulo seguinte, medida pela narrativa. */
  gapToNext: string | null;
  description: string[];
  href: string;
  pdf: string | null;
};

export const chapters: Chapter[] = [
  {
    number: 1,
    numberLabel: "01",
    title: "Cinco minutos para fechar",
    pages: 17,
    minutes: 10,
    anchor: "Balcão da Prisma · 17h54",
    gapToNext: "no mesmo dia",
    description: [
      "O começo.",
      "Gustavo trabalha na Prisma quando, pouco depois do horário de fechar, uma garota entra para imprimir um trabalho.",
    ],
    href: "/capitulo-1",
    pdf: "/assets/pdf/capitulo-1-cinco-minutos-para-fechar.pdf",
  },
  {
    number: 2,
    numberLabel: "02",
    title: "O tipo de mensagem que não significa nada",
    pages: 22,
    minutes: 13,
    anchor: "O quarto, à noite",
    gapToNext: "até sábado",
    description: [
      "Às vezes uma conversa começa sem significar absolutamente nada.",
      "Até começar a significar.",
    ],
    href: "/capitulo-2",
    pdf: "/assets/pdf/capitulo-2-o-tipo-de-mensagem-que-nao-significa-nada.pdf",
  },
  {
    number: 3,
    numberLabel: "03",
    title: "O problema dos pedalinhos",
    pages: 29,
    minutes: 16,
    anchor: "Sábado de manhã",
    gapToNext: null,
    description: [
      "Um sábado. Um lago. Um pedalinho.",
      "E a primeira vez em que ficar em silêncio começa a significar alguma coisa.",
    ],
    href: "/capitulo-3",
    pdf: "/assets/pdf/capitulo-3-o-problema-dos-pedalinhos.pdf",
  },
];

export const completeDownloadUrl =
  "/assets/pdf/a-distancia-entre-dois-encontros-primeiros-3-capitulos.pdf";
export const totalMinutes = chapters.reduce((sum, c) => sum + c.minutes, 0);

export const feedbackUrl = "";
export const nextChapterNoticeUrl = "";
export const instagramUrl = "";

export function getChapter(number: number) {
  return chapters.find((chapter) => chapter.number === number);
}
