import type { Metadata, Viewport } from "next";
import { SURFACE_BOOT_SCRIPT } from "@/components/book/surface";

/* Fontes auto-hospedadas (@fontsource) em vez do Google Fonts: mesma origem,
   sem handshake com terceiros e sem CSS externo bloqueando a renderização.
   Cada arquivo traz `unicode-range`, então só o subset latino é baixado.

   Importadas daqui, e não do CSS, para o Vite reescrever as URLs dos .woff2.

   - Bodoni Moda com o eixo `opsz`: a direção depende do contraste mais fino
     nos tamanhos grandes do display.
   - Literata só com `wght`: no corpo de texto o eixo óptico não compensa
     dobrar o peso do arquivo. */
import "@fontsource-variable/bodoni-moda/opsz.css";
import "@fontsource-variable/bodoni-moda/opsz-italic.css";
import "@fontsource-variable/literata/wght.css";
import "@fontsource-variable/literata/wght-italic.css";
import "@fontsource-variable/geist-mono/wght.css";

import "./globals.css";

/* Alguns raspadores (WhatsApp entre eles) exigem URL absoluta na imagem de
   compartilhamento. Defina SITE_URL no ambiente de produção — sem isso a
   imagem continua funcionando na maioria dos lugares, mas não em todos. */
const siteUrl = process.env.SITE_URL;

export const metadata: Metadata = {
  ...(siteUrl ? { metadataBase: new URL(siteUrl) } : {}),
  title: "A Distância Entre Dois Encontros — três capítulos abertos",
  description:
    "Faltavam seis minutos para as seis quando alguém entrou na gráfica precisando imprimir um trabalho. Leia os três primeiros capítulos do romance de André Radke, sem cadastro.",
  openGraph: {
    title: "A Distância Entre Dois Encontros",
    description: "Três capítulos na íntegra, sem cadastro. Uma prova de leitura pública.",
    type: "book",
    locale: "pt_BR",
    images: [
      {
        url: "/assets/images/og.png",
        width: 1200,
        height: 630,
        alt: "A Distância Entre Dois Encontros — três capítulos abertos, cerca de 39 minutos de leitura.",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "A Distância Entre Dois Encontros",
    description: "Três capítulos na íntegra, sem cadastro.",
    images: ["/assets/images/og.png"],
  },
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#080b0f" },
    { media: "(prefers-color-scheme: light)", color: "#080b0f" },
  ],
  colorScheme: "dark light",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR">
      <head>
        <script dangerouslySetInnerHTML={{ __html: SURFACE_BOOT_SCRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
