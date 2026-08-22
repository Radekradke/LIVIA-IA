/* Service worker da Livia — o que sobrevive quando o servidor não responde.
 *
 * O QUE "OFFLINE" PODE E NÃO PODE SIGNIFICAR AQUI
 * ------------------------------------------------
 * A inteligência da Livia é Python rodando na sua máquina. Mesmo no modo
 * totalmente local, quem pensa é o servidor — o navegador só desenha. Então
 * NÃO existe "conversar offline": sem o servidor de pé, não há resposta,
 * e nenhum truque de cache muda isso.
 *
 * O que existe, e é o que este arquivo entrega:
 *
 *   1. abrir instantâneo, sem esperar rede, como aplicativo de verdade;
 *   2. LER as conversas, memórias e lições já vistas com o servidor desligado;
 *   3. dizer a verdade quando algo não dá para fazer, em vez de mostrar a
 *      tela de erro do navegador ou — pior — uma casca bonita que falha em
 *      silêncio a cada clique.
 *
 * O item 3 é o motivo de este arquivo ser mais comprido do que o mínimo. Uma
 * PWA mal feita é PIOR que nenhuma: ela abre, parece funcionar, e só quebra
 * quando a pessoa já digitou a pergunta.
 *
 * ESTRATÉGIA POR ROTA
 * -------------------
 *   casca (HTML, ícones)   cache primeiro, atualiza atrás    → abre na hora
 *   GET de leitura         rede primeiro, cache se falhar    → histórico offline
 *   POST, chat, login      só rede, nunca cache              → ver abaixo
 *
 * Por que POST e chat NUNCA passam por aqui: uma resposta de chat servida do
 * cache seria a Livia repetindo uma conversa antiga como se fosse nova. E o
 * /api/chat é um stream SSE — interceptar sem necessidade é a receita
 * conhecida de quebrar streaming. Para essas rotas o worker simplesmente não
 * chama respondWith, e o navegador faz o de sempre.
 */

const VERSAO = "livia-v1";
const CASCA = `${VERSAO}-casca`;
const DADOS = `${VERSAO}-dados`;

// A casca mínima para a tela existir. O HTML é uma página só, então é curto.
const ARQUIVOS_DA_CASCA = [
  "/",
  "/icones/livia.svg",
  "/icones/icone-192.png",
  "/icones/icone-512.png",
  "/manifest.webmanifest",
];

// GETs cujo conteúdo vale guardar para ler sem servidor. São leituras do que
// é SEU: conversas, memórias, skills, lições, experiências.
const LEITURAS = [
  "/api/conversations",
  "/api/store/",
  "/api/experiencias",
  "/api/licoes",
  "/api/candidatas",
  "/api/status",
  "/api/persona",
  "/api/biblioteca",
];

// Nunca tocadas pelo worker. Autenticação e chat têm que falar com o servidor
// de verdade, sempre.
const INTOCAVEIS = ["/api/chat", "/api/entrar", "/entrar", "/sair", "/saude"];

self.addEventListener("install", (evento) => {
  evento.waitUntil(
    caches.open(CASCA)
      .then((c) => c.addAll(ARQUIVOS_DA_CASCA))
      // Um ícone que falhou não pode impedir a instalação inteira.
      .catch(() => caches.open(CASCA).then((c) => c.add("/")))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(
        // Versão nova entra, versões velhas saem. Sem isto, uma atualização
        // da Livia ficaria invisível para sempre atrás de um cache antigo.
        nomes.filter((n) => !n.startsWith(VERSAO)).map((n) => caches.delete(n))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (evento) => {
  if (evento.data === "atualizar") self.skipWaiting();
});

function ehLeitura(url) {
  return LEITURAS.some((p) => url.pathname === p || url.pathname.startsWith(p));
}

function ehIntocavel(url) {
  return INTOCAVEIS.some((p) => url.pathname === p || url.pathname.startsWith(p));
}

self.addEventListener("fetch", (evento) => {
  const pedido = evento.request;
  const url = new URL(pedido.url);

  // Outra origem não é problema nosso.
  if (url.origin !== self.location.origin) return;

  // Só GET. POST, DELETE e PATCH mudam estado no servidor: fingir que deram
  // certo offline criaria uma memória que existe na tela e não no disco.
  if (pedido.method !== "GET") return;

  if (ehIntocavel(url)) return;

  // --- navegação: a casca, sempre ---------------------------------------
  // `mode: navigate` é a pessoa abrindo o app. Servimos o HTML do cache na
  // hora e buscamos a versão nova atrás; ela aparece no próximo abrir.
  if (pedido.mode === "navigate") {
    evento.respondWith(cascaPrimeiro(pedido));
    return;
  }

  if (ehLeitura(url)) {
    evento.respondWith(redePrimeiro(pedido));
    return;
  }

  // Ícones, manifesto e o que mais for estático.
  evento.respondWith(cascaPrimeiro(pedido));
});

async function cascaPrimeiro(pedido) {
  const cache = await caches.open(CASCA);
  const guardado = await cache.match(pedido, { ignoreSearch: true });

  const daRede = fetch(pedido)
    .then((resposta) => {
      // Só guardamos 200. Um redirecionamento para a tela de senha guardado
      // como se fosse a página deixaria o app preso no login para sempre.
      if (resposta && resposta.status === 200 && resposta.type === "basic") {
        cache.put(pedido, resposta.clone());
      }
      return resposta;
    })
    .catch(() => null);

  if (guardado) {
    daRede.catch(() => {});   // atualiza atrás, sem travar a resposta
    return guardado;
  }

  const resposta = await daRede;
  if (resposta) return resposta;

  // Primeira visita, sem rede: não há casca guardada e não há o que inventar.
  return new Response(
    "<!doctype html><meta charset=utf-8><title>Livia</title>" +
    "<body style=\"font-family:system-ui;background:#140a0c;color:#f2e3e0;" +
    "display:grid;place-items:center;height:100vh;margin:0;text-align:center\">" +
    "<div><h1 style=\"font-weight:500\">A Livia não está rodando</h1>" +
    "<p style=\"opacity:.7;line-height:1.6\">O servidor dela é um programa na sua " +
    "máquina.<br>Abra o terminal e rode <code>python run.py</code>.</p></div>",
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
  );
}

async function redePrimeiro(pedido) {
  const cache = await caches.open(DADOS);
  try {
    const resposta = await fetch(pedido);
    if (resposta && resposta.status === 200) {
      cache.put(pedido, resposta.clone());
    }
    return resposta;
  } catch (erro) {
    const guardado = await cache.match(pedido, { ignoreSearch: true });
    if (guardado) {
      // Marcamos a resposta para a interface saber que aquilo é do cache e
      // poder avisar. Devolver dado velho sem dizer que é velho é mentir.
      const copia = guardado.clone();
      const corpo = await copia.text();
      return new Response(corpo, {
        status: 200,
        headers: {
          "Content-Type": guardado.headers.get("Content-Type") || "application/json",
          "X-Livia-Cache": "1",
        },
      });
    }
    return new Response(
      JSON.stringify({
        error: "A Livia não está respondendo. O servidor dela está rodando?",
        offline: true,
      }),
      { status: 503, headers: { "Content-Type": "application/json" } }
    );
  }
}
