/* Nebulosa — o céu de ideias, ao lado da conversa.
 *
 * Um arquivo à parte do index.html porque é uma aplicação inteira: câmera,
 * física, três vistas e uma gaveta. O index continua sendo a casca; isto
 * pendura em <section id="nebulosa"> e conversa com o chat por duas portas:
 *
 *   window.Nebulosa.evento(evt, bolha)  o chat avisa: aqueceu, pescou, mexeu
 *   window.LiviaChat.perguntar(texto)   a Nebulosa pede: fala com a Livia
 *
 * FÍSICA NO NAVEGADOR, GRAFO NO SERVIDOR
 * --------------------------------------
 * O servidor diz quem puxa quem (arestas por semelhança de vetor) e quem
 * forma constelação. A animação roda aqui, em requestAnimationFrame,
 * mexendo o transform direto no DOM. Nada de posição dentro de estado
 * reativo: a 60 quadros por segundo isso seria re-render sem fim. As
 * posições só vão para o banco quando a física para.
 */
"use strict";

(function () {
  const raiz = document.getElementById("nebulosa");
  if (!raiz) return;

  const N = {
    estado: null,
    ideias: new Map(),
    pos: new Map(),          // id -> {x, y, vx, vy}
    vista: "ceu",
    sel: null,
    cam: { x: 0, y: 0, s: 0.85 },
    grav: null,              // último grafo de gravidade
    fisica: null,
    intensidade: parseFloat(localStorage.getItem("neb-intensidade") || "0.45"),
    arquivadas: false,
    selConst: [],
    ligarDe: null,
    sugestoes: [],
    padroes: null,
    matriz: false,
    ultimoAviso: null,
    sistemas: [],            // sistemas solares (categorias)
    selSist: null,
  };

  const $n = (sel) => raiz.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(url, opcoes = {}) {
    const init = { method: opcoes.method || "GET", headers: {} };
    if (opcoes.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opcoes.body);
    }
    const r = await fetch(url, init);
    let dados = {};
    try { dados = await r.json(); } catch { /* corpo vazio */ }
    if (!r.ok) {
      const erro = new Error(dados.error || `Falha (${r.status})`);
      erro.status = r.status;
      erro.dados = dados;
      throw erro;
    }
    return dados;
  }

  const GLIFO = { faisca: "✧", rascunho: "✎", validada: "◆", execucao: "☀", arquivada: "◌" };
  const FAIXA_NOME = { brasa: "em brasa", morna: "morna", esfriando: "esfriando", apagada: "apagada" };
  const EVENTO_NOME = {
    criar: "nasceu", editar: "editada", abrir: "aberta", mencao: "falada no chat",
    ligar: "ligada", gate: "respondeu um gate", promover: "subiu", reviver: "revivida",
    estagio: "mudou de estágio", adotar: "adotada", rebaixar: "desceu",
    nota: "ganhou uma nota", sistema: "mudou de sistema", relatorio: "relatório de trabalho",
  };

  function relativo(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const s = (Date.now() - d) / 1000;
    if (s < 60) return "agora";
    if (s < 3600) return `${Math.floor(s / 60)}min`;
    if (s < 86400) return `${Math.floor(s / 3600)}h`;
    if (s < 86400 * 30) return `${Math.floor(s / 86400)}d`;
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  }

  function nomeEstagio(chave) {
    const e = (N.estado?.estagios || []).find((x) => x.chave === chave);
    return e ? e.nome : chave;
  }

  // ───────────────────────── estrutura ─────────────────────────

  raiz.innerHTML = `
    <div class="neb-topo">
      <div class="neb-marca"><h2>Nebulosa</h2><small id="nebResumo">—</small></div>
      <div class="neb-vistas" role="tablist">
        <button data-vista="ceu" class="ativa">Céu</button>
        <button data-vista="esteira">Esteira</button>
        <button data-vista="fogueira">Fogueira<span id="nebBadge"></span></button>
        <button data-vista="padroes">Padrões</button>
      </div>
      <div class="neb-acoes" id="nebAcoes"></div>
    </div>
    <div class="neb-corpo">
      <div class="neb-vista ativa" data-vista="ceu">
        <div class="ceu" id="ceu" tabindex="0" aria-label="Céu de ideias">
          <canvas class="galaxia" id="galaxia" aria-hidden="true"></canvas>
          <div class="mundo" id="mundo">
            <div class="borda-escura" id="borda"></div>
            <div class="borda-rotulo" id="bordaRotulo">borda escura</div>
            <div id="sistemas"></div>
            <svg class="fios" id="fios" width="1" height="1"></svg>
            <div id="constelacoes"></div>
            <div id="estrelas"></div>
          </div>
        </div>
        <div class="ceu-vazio" id="ceuVazio" hidden>
          <div>
            <h3>Um céu vazio</h3>
            Dê dois cliques em qualquer lugar (ou aperte <kbd>N</kbd>) e escreva a primeira faísca.
            Ou conte uma ideia pra Livia — "tive uma ideia: …" — e ela pesca pra você.
          </div>
        </div>
        <div class="livia-sugere" id="liviaSugere"></div>
        <div class="legenda-temp" aria-hidden="true">
          <span style="--c:var(--t-apagada)"><i></i>apagada</span>
          <span style="--c:var(--t-esfriando)"><i></i>esfriando</span>
          <span style="--c:var(--t-morna)"><i></i>morna</span>
          <span style="--c:var(--t-brasa)"><i></i>em brasa</span>
        </div>
        <div class="ceu-ajuda">duplo clique cria · solte uma ideia num sol pra pôr em órbita · roda dá zoom · <kbd>G</kbd> gravidade</div>
        <div class="ceu-zoom">
          <div class="opac-galaxia" id="opacGal" hidden>
            <span>galáxia</span>
            <input type="range" id="opacGalRange" min="0" max="100" step="5" aria-label="Opacidade da galáxia de fundo">
            <b id="opacGalVal"></b>
          </div>
          <button id="opacGalBtn" title="Opacidade da galáxia de fundo" aria-expanded="false">◐</button>
          <button data-zoom="-1" title="afastar">−</button>
          <button data-zoom="0" title="enquadrar tudo">◎</button>
          <button data-zoom="1" title="aproximar">+</button>
        </div>
      </div>
      <div class="neb-vista" data-vista="esteira"><div id="esteira"></div></div>
      <div class="neb-vista" data-vista="fogueira"><div class="lista-vista" id="fogueira"></div></div>
      <div class="neb-vista" data-vista="padroes"><div class="lista-vista" id="padroes"></div></div>
      <aside class="gaveta" id="gaveta" aria-label="Ideia"></aside>
      <div class="neb-toast" id="nebToast"></div>
    </div>`;

  const ceu = $n("#ceu");
  const mundo = $n("#mundo");
  const fios = $n("#fios");
  const camadaEstrelas = $n("#estrelas");
  const camadaConst = $n("#constelacoes");
  const camadaSist = $n("#sistemas");
  const gaveta = $n("#gaveta");

  // O fundo vivo do céu. Três degraus, do melhor para o mais simples:
  //   1. cosmos.js  — galáxia 3D em Three.js (WebGL2), carregado como módulo;
  //   2. galaxia.js — o shader 2D, para navegador sem WebGL2;
  //   3. o degradê em CSS, se nem isso der.
  // É cenário, não dependência: qualquer degrau que falhe, o céu continua.
  let galaxia = null;
  const telaCeu = $n("#galaxia");

  // Opacidade da galáxia de fundo, escolhida pelo André e lembrada no navegador.
  let opacidadeGalaxia = (() => {
    try { const v = parseInt(localStorage.getItem("neb-opac-galaxia"), 10); return Number.isFinite(v) ? Math.max(0, Math.min(100, v)) : 100; }
    catch { return 100; }
  })();
  function aplicarOpacidade() {
    telaCeu.style.opacity = String(opacidadeGalaxia / 100);
    // Sem galáxia nenhuma, volta o céu em CSS (com a borda escura desenhada).
    ceu.classList.toggle("webgl", Boolean(galaxia) && opacidadeGalaxia > 0);
    $n("#opacGalRange").value = String(opacidadeGalaxia);
    $n("#opacGalVal").textContent = `${opacidadeGalaxia}%`;
    if (galaxia && opacidadeGalaxia > 0 && galaxia.redesenhar) galaxia.redesenhar();
  }
  $n("#opacGalBtn").onclick = (e) => {
    e.stopPropagation();
    const caixa = $n("#opacGal");
    caixa.hidden = !caixa.hidden;
    $n("#opacGalBtn").setAttribute("aria-expanded", String(!caixa.hidden));
  };
  $n("#opacGalRange").oninput = (e) => {
    opacidadeGalaxia = parseInt(e.target.value, 10);
    try { localStorage.setItem("neb-opac-galaxia", String(opacidadeGalaxia)); } catch { /* modo privado */ }
    aplicarOpacidade();
  };
  // O controle mora dentro do céu: sem isto, arrastar o slider arrastaria o céu junto.
  ["pointerdown", "wheel", "dblclick"].forEach((ev) =>
    $n(".ceu-zoom").addEventListener(ev, (e) => e.stopPropagation()));
  function ligarFundo(fabrica) {
    if (galaxia || !fabrica) return Boolean(galaxia);
    try { galaxia = fabrica.criar(telaCeu); } catch (erro) { console.warn("[nebulosa] fundo:", erro); galaxia = null; }
    if (!galaxia) return false;
    ceu.classList.add("webgl");
    // Em 0% de opacidade a galáxia nem desenha: não gasta GPU com o invisível.
    galaxia.quandoVisivel(() =>
      N.vista === "ceu" && opacidadeGalaxia > 0 && !document.body.classList.contains("modo-conversa"));
    aplicarOpacidade();
    aplicarCamera();
    return true;
  }
  if (window.Cosmos) ligarFundo(window.Cosmos);
  else {
    window.addEventListener("cosmos-pronto", () => {
      if (!ligarFundo(window.Cosmos)) ligarFundo(window.Galaxia);
    }, { once: true });
    // O módulo nem carregou (navegador antigo, rede ruim): vai o shader 2D.
    setTimeout(() => { if (!galaxia) ligarFundo(window.Galaxia); }, 5000);
  }

  // ───────────────────────── avisos ─────────────────────────

  let timerToast = null;
  function aviso(texto, acao) {
    const t = $n("#nebToast");
    t.innerHTML = `<span>${texto}</span>`;
    if (acao) {
      const b = document.createElement("button");
      b.textContent = acao.rotulo;
      b.onclick = () => { t.classList.remove("mostra"); acao.fazer(); };
      t.appendChild(b);
    }
    t.classList.add("mostra");
    clearTimeout(timerToast);
    timerToast = setTimeout(() => t.classList.remove("mostra"), acao ? 7000 : 3200);
  }

  function falha(e) {
    aviso(esc(e.message || "Algo deu errado."));
  }

  // ───────────────────────── dados ─────────────────────────

  async function recarregar() {
    let dados;
    try { dados = await api("/api/nebulosa"); } catch { return; }
    N.estado = dados;
    N.ideias = new Map(dados.ideias.map((i) => [i.id, i]));
    for (const i of dados.ideias) {
      const p = N.pos.get(i.id);
      if (!p || (!N.fisica && !p.arrastando)) {
        N.pos.set(i.id, { x: i.x ?? 0, y: i.y ?? 0, vx: 0, vy: 0 });
      }
    }
    for (const id of [...N.pos.keys()]) if (!N.ideias.has(id)) N.pos.delete(id);
    N.sugestoes = dados.sugestoes || [];
    N.sistemas = dados.sistemas || [];
    pintarTopo();
    renderizar();
    if (N.selSist && gaveta.classList.contains("aberta") && !gaveta.contains(document.activeElement)) {
      if (N.sistemas.some((x) => x.id === N.selSist)) abrirSistema(N.selSist); else fecharGaveta();
    }
    if (N.sel && N.ideias.has(N.sel) && gaveta.classList.contains("aberta")) {
      // Só redesenha a gaveta se o André não estiver digitando nela.
      if (!gaveta.contains(document.activeElement)) abrirGaveta(N.sel, { silencioso: true });
    }
  }

  function pintarTopo() {
    const r = N.estado?.resumo;
    if (!r) return;
    const partes = [`${r.vivas} viva${r.vivas === 1 ? "" : "s"}`];
    if (r.por_faixa.brasa) partes.push(`${r.por_faixa.brasa} em brasa`);
    if (r.por_faixa.esfriando + r.por_faixa.apagada)
      partes.push(`${r.por_faixa.esfriando + r.por_faixa.apagada} esfriando`);
    $n("#nebResumo").textContent = partes.join(" · ");

    const pend = (r.sugestoes || 0) + (r.propostas || 0);
    $n("#nebBadge").innerHTML = pend ? `<span class="contador">${pend}</span>` : "";
    document.querySelectorAll(".modos [data-modo='nebulosa']").forEach((b) => {
      let badge = b.querySelector(".badge-neb");
      if (pend && !badge) { badge = document.createElement("span"); badge.className = "badge-neb"; b.appendChild(badge); }
      if (badge) { if (pend) badge.textContent = pend; else badge.remove(); }
    });

    const chip = $n("#liviaSugere");
    if (pend && N.vista === "ceu") {
      chip.innerHTML = `✦ a Livia tem <b>${pend}</b> ${pend === 1 ? "sugestão" : "sugestões"} pra você`;
      chip.classList.add("mostra");
      chip.onclick = () => trocarVista("fogueira");
    } else chip.classList.remove("mostra");
  }

  function renderizar() {
    pintarAcoes();
    if (N.vista === "ceu") renderCeu();
    else if (N.vista === "esteira") renderEsteira();
    else if (N.vista === "fogueira") renderFogueira();
    else if (N.vista === "padroes") renderPadroes();
  }

  // ───────────────────────── vistas e ações do topo ─────────────────────────

  raiz.querySelectorAll(".neb-vistas button").forEach((b) => {
    b.onclick = () => trocarVista(b.dataset.vista);
  });

  function trocarVista(v) {
    N.vista = v;
    raiz.querySelectorAll(".neb-vistas button").forEach((b) => b.classList.toggle("ativa", b.dataset.vista === v));
    raiz.querySelectorAll(".neb-vista").forEach((d) => d.classList.toggle("ativa", d.dataset.vista === v));
    if (v === "fogueira") carregarSugestoes();
    if (v === "padroes") carregarPadroes();
    pintarTopo();
    renderizar();
  }

  function pintarAcoes() {
    const alvo = $n("#nebAcoes");
    if (N.vista === "ceu") {
      const ponte = N.selConst.length === 2
        ? `<button class="neb-btn" id="nbPonte" title="A Livia sugere uma pergunta que liga as duas">✦ Nó-ponte</button>` : "";
      alvo.innerHTML = `
        ${ponte}
        <label class="neb-intensidade" title="Intensidade semântica: à esquerda, agrupamentos amplos; à direita, só o que é muito parecido">
          foco <input type="range" id="nbIntensidade" min="0" max="1" step="0.05" value="${N.intensidade}">
        </label>
        <button class="neb-btn icone ${N.arquivadas ? "ligado" : ""}" id="nbArq" title="${N.arquivadas ? "esconder" : "mostrar"} as arquivadas">◌</button>
        <button class="neb-btn icone" id="nbDesfazer" title="Desfazer o último pulso de gravidade (Ctrl+Z)">↶</button>
        <button class="neb-btn" id="nbSistema" title="Novo sistema solar: uma categoria para as ideias orbitarem">☉ Sistema</button>
        <button class="neb-btn mic-faisca" id="nbVoz" hidden title="Faísca por voz: toque, fale a ideia, toque de novo">🎙</button>
        <button class="neb-btn" id="nbNova" title="Nova faísca no centro (N)">＋ Faísca</button>
        <button class="neb-btn gema ${N.fisica ? "puxando" : ""}" id="nbGrav">Gravidade <kbd>G</kbd></button>`;
      $n("#nbIntensidade").oninput = (e) => {
        N.intensidade = parseFloat(e.target.value);
        localStorage.setItem("neb-intensidade", String(N.intensidade));
      };
      $n("#nbArq").onclick = () => { N.arquivadas = !N.arquivadas; renderizar(); };
      $n("#nbDesfazer").onclick = desfazer;
      $n("#nbNova").onclick = () => rascunhoRapido(centroTela());
      $n("#nbSistema").onclick = novoSistema;
      // Faísca por voz: o que você falar vira ideia direto no centro do céu.
      // É captura, não redação — editar depois é um clique.
      if (window.LiviaVoz?.temMicrofone()) {
        fetch("/api/status").then((r) => r.json()).then((st) => {
          if (!st.voz) return;
          const b = $n("#nbVoz");
          b.hidden = false;
          LiviaVoz.ligarMicrofone(b, async (texto) => {
            LiviaVoz.ultimaFoiVoz = false;   // aqui é captura, não conversa
            const c = centroTela();
            try {
              const { ideia } = await api("/api/nebulosa/ideias", {
                method: "POST", body: { conteudo: texto, x: Math.round(c.x), y: Math.round(c.y), origem: "voz" },
              });
              N.pos.set(ideia.id, { x: c.x, y: c.y, vx: 0, vy: 0 });
              await recarregar();
              pulsar([ideia.id]);
              aviso(`🎙 Faísca <b>#${ideia.id}</b> anotada: “${esc(texto.slice(0, 80))}${texto.length > 80 ? "…" : ""}”`);
            } catch (err) { falha(err); }
          }, { aoMudar: (fase) => { b.textContent = fase === "ouvindo" ? "● ouvindo" : fase === "pensando" ? "…" : "🎙"; } });
        }).catch(() => {});
      }
      $n("#nbGrav").onclick = gravidade;
      if (ponte) $n("#nbPonte").onclick = criarPonte;
    } else if (N.vista === "esteira") {
      alvo.innerHTML = `
        <div class="neb-vistas">
          <button id="nbFunil" class="${N.matriz ? "" : "ativa"}">Funil</button>
          <button id="nbMatriz" class="${N.matriz ? "ativa" : ""}">Esforço × impacto</button>
        </div>`;
      $n("#nbFunil").onclick = () => { N.matriz = false; renderizar(); };
      $n("#nbMatriz").onclick = () => { N.matriz = true; renderizar(); };
    } else if (N.vista === "fogueira") {
      alvo.innerHTML = `<button class="neb-btn" id="nbOlhar">✦ Livia, dá uma olhada</button>`;
      $n("#nbOlhar").onclick = () => carregarSugestoes(true);
    } else {
      alvo.innerHTML = "";
    }
  }

  // ───────────────────────── céu: câmera ─────────────────────────

  function aplicarCamera() {
    mundo.style.transform = `translate(${N.cam.x}px, ${N.cam.y}px) scale(${N.cam.s})`;
    ceu.classList.toggle("lod-longe", N.cam.s < 0.42);
    ceu.classList.toggle("lod-medio", N.cam.s >= 0.42 && N.cam.s < 0.8);
    if (galaxia) galaxia.camera(N.cam, N.estado ? raioBorda() : 0);
  }

  function centrar() {
    const r = ceu.getBoundingClientRect();
    N.cam.x = r.width / 2;
    N.cam.y = r.height / 2;
    aplicarCamera();
  }

  function telaParaMundo(cx, cy) {
    const r = ceu.getBoundingClientRect();
    return { x: (cx - r.left - N.cam.x) / N.cam.s, y: (cy - r.top - N.cam.y) / N.cam.s };
  }

  function centroTela() {
    const r = ceu.getBoundingClientRect();
    return telaParaMundo(r.left + r.width / 2, r.top + r.height / 2);
  }

  function zoomEm(fator, cx, cy) {
    const r = ceu.getBoundingClientRect();
    const px = cx - r.left, py = cy - r.top;
    const novo = Math.min(2.2, Math.max(0.12, N.cam.s * fator));
    N.cam.x = px - (px - N.cam.x) * (novo / N.cam.s);
    N.cam.y = py - (py - N.cam.y) * (novo / N.cam.s);
    N.cam.s = novo;
    aplicarCamera();
  }

  function enquadrar(suave) {
    const vis = visiveis();
    if (!vis.length) { N.cam.s = 0.85; centrar(); return; }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const i of vis) {
      const p = N.pos.get(i.id);
      x0 = Math.min(x0, p.x - 130); x1 = Math.max(x1, p.x + 130);
      y0 = Math.min(y0, p.y - 80); y1 = Math.max(y1, p.y + 80);
    }
    for (const sist of N.sistemas) {
      const ext = geometria(sist).ext;
      x0 = Math.min(x0, sist.x - ext); x1 = Math.max(x1, sist.x + ext);
      y0 = Math.min(y0, sist.y - ext); y1 = Math.max(y1, sist.y + ext);
    }
    const r = ceu.getBoundingClientRect();
    const s = Math.min(1.1, Math.max(0.14, Math.min(r.width / (x1 - x0 + 80), r.height / (y1 - y0 + 80))));
    const alvo = { s, x: r.width / 2 - ((x0 + x1) / 2) * s, y: r.height / 2 - ((y0 + y1) / 2) * s };
    if (!suave || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      Object.assign(N.cam, alvo);
      aplicarCamera();
      return;
    }
    // Câmera desliza até enquadrar: o olho acompanha para onde as ideias foram.
    const de = { ...N.cam };
    const t0 = performance.now();
    const passo = (t) => {
      const k = Math.min(1, (t - t0) / 520);
      const e = 1 - Math.pow(1 - k, 3);
      N.cam.s = de.s + (alvo.s - de.s) * e;
      N.cam.x = de.x + (alvo.x - de.x) * e;
      N.cam.y = de.y + (alvo.y - de.y) * e;
      aplicarCamera();
      if (k < 1) requestAnimationFrame(passo);
    };
    requestAnimationFrame(passo);
  }

  ceu.addEventListener("wheel", (e) => {
    e.preventDefault();
    const intensidade = e.ctrlKey ? 0.012 : 0.0016;   // pinça do trackpad vem como ctrl+roda
    zoomEm(Math.exp(-e.deltaY * intensidade), e.clientX, e.clientY);
  }, { passive: false });

  raiz.querySelectorAll("[data-zoom]").forEach((b) => {
    b.onclick = () => {
      const z = +b.dataset.zoom;
      if (z === 0) { enquadrar(); return; }
      const r = ceu.getBoundingClientRect();
      zoomEm(z > 0 ? 1.25 : 0.8, r.left + r.width / 2, r.top + r.height / 2);
    };
  });

  // ───────────────────────── céu: desenho ─────────────────────────

  function visiveis() {
    return [...N.ideias.values()].filter((i) => N.arquivadas || i.estagio !== "arquivada");
  }

  function raioBorda() {
    const n = visiveis().length;
    return Math.max(900, 260 * Math.sqrt(n + 4));
  }

  const elementos = new Map();   // id -> elemento da estrela

  function htmlEstrela(i) {
    const excerto = (i.conteudo || "").replace(/\s+/g, " ").slice(0, 160);
    const chips = [];
    if (i.proposta) chips.push(`<span class="chip livia">a Livia propõe</span>`);
    if (i.faltando && i.faltando.length && i.proximo)
      chips.push(`<span class="chip falta">faltam ${i.faltando.length}</span>`);
    if (i.n_notas) chips.push(`<span class="chip lua" title="${i.n_notas} nota(s), ${i.n_relatorios || 0} relatório(s)">☾ ${i.n_notas}</span>`);
    // As notas são luas: pontinhos girando em volta do núcleo, cada uma no
    // seu raio e no seu passo. Até seis; daí pra cima o chip conta. Lua de
    // relatório leva a cor da IA que ajudou.
    const luas = (i.luas || []).map((ia, k) =>
      `<b class="${ia ? `ia-${ia}` : ""}" style="--r:${15 + k * 6}px; --f:${(k * 137.5 + i.id * 40) % 360}deg; --v:${(7 + k * 3.1).toFixed(1)}s"></b>`).join("");
    return `
      <div class="estrela-topo">
        <i class="nucleo">${luas ? `<span class="luas">${luas}</span>` : ""}</i><span class="num">#${i.id}</span>
        <span title="${esc(i.estagio_nome)}">${GLIFO[i.estagio] || ""}</span>
        ${i.ancorada ? `<span class="pino" title="ancorada: a gravidade não move">📌</span>` : ""}
        <span class="graus">${Math.round(i.calor)}°</span>
      </div>
      <div class="estrela-titulo">${esc(i.titulo)}</div>
      ${excerto && excerto !== i.titulo ? `<div class="estrela-texto">${esc(excerto)}</div>` : ""}
      ${chips.length ? `<div class="estrela-pe">${chips.join("")}</div>` : ""}`;
  }

  function renderCeu() {
    const vis = visiveis();
    $n("#ceuVazio").hidden = vis.length > 0;
    const vivos = new Set(vis.map((i) => i.id));

    for (const [id, el] of elementos) {
      if (!vivos.has(id)) { el.remove(); elementos.delete(id); }
    }
    for (const i of vis) {
      let el = elementos.get(i.id);
      if (!el) {
        el = document.createElement("div");
        el.dataset.id = i.id;
        camadaEstrelas.appendChild(el);
        elementos.set(i.id, el);
      }
      const classes = ["astro", `e-${i.estagio}`, `f-${i.faixa}`];
      if (i.proposta) classes.push("proposta");
      if (emSistema(i)) classes.push("planeta");
      if (N.sel === i.id) classes.push("selecionada");
      if (el.classList.contains("aquecendo")) classes.push("aquecendo");
      el.className = classes.join(" ");
      el.style.setProperty("--g", (Math.min(100, i.calor) / 100).toFixed(2));
      // Cada estrela cintila no seu tempo; o número da ideia dá a fase.
      el.style.setProperty("--d", `${-((i.id * 1.618) % 6).toFixed(2)}s`);
      el.title = `${i.titulo} — ${i.estagio_nome}, ${FAIXA_NOME[i.faixa]} (${Math.round(i.calor)}°)`;
      const html = htmlEstrela(i);
      if (el._html !== html) { el.innerHTML = html; el._html = html; }
      posicionar(i.id);
    }

    const R = raioBorda();
    const borda = $n("#borda");
    borda.style.width = borda.style.height = `${R * 2}px`;
    borda.style.left = borda.style.top = "0px";
    const rot = $n("#bordaRotulo");
    rot.style.left = "0px";
    rot.style.top = `${R * 0.9}px`;
    if (galaxia) galaxia.camera(N.cam, R);

    renderSistemas();
    desenharFios();
    desenharConstelacoes();
  }

  // ───────────────────────── sistemas solares ─────────────────────────
  //
  // Um sistema é uma categoria. O sol fica parado onde o André pôs; as
  // ideias dele são planetas em órbita; as notas de cada ideia são as luas.
  // Os anéis seguem a temperatura: em brasa gira perto do sol, apagada vai
  // para o anel de fora. A posição de um planeta é calculada aqui, a cada
  // quadro — não é salva, porque é consequência, não decisão.

  const sisEl = new Map();       // id -> {el, t}
  const ANEL = ["brasa", "morna", "esfriando", "apagada"];
  const LARGURA_PLANETA = 290;

  function sistemaDe(id) { return N.sistemas.find((x) => x.id === id) || null; }
  function emSistema(i) { return Boolean(i && i.sistema_id && sistemaDe(i.sistema_id) && i.estagio !== "arquivada"); }

  function geometria(sist) {
    const membros = sist.membros.map((id) => N.ideias.get(id)).filter((i) => i && emSistema(i));
    const aneis = [];
    let r = 230;
    for (const f of ANEL) {
      const ids = membros.filter((i) => i.faixa === f).sort((a, b) => b.calor - a.calor).map((i) => i.id);
      if (!ids.length) continue;
      // O anel cresce até caberem os cartões lado a lado sem encostar.
      r = Math.max(r, (ids.length * LARGURA_PLANETA) / (2 * Math.PI));
      aneis.push({ r, ids, faixa: f });
      r += 175;
    }
    const ext = aneis.length ? aneis[aneis.length - 1].r + 140 : 190;
    return { aneis, ext };
  }

  function renderSistemas() {
    const vivos = new Set(N.sistemas.map((x) => x.id));
    for (const [id, reg] of sisEl) if (!vivos.has(id)) { reg.el.remove(); sisEl.delete(id); }
    for (const sist of N.sistemas) {
      let reg = sisEl.get(sist.id);
      if (!reg) {
        const el = document.createElement("div");
        el.dataset.sistema = sist.id;
        camadaSist.appendChild(el);
        reg = { el, t: 0 };
        sisEl.set(sist.id, reg);
      }
      const geo = geometria(sist);
      reg.geo = geo;
      reg.el.className = `sistema f-${sist.faixa} ${N.selSist === sist.id ? "selecionado" : ""}`;
      reg.el.style.setProperty("--g", (Math.min(100, sist.calor) / 100).toFixed(2));
      reg.el.style.transform = `translate(${sist.x}px, ${sist.y}px)`;
      const html = `
        <div class="halo" style="--R:${geo.ext}px"></div>
        ${geo.aneis.map((a) => `<div class="anel f-${a.faixa}" style="--R:${a.r}px"></div>`).join("")}
        <div class="sol" title="${esc(sist.nome)} — ${sist.membros.length} ideia(s). Clique para abrir; arraste para mover."></div>
        <div class="sol-nome">${esc(sist.nome)}<small>${sist.membros.length} ${sist.membros.length === 1 ? "ideia" : "ideias"}</small></div>`;
      if (reg.html !== html) { reg.el.innerHTML = html; reg.html = html; }
    }
    orbitar(0);
  }

  // Velocidade de Kepler, devagar de propósito: um giro do anel de dentro
  // leva uns dois minutos. É para o céu respirar, não para distrair.
  const omega = (r) => 0.045 * Math.pow(230 / r, 1.5);

  function pausado(sist) {
    if (gesto && (gesto.sid === sist.id || (gesto.id && sist.membros.includes(gesto.id)))) return true;
    if (N.pairando && sist.membros.includes(N.pairando)) return true;
    if (N.selSist === sist.id && gaveta.classList.contains("aberta")) return true;
    return N.sel != null && sist.membros.includes(N.sel) && gaveta.classList.contains("aberta");
  }

  function orbitar(dt) {
    let mexeu = false;
    for (const sist of N.sistemas) {
      const reg = sisEl.get(sist.id);
      if (!reg || !reg.geo) continue;
      if (dt && !pausado(sist)) reg.t += dt;
      reg.geo.aneis.forEach((anel, k) => {
        const base = sist.id * 1.7 + k * 0.9;
        anel.ids.forEach((id, j) => {
          const p = N.pos.get(id);
          if (!p || p.arrastando) return;
          const ang = base + (2 * Math.PI * j) / anel.ids.length + reg.t * omega(anel.r);
          p.x = sist.x + Math.cos(ang) * anel.r;
          p.y = sist.y + Math.sin(ang) * anel.r;
          posicionar(id);
          mexeu = true;
        });
      });
    }
    return mexeu;
  }

  let ultimoQuadro = 0, quadrosFios = 0;
  const parado = window.matchMedia("(prefers-reduced-motion: reduce)");
  function girar(t) {
    const dt = ultimoQuadro ? Math.min(0.1, (t - ultimoQuadro) / 1000) : 0;
    ultimoQuadro = t;
    const visivel = N.vista === "ceu" && !document.hidden && !document.body.classList.contains("modo-conversa");
    if (visivel && N.sistemas.length && !parado.matches && orbitar(dt)) {
      // Fios e rótulos a cada dois quadros: o olho não vê a diferença.
      if (++quadrosFios % 2 === 0) { desenharFios(); desenharConstelacoes(); }
    }
    requestAnimationFrame(girar);
  }
  requestAnimationFrame(girar);

  function sistemaEmbaixo(m, ignorar) {
    let melhor = null, dMin = Infinity;
    for (const sist of N.sistemas) {
      if (sist.id === ignorar) continue;
      const reg = sisEl.get(sist.id);
      const ext = reg?.geo?.ext ?? geometria(sist).ext;
      const d = Math.hypot(m.x - sist.x, m.y - sist.y);
      if (d < ext && d < dMin) { melhor = sist; dMin = d; }
    }
    return melhor;
  }

  function marcarAlvo(sist) {
    for (const [id, reg] of sisEl) reg.el.classList.toggle("alvo", Boolean(sist) && id === sist.id);
  }

  async function novoSistema() {
    try {
      const c = centroTela();
      const { sistema } = await api("/api/nebulosa/sistemas", {
        method: "POST", body: { nome: "Novo sistema", x: Math.round(c.x), y: Math.round(c.y) },
      });
      await recarregar();
      abrirSistema(sistema.id, { nomear: true });
      aviso("Sol aceso. Dê um nome e arraste ideias pra dentro dele.");
    } catch (err) { falha(err); }
  }

  function abrirSistema(sid, opcoes = {}) {
    const sist = sistemaDe(sid);
    if (!sist) return;
    N.sel = null;
    N.selSist = sid;
    elementos.forEach((el) => el.classList.remove("selecionada"));
    const membros = sist.membros.map((id) => N.ideias.get(id)).filter(Boolean).sort((a, b) => b.calor - a.calor);
    gaveta.innerHTML = `
      <div class="gaveta-topo">
        <span class="num">☉</span>
        <span class="chip">sistema · ${FAIXA_NOME[sist.faixa] || ""}</span>
        ${sist.origem && sist.origem.startsWith("livia") ? `<span class="chip livia">a Livia sugeriu</span>` : ""}
        <button class="x" id="gvFechar" title="fechar (Esc)">×</button>
      </div>
      <div class="gaveta-corpo">
        <input class="titulo" id="gsNome" value="${esc(sist.nome)}" placeholder="Nome da categoria">
        <div class="rot">Do que é este sistema</div>
        <textarea id="gsDesc" placeholder="Uma linha pra lembrar o que mora aqui (opcional)">${esc(sist.descricao)}</textarea>
        <div class="temperatura f-${sist.faixa}">
          <span class="graus">${Math.round(sist.calor)}°</span>
          <span class="txt">O sol brilha com as três ideias mais quentes dele. Em brasa orbita perto; apagada vai para o anel de fora.</span>
        </div>
        <div class="rot"><span>Planetas</span><span>${membros.length}</span></div>
        <div class="lista-sistema">
          ${membros.length ? membros.map((i) => linhaIdeia(i, `<button class="mini" data-soltar="${i.id}" title="tirar do sistema">soltar</button>`)).join("")
            : `<p class="hint">Vazio. No céu, arraste uma ideia e solte em cima deste sol.</p>`}
        </div>
        <div class="rot">Ações</div>
        <div class="botoes-linha">
          <button class="neb-btn" id="gsVer">◎ Ver no céu</button>
          <button class="neb-btn" id="gsConversar">💬 Conversar sobre o sistema</button>
          <button class="neb-btn perigo" id="gsApagar">Apagar sistema</button>
        </div>
        <p class="hint">Apagar o sistema não apaga ideia nenhuma: elas ficam soltas em volta de onde o sol estava.</p>
      </div>`;
    gaveta.classList.add("aberta");
    renderSistemas();
    const g = (q) => gaveta.querySelector(q);
    g("#gvFechar").onclick = fecharGaveta;
    const salvar = async () => {
      const nome = g("#gsNome").value.trim(), descricao = g("#gsDesc").value.trim();
      if (!nome || (nome === sist.nome && descricao === (sist.descricao || ""))) return;
      try { await api(`/api/nebulosa/sistemas/${sid}`, { method: "PATCH", body: { nome, descricao } }); await recarregar(); }
      catch (err) { falha(err); }
    };
    g("#gsNome").addEventListener("blur", salvar);
    g("#gsDesc").addEventListener("blur", salvar);
    g("#gsNome").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
    if (opcoes.nomear) { g("#gsNome").focus(); g("#gsNome").select(); }
    gaveta.querySelectorAll(".linha-ideia").forEach((l) => {
      l.onclick = (e) => { if (!e.target.closest("button")) abrirGaveta(+l.dataset.id); };
    });
    gaveta.querySelectorAll("[data-soltar]").forEach((b) => {
      b.onclick = () => moverParaSistema(+b.dataset.soltar, null);
    });
    g("#gsVer").onclick = () => {
      if (N.vista !== "ceu") trocarVista("ceu");
      const ext = sisEl.get(sid)?.geo?.ext || 400;
      const r = ceu.getBoundingClientRect();
      N.cam.s = Math.min(1.1, Math.max(0.18, Math.min(r.width - 380, r.height) / (ext * 2.2)));
      N.cam.x = r.width / 2 - sist.x * N.cam.s - 180;
      N.cam.y = r.height / 2 - sist.y * N.cam.s;
      aplicarCamera();
    };
    g("#gsConversar").onclick = () => falarComLivia(
      `Sobre o sistema «${sist.nome}» (${membros.map((i) => `#${i.id}`).join(", ") || "vazio"}): `, false);
    g("#gsApagar").onclick = async () => {
      if (!confirm(`Apagar o sistema «${sist.nome}»? As ideias ficam, soltas no céu.`)) return;
      try { await api(`/api/nebulosa/sistemas/${sid}`, { method: "DELETE" }); fecharGaveta(); await recarregar(); aviso("Sistema desfeito. As ideias continuam no céu."); }
      catch (err) { falha(err); }
    };
  }

  async function moverParaSistema(id, sid, ponto) {
    try {
      const corpo = { sistema: sid };
      if (sid == null && ponto) { corpo.x = Math.round(ponto.x); corpo.y = Math.round(ponto.y); }
      const { ideia } = await api(`/api/nebulosa/ideias/${id}/sistema`, { method: "POST", body: corpo });
      await recarregar();
      aviso(ideia.sistema_nome ? `#${id} entrou em órbita de <b>${esc(ideia.sistema_nome)}</b>.` : `#${id} está solta no céu.`);
    } catch (err) { falha(err); }
  }

  function posicionar(id) {
    const el = elementos.get(id);
    const p = N.pos.get(id);
    if (el && p) el.style.transform = `translate(${p.x}px, ${p.y}px) translate(-50%, -50%)`;
  }

  function desenharFios() {
    const linhas = [];
    const ligadas = new Set();
    for (const l of N.estado?.ligacoes || []) {
      const a = N.pos.get(l.a), b = N.pos.get(l.b);
      if (!a || !b || !elementos.has(l.a) || !elementos.has(l.b)) continue;
      ligadas.add(`${l.a}-${l.b}`);
      const classe = l.origem === "ponte" ? "fio-ponte" : "fio-manual";
      linhas.push(`<line class="${classe}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`);
    }
    for (const e of N.grav?.arestas || []) {
      if (ligadas.has(`${e.a}-${e.b}`)) continue;
      const a = N.pos.get(e.a), b = N.pos.get(e.b);
      if (!a || !b || !elementos.has(e.a) || !elementos.has(e.b)) continue;
      linhas.push(`<line class="fio-semantico" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`);
    }
    fios.innerHTML = linhas.join("");
  }

  function centroide(membros) {
    let x = 0, y = 0, n = 0, topo = Infinity, raio = 0;
    for (const m of membros) {
      const p = N.pos.get(m);
      if (!p || !elementos.has(m)) continue;
      x += p.x; y += p.y; n++;
      topo = Math.min(topo, p.y);
    }
    if (!n) return null;
    x /= n; y /= n;
    for (const m of membros) {
      const p = N.pos.get(m);
      if (p && elementos.has(m)) raio = Math.max(raio, Math.hypot(p.x - x, p.y - y));
    }
    return { x, y, topo, raio, n };
  }

  function desenharConstelacoes() {
    const lista = N.grav?.constelacoes || [];
    const html = [];
    for (const c of lista) {
      const g = centroide(c.membros);
      // Só rotula o grupo que está de fato junto no céu. Um rótulo flutuando
      // sobre ideias espalhadas mentiria sobre o que a pessoa está vendo.
      if (!g || g.n < 2 || g.raio > 560) continue;
      const sel = N.selConst.includes(c.chave) ? "selecionada" : "";
      html.push(`<div class="constelacao ${c.nomeada ? "" : "provisoria"} ${sel}" data-chave="${esc(c.chave)}"
        style="left:${g.x}px; top:${g.topo - 70}px" title="clique para escolher; dois escolhidos = nó-ponte">${esc(c.nome)}</div>`);
    }
    camadaConst.innerHTML = html.join("");
  }

  // ───────────────────────── céu: interação ─────────────────────────

  let gesto = null;

  ceu.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || pinca) return;   // segundo dedo: quem manda é a pinça
    const estrela = e.target.closest(".astro");
    const constel = e.target.closest(".constelacao");
    const sol = e.target.closest(".sol, .sol-nome");
    fecharRascunho(true);
    if (constel) { escolherConstelacao(constel.dataset.chave); return; }
    ceu.setPointerCapture(e.pointerId);
    if (sol && !estrela) {
      const sid = +sol.closest(".sistema").dataset.sistema;
      const sist = sistemaDe(sid);
      const m = telaParaMundo(e.clientX, e.clientY);
      gesto = { tipo: "sol", sid, dx: m.x - sist.x, dy: m.y - sist.y, x0: e.clientX, y0: e.clientY, moveu: false };
    } else if (estrela) {
      const id = +estrela.dataset.id;
      const p = N.pos.get(id);
      const m = telaParaMundo(e.clientX, e.clientY);
      gesto = { tipo: "estrela", id, el: estrela, dx: m.x - p.x, dy: m.y - p.y, x0: e.clientX, y0: e.clientY, moveu: false };
    } else {
      gesto = { tipo: "pan", x0: e.clientX, y0: e.clientY, cx: N.cam.x, cy: N.cam.y };
      ceu.classList.add("panning");
    }
  });

  // Pinça com dois dedos (celular). Acompanha os toques por fora dos outros
  // gestos: quando o segundo dedo encosta, o arrasto de um dedo é abandonado
  // e a câmera passa a seguir a distância e o ponto médio dos dois.
  const toques = new Map();
  let pinca = null;
  const medio = () => {
    const [a, b] = [...toques.values()];
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, d: Math.hypot(a.x - b.x, a.y - b.y) || 1 };
  };
  ceu.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") return;
    toques.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (toques.size === 2) {
      if (gesto && gesto.tipo === "estrela") {
        const p = N.pos.get(gesto.id);
        if (p) p.arrastando = false;
        gesto.el?.classList.remove("arrastando");
      }
      gesto = null;
      marcarAlvo(null);
      pinca = medio();
    }
  }, true);
  ceu.addEventListener("pointermove", (e) => {
    if (!toques.has(e.pointerId)) return;
    toques.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (!pinca || toques.size !== 2) return;
    const agora = medio();
    N.cam.x += agora.x - pinca.x;
    N.cam.y += agora.y - pinca.y;
    zoomEm(agora.d / pinca.d, agora.x, agora.y);
    pinca = agora;
  }, true);
  const soltarToque = (e) => {
    toques.delete(e.pointerId);
    if (toques.size < 2) pinca = null;
  };
  ceu.addEventListener("pointerup", soltarToque, true);
  ceu.addEventListener("pointercancel", soltarToque, true);

  ceu.addEventListener("pointermove", (e) => {
    if (!gesto) return;
    if (gesto.tipo === "pan") {
      N.cam.x = gesto.cx + (e.clientX - gesto.x0);
      N.cam.y = gesto.cy + (e.clientY - gesto.y0);
      aplicarCamera();
      return;
    }
    if (!gesto.moveu && Math.hypot(e.clientX - gesto.x0, e.clientY - gesto.y0) < 4) return;
    if (N.fisica) return;  // durante o pulso a física manda
    if (gesto.tipo === "sol") {
      gesto.moveu = true;
      const sist = sistemaDe(gesto.sid);
      const m = telaParaMundo(e.clientX, e.clientY);
      sist.x = m.x - gesto.dx; sist.y = m.y - gesto.dy;
      sisEl.get(sist.id).el.style.transform = `translate(${sist.x}px, ${sist.y}px)`;
      orbitar(0); desenharFios(); desenharConstelacoes();
      return;
    }
    gesto.moveu = true;
    gesto.el.classList.add("arrastando");
    const m = telaParaMundo(e.clientX, e.clientY);
    const p = N.pos.get(gesto.id);
    p.x = m.x - gesto.dx; p.y = m.y - gesto.dy; p.arrastando = true;
    const ideia = N.ideias.get(gesto.id);
    marcarAlvo(sistemaEmbaixo(p, emSistema(ideia) ? ideia.sistema_id : null));
    posicionar(gesto.id);
    desenharFios();
    desenharConstelacoes();
  });

  ceu.addEventListener("pointerup", async (e) => {
    const g = gesto;
    gesto = null;
    ceu.classList.remove("panning");
    if (g && g.tipo === "sol") {
      const sist = sistemaDe(g.sid);
      if (!g.moveu) { abrirSistema(g.sid); return; }
      try { await api(`/api/nebulosa/sistemas/${g.sid}`, { method: "PATCH", body: { x: Math.round(sist.x), y: Math.round(sist.y) } }); }
      catch (err) { falha(err); }
      return;
    }
    if (!g || g.tipo !== "estrela") return;
    g.el.classList.remove("arrastando");
    marcarAlvo(null);
    const p = N.pos.get(g.id);
    if (p) p.arrastando = false;
    if (g.moveu) {
      // Soltou num sol: entra em órbita. Era planeta e saiu para além do
      // anel de fora: fica solta onde caiu. Dentro do próprio sistema:
      // volta para a órbita, sem gravar nada.
      const ideia = N.ideias.get(g.id);
      const atual = emSistema(ideia) ? sistemaDe(ideia.sistema_id) : null;
      const alvo = sistemaEmbaixo(p, null);
      if (alvo && (!atual || alvo.id !== atual.id)) { moverParaSistema(g.id, alvo.id); return; }
      if (atual) {
        if (!alvo) moverParaSistema(g.id, null, p);
        return;
      }
      try {
        await api(`/api/nebulosa/ideias/${g.id}`, { method: "PATCH", body: { x: Math.round(p.x), y: Math.round(p.y) } });
      } catch (err) { falha(err); }
      return;
    }
    if (N.ligarDe && N.ligarDe !== g.id) {
      const de = N.ligarDe;
      N.ligarDe = null;
      ceu.style.cursor = "";
      try {
        await api("/api/nebulosa/ligacoes", { method: "POST", body: { a: de, b: g.id } });
        aviso(`Liguei #${de} e #${g.id}.`);
        await recarregar();
      } catch (err) { falha(err); }
      return;
    }
    abrirGaveta(g.id);
  });

  // Passar o mouse num planeta segura a órbita do sistema inteiro: ninguém
  // consegue ler um cartão que está andando.
  ceu.addEventListener("pointerover", (e) => {
    const a = e.target.closest(".astro");
    N.pairando = a ? +a.dataset.id : null;
  });
  ceu.addEventListener("pointerleave", () => { N.pairando = null; });

  ceu.addEventListener("dblclick", (e) => {
    // Com o ponteiro capturado, o alvo do evento vira o próprio céu; quem diz
    // o que está embaixo do cursor é o elementFromPoint.
    const embaixo = document.elementFromPoint(e.clientX, e.clientY);
    if (embaixo && embaixo.closest(".astro, .constelacao, .rascunho-rapido, .sol, .sol-nome")) return;
    rascunhoRapido(telaParaMundo(e.clientX, e.clientY));
  });

  // Rascunho rápido: digitar e soltar a faísca exatamente onde clicou.
  let rascunho = null;

  function rascunhoRapido(m) {
    if (N.vista !== "ceu") trocarVista("ceu");
    fecharRascunho(true);
    const el = document.createElement("div");
    el.className = "rascunho-rapido";
    el.style.left = `${m.x}px`;
    el.style.top = `${m.y}px`;
    // Contra-escala: o rascunho fica legível em qualquer zoom.
    el.style.transform = `translate(-50%, -50%) scale(${(1 / N.cam.s).toFixed(3)})`;
    el.innerHTML = `<textarea placeholder="Qual é a ideia?"></textarea>
      <small>Enter solta no céu · Shift+Enter quebra linha · Esc cancela</small>`;
    mundo.appendChild(el);
    rascunho = { el, m };
    const ta = el.querySelector("textarea");
    ta.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    ta.addEventListener("keydown", async (ev) => {
      if (ev.key === "Escape") { fecharRascunho(true); return; }
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        const texto = ta.value.trim();
        if (!texto) { fecharRascunho(true); return; }
        fecharRascunho(true);
        try {
          const { ideia } = await api("/api/nebulosa/ideias", {
            method: "POST", body: { conteudo: texto, x: Math.round(m.x), y: Math.round(m.y) },
          });
          N.pos.set(ideia.id, { x: m.x, y: m.y, vx: 0, vy: 0 });
          await recarregar();
          pulsar([ideia.id]);
        } catch (err) { falha(err); }
      }
    });
    setTimeout(() => ta.focus(), 0);
  }

  function fecharRascunho() {
    if (rascunho) { rascunho.el.remove(); rascunho = null; }
  }

  function pulsar(ids) {
    for (const id of ids) {
      const el = elementos.get(id);
      if (!el) continue;
      el.classList.remove("aquecendo");
      void el.offsetWidth;
      el.classList.add("aquecendo");
      setTimeout(() => el.classList.remove("aquecendo"), 3400);
    }
  }

  function focar(id) {
    const p = N.pos.get(id);
    if (!p) return;
    const r = ceu.getBoundingClientRect();
    if (N.cam.s < 0.6) N.cam.s = 0.85;
    N.cam.x = r.width / 2 - p.x * N.cam.s - (gaveta.classList.contains("aberta") ? 180 : 0);
    N.cam.y = r.height / 2 - p.y * N.cam.s;
    aplicarCamera();
  }

  function escolherConstelacao(chave) {
    const i = N.selConst.indexOf(chave);
    if (i >= 0) N.selConst.splice(i, 1);
    else { N.selConst.push(chave); if (N.selConst.length > 2) N.selConst.shift(); }
    if (N.selConst.length === 2) aviso("Duas constelações escolhidas — <b>✦ Nó-ponte</b> pede à Livia uma pergunta que ligue as duas.");
    pintarAcoes();
    desenharConstelacoes();
  }

  async function criarPonte() {
    const [ca, cb] = N.selConst.map((k) => (N.grav?.constelacoes || []).find((c) => c.chave === k));
    if (!ca || !cb) return;
    const ga = centroide(ca.membros), gb = centroide(cb.membros);
    aviso("A Livia está pensando numa ponte…");
    try {
      const { ideia } = await api("/api/nebulosa/ponte", {
        method: "POST",
        body: { a: ca.membros, b: cb.membros, x: Math.round((ga.x + gb.x) / 2), y: Math.round((ga.y + gb.y) / 2) },
      });
      N.selConst = [];
      await recarregar();
      pulsar([ideia.id]);
      abrirGaveta(ideia.id);
    } catch (err) { falha(err); }
  }

  // ───────────────────────── gravidade ─────────────────────────

  async function gravidade() {
    if (N.fisica || !visiveis().length) return;
    if (N.vista !== "ceu") trocarVista("ceu");
    N.fisica = { quadro: 0 };
    pintarAcoes();
    let g;
    try {
      g = await api(`/api/nebulosa/gravidade?intensidade=${N.intensidade}`);
      await api("/api/nebulosa/layout", { method: "POST", body: { retrato: true } });
    } catch (err) {
      N.fisica = null; pintarAcoes(); falha(err); return;
    }
    N.grav = g;
    if (g.aviso) aviso(esc(g.aviso));
    // A onda sai do centro junto com o primeiro quadro da física: o espaço
    // entorta, e as ideias começam a cair umas nas outras.
    if (galaxia) galaxia.pulso();
    simular(g);
  }

  function tamanho(i) {
    const w = { faisca: 172, rascunho: 204, validada: 228, execucao: 248 }[i.estagio] || 204;
    const el = elementos.get(i.id);
    // Altura do cartão por inteiro, mesmo que o zoom atual esconda o texto:
    // o layout tem que servir quando o André aproximar.
    const h = Math.max(i.conteudo ? 124 : 70, el && !ceu.classList.contains("lod-longe") && !ceu.classList.contains("lod-medio") ? el.offsetHeight : 0);
    return { w: w + 26, h: h + 22 };
  }

  function simular(g) {
    // Planetas não entram: quem decide onde eles ficam é o sol. Os sóis
    // entram como obstáculos fixos, do tamanho do anel de fora, para a
    // gravidade não jogar ideia solta no meio de um sistema.
    const nos = visiveis().filter((i) => i.estagio !== "arquivada" && !emSistema(i)).map((i) => ({
      i, p: N.pos.get(i.id),
      // Massa = temperatura. A quente pesa e vira centro; a fria é empurrada.
      m: i.ancorada ? Infinity : 1 + i.calor / 18,
      t: tamanho(i),
    }));
    for (const sist of N.sistemas) {
      const ext = geometria(sist).ext;
      nos.push({ i: { id: `sol${sist.id}`, faixa: "brasa", calor: 100 }, p: { x: sist.x, y: sist.y, vx: 0, vy: 0 },
                 m: Infinity, t: { w: ext * 1.7, h: ext * 1.7 }, sol: true });
    }
    const indice = new Map(nos.map((n, k) => [n.i.id, k]));
    const molas = (g.arestas || [])
      .filter((e) => indice.has(e.a) && indice.has(e.b))
      .map((e) => ({ a: indice.get(e.a), b: indice.get(e.b), peso: e.peso }));
    const grupo = new Map();
    (g.constelacoes || []).forEach((c, k) => c.membros.forEach((m) => grupo.set(m, k)));
    const R = raioBorda();
    for (const n of nos) { n.p.vx = 0; n.p.vy = 0; }

    const reduzido = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const MAX = reduzido ? 600 : 420;

    function passo() {
      const F = nos.map(() => ({ x: 0, y: 0 }));

      // Molas: parecidas se puxam até ficarem lado a lado.
      for (const s of molas) {
        const a = nos[s.a].p, b = nos[s.b].p;
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 1;
        const repouso = 230 + (1 - s.peso) * 320;
        const f = 0.012 * (0.5 + s.peso) * (d - repouso);
        F[s.a].x += f * dx / d; F[s.a].y += f * dy / d;
        F[s.b].x -= f * dx / d; F[s.b].y -= f * dy / d;
      }

      for (let x = 0; x < nos.length; x++) {
        const A = nos[x];
        for (let y = x + 1; y < nos.length; y++) {
          const B = nos[y];
          const dx = B.p.x - A.p.x, dy = B.p.y - A.p.y;
          const d2 = Math.max(2500, dx * dx + dy * dy);
          const d = Math.sqrt(d2);
          // Repulsão fraca de longe: grupos diferentes se afastam.
          const mesmo = grupo.has(A.i.id) && grupo.get(A.i.id) === grupo.get(B.i.id);
          const rep = (mesmo ? 3000 : 9000) / d2;
          F[x].x -= rep * dx / d; F[x].y -= rep * dy / d;
          F[y].x += rep * dx / d; F[y].y += rep * dy / d;
          // Colisão: ninguém fica em cima de ninguém.
          const ox = (A.t.w + B.t.w) / 2 - Math.abs(dx);
          const oy = (A.t.h + B.t.h) / 2 - Math.abs(dy);
          if (ox > 0 && oy > 0) {
            if (ox < oy) {
              const s = (dx >= 0 ? 1 : -1) * ox * 0.5;
              F[x].x -= s; F[y].x += s;
            } else {
              const s = (dy >= 0 ? 1 : -1) * oy * 0.5;
              F[x].y -= s; F[y].y += s;
            }
          }
        }
      }

      let maior = 0;
      nos.forEach((n, k) => {
        const r = Math.hypot(n.p.x, n.p.y) || 1;
        if (n.i.faixa === "apagada") {
          // A borda escura: apagada perde o centro e vai para o anel.
          const alvo = R * 0.9;
          F[k].x += 0.02 * (alvo - r) * n.p.x / r; F[k].y += 0.02 * (alvo - r) * n.p.y / r;
        } else {
          const puxa = n.i.faixa === "esfriando" ? 0.001 : 0.0032;
          F[k].x -= puxa * n.p.x; F[k].y -= puxa * n.p.y;
          if (r > R * 0.8) { F[k].x -= 0.05 * (r - R * 0.8) * n.p.x / r; F[k].y -= 0.05 * (r - R * 0.8) * n.p.y / r; }
        }
        if (n.m === Infinity) return;
        n.p.vx = (n.p.vx + F[k].x / n.m) * 0.84;
        n.p.vy = (n.p.vy + F[k].y / n.m) * 0.84;
        const v = Math.hypot(n.p.vx, n.p.vy);
        if (v > 34) { n.p.vx *= 34 / v; n.p.vy *= 34 / v; }
        n.p.x += n.p.vx; n.p.y += n.p.vy;
        maior = Math.max(maior, v);
      });
      return maior;
    }

    function quadro() {
      const iteracoes = reduzido ? 12 : 1;
      let v = 0;
      for (let k = 0; k < iteracoes; k++) { v = passo(); N.fisica.quadro++; }
      if (!reduzido || N.fisica.quadro >= MAX) {
        for (const n of nos) posicionar(n.i.id);
        desenharFios();
        desenharConstelacoes();
      }
      if ((N.fisica.quadro > 50 && v < 0.22) || N.fisica.quadro >= MAX) { terminar(); return; }
      requestAnimationFrame(quadro);
    }

    async function terminar() {
      N.fisica = null;
      pintarAcoes();
      for (const n of nos) posicionar(n.i.id);
      desenharFios();
      desenharConstelacoes();
      try {
        await api("/api/nebulosa/layout", {
          method: "POST",
          body: { posicoes: nos.filter((n) => !n.sol).map((n) => ({ id: n.i.id, x: Math.round(n.p.x), y: Math.round(n.p.y) })) },
        });
      } catch (err) { falha(err); }
      const nc = (g.constelacoes || []).length;
      aviso(nc ? (nc === 1 ? "1 constelação se formou." : `${nc} constelações se formaram.`)
               : "Nada se atraiu muito — puxe o foco para a esquerda e tente de novo.",
        { rotulo: "desfazer", fazer: desfazer });
      enquadrar(true);
      nomearPendentes();
    }

    requestAnimationFrame(quadro);
  }

  async function nomearPendentes() {
    const faltam = (N.grav?.constelacoes || []).filter((c) => !c.nomeada);
    if (!faltam.length) return;
    try {
      const { constelacoes } = await api("/api/nebulosa/constelacoes", {
        method: "POST", body: { grupos: faltam.map((c) => c.membros) },
      });
      for (const nova of constelacoes) {
        const c = N.grav.constelacoes.find((x) => x.chave === nova.chave);
        if (c) { c.nome = nova.nome; c.nomeada = true; }
      }
      desenharConstelacoes();
    } catch { /* nome provisório continua valendo */ }
  }

  async function desfazer() {
    try {
      const { posicoes } = await api("/api/nebulosa/layout/desfazer", { method: "POST" });
      for (const p of posicoes) {
        const atual = N.pos.get(p.id);
        if (atual && p.x != null) { atual.x = p.x; atual.y = p.y; }
      }
      renderCeu();
      aviso("Voltei o céu para antes do pulso.");
    } catch (err) {
      aviso(err.status === 404 ? "Nada para desfazer." : esc(err.message));
    }
  }

  // ───────────────────────── esteira ─────────────────────────

  const FUNIL = ["faisca", "rascunho", "validada", "execucao"];

  function renderEsteira() {
    const alvo = $n("#esteira");
    if (N.matriz) { renderMatriz(alvo); return; }
    const ideias = [...N.ideias.values()];
    const colunas = [...FUNIL, "arquivada"].map((e) => {
      const daqui = ideias.filter((i) => i.estagio === e).sort((a, b) => b.calor - a.calor);
      const info = (N.estado?.estagios || []).find((x) => x.chave === e);
      return `
        <div class="coluna ${e === "arquivada" ? "recolhida" : ""}" data-estagio="${e}">
          <div class="coluna-topo"><span>${GLIFO[e]} ${esc(nomeEstagio(e))}</span><span>${daqui.length}</span></div>
          ${e !== "arquivada" && info ? `<div class="meia-vida">meia-vida: ${info.meia_vida_dias} dias</div>` : ""}
          <div class="coluna-lista">
            ${daqui.map((i) => `
              <div class="cartao f-${i.faixa} ${i.proposta ? "proposta" : ""}" draggable="true" data-id="${i.id}">
                <div class="cartao-titulo">${esc(i.titulo)}</div>
                <div class="termometro"><i style="width:${Math.min(100, i.calor)}%"></i></div>
                <div class="cartao-meta">
                  <span>#${i.id}</span><span>${Math.round(i.calor)}°</span>
                  ${i.faltando?.length && i.proximo ? `<span>faltam ${i.faltando.length}</span>` : ""}
                  ${i.proposta ? "<span>proposta</span>" : ""}
                  ${i.sistema_nome ? `<span class="sist">☉ ${esc(i.sistema_nome)}</span>` : ""}
                  ${i.n_notas ? `<span>☾ ${i.n_notas}</span>` : ""}
                </div>
              </div>`).join("")}
          </div>
        </div>`;
    });
    alvo.className = "esteira";
    alvo.innerHTML = colunas.join("");

    alvo.querySelectorAll(".cartao").forEach((c) => {
      c.onclick = () => abrirGaveta(+c.dataset.id);
      c.ondragstart = (e) => { e.dataTransfer.setData("text/plain", c.dataset.id); e.dataTransfer.effectAllowed = "move"; };
    });
    alvo.querySelectorAll(".coluna").forEach((col) => {
      col.ondragover = (e) => { e.preventDefault(); col.classList.add("alvo"); };
      col.ondragleave = () => col.classList.remove("alvo");
      col.ondrop = (e) => {
        e.preventDefault();
        col.classList.remove("alvo");
        const id = +e.dataTransfer.getData("text/plain");
        if (id) moverEstagio(id, col.dataset.estagio);
      };
    });
  }

  function renderMatriz(alvo) {
    const comNota = [...N.ideias.values()].filter(
      (i) => i.estagio !== "arquivada" && i.gates?.esforco && i.gates?.impacto);
    alvo.className = "matriz";
    if (!comNota.length) {
      alvo.innerHTML = `<div class="none" style="padding-top:80px">
        Nenhuma ideia com esforço e impacto respondidos ainda.<br>
        Eles são perguntas do gate de <b>Validada</b>.</div>`;
      return;
    }
    const pontos = comNota.map((i) => {
      const jitter = ((i.id * 37) % 11 - 5) * 1.1;
      const x = ((+i.gates.esforco - 0.5) / 5) * 100 + jitter;
      const y = ((+i.gates.impacto - 0.5) / 5) * 100 - jitter;
      const tam = 12 + (+(i.gates.empolgacao || 3)) * 5;
      return `<div class="ponto f-${i.faixa}" data-id="${i.id}" title="${esc(i.titulo)}"
        style="left:${x}%; bottom:${y}%; width:${tam}px; height:${tam}px"><span>#${i.id} ${esc(i.titulo.slice(0, 32))}</span></div>`;
    }).join("");
    alvo.innerHTML = `
      <div class="matriz-grade">
        <span class="matriz-quadrante" style="left:12px; top:10px">vitória rápida</span>
        <span class="matriz-quadrante" style="right:12px; top:10px">grande aposta</span>
        <span class="matriz-quadrante" style="left:12px; bottom:10px">passatempo</span>
        <span class="matriz-quadrante" style="right:12px; bottom:10px">armadilha</span>
        <span class="matriz-eixo" style="left:50%; bottom:-28px; transform:translateX(-50%)">esforço →</span>
        <span class="matriz-eixo" style="left:-46px; top:50%; transform:rotate(-90deg)">impacto →</span>
        ${pontos}
      </div>`;
    alvo.querySelectorAll(".ponto").forEach((p) => { p.onclick = () => abrirGaveta(+p.dataset.id); });
  }

  async function moverEstagio(id, destino, motivo) {
    const i = N.ideias.get(id);
    if (!i || i.estagio === destino) return;
    try {
      const { ideia } = await api(`/api/nebulosa/ideias/${id}/estagio`, {
        method: "POST", body: { estagio: destino, motivo: motivo || "" },
      });
      aviso(`#${id} agora está em <b>${esc(ideia.estagio_nome)}</b>.`);
      await recarregar();
      pulsar([id]);
    } catch (err) {
      if (err.status === 409 && err.dados?.gate_faltando) {
        abrirGaveta(id, { destino });
        aviso(`Para ir a ${esc(nomeEstagio(destino))}, responda o que falta.`);
      } else falha(err);
    }
  }

  // ───────────────────────── fogueira ─────────────────────────

  async function carregarSugestoes(forcar) {
    try {
      const { sugestoes } = await api(`/api/nebulosa/sugestoes?gerar=1${forcar ? "&forcar=1" : ""}`);
      N.sugestoes = sugestoes;
      if (forcar) aviso(sugestoes.length ? `${sugestoes.length} sugestão(ões) na mesa.` : "Olhei tudo — nada pedindo atenção agora.");
      await recarregar();
    } catch (err) { falha(err); }
  }

  const SUG = {
    promover: (s) => ["🔥", `Subir <b>#${s.ideia_id} ${esc(s.titulo)}</b>?`],
    arquivar: (s) => ["🌑", `Arquivar <b>#${s.ideia_id} ${esc(s.titulo)}</b>?`],
    juntar: (s) => ["🔗", `Juntar <b>#${s.outra_id} ${esc(s.outra_titulo)}</b> dentro de <b>#${s.ideia_id} ${esc(s.titulo)}</b>?`],
    ligar: (s) => ["✦", `Ligar <b>#${s.ideia_id} ${esc(s.titulo)}</b> com <b>#${s.outra_id} ${esc(s.outra_titulo)}</b>?`],
    reviver: (s) => ["♻", `Reviver <b>#${s.ideia_id} ${esc(s.titulo)}</b>?`],
    sistema: (s) => {
      const membros = (s.dados?.membros || []).map((id) => N.ideias.get(id)).filter(Boolean);
      return ["☉", `Criar o sistema <b>${esc(s.dados?.nome || "novo")}</b> com ${membros.map((i) =>
        `<b>#${i.id}</b> ${esc(i.titulo.slice(0, 28))}`).join(", ")}?`];
    },
  };

  function linhaIdeia(i, botoes = "") {
    const sub = i.estagio === "arquivada"
      ? `arquivada${i.motivo_arquivo ? " — " + esc(i.motivo_arquivo) : ""}`
      : `${esc(i.estagio_nome)} · ${FAIXA_NOME[i.faixa]}` +
        (i.dias_sem_mexer != null
          ? (i.dias_sem_mexer < 1 ? " · mexida hoje" : ` · ${Math.floor(i.dias_sem_mexer)}d sem mexer`) : "") +
        (i.dias_ate_apagar && i.faixa !== "apagada" ? ` · apaga em ~${Math.ceil(i.dias_ate_apagar)}d` : "") +
        (i.sistema_nome ? ` · ☉ ${esc(i.sistema_nome)}` : "") + (i.n_notas ? ` · ☾ ${i.n_notas}` : "") +
        (i.n_relatorios ? ` · ⧉ ${i.n_relatorios} relatório${i.n_relatorios > 1 ? "s" : ""}` : "");
    return `<div class="linha-ideia f-${i.faixa}" data-id="${i.id}">
      <div class="chama">${Math.round(i.calor)}°</div>
      <div class="meio"><div class="t">#${i.id} ${esc(i.titulo)}</div><div class="s">${sub}</div></div>
      <div class="botoes">${botoes}</div>
    </div>`;
  }

  function renderFogueira() {
    const alvo = $n("#fogueira");
    const ideias = [...N.ideias.values()];
    const vivas = ideias.filter((i) => i.estagio !== "arquivada" && !i.proposta);
    const propostas = ideias.filter((i) => i.proposta);
    const porFaixa = (f) => vivas.filter((i) => i.faixa === f).sort((a, b) => b.calor - a.calor);
    const blocos = [];

    if (N.sugestoes.length) {
      blocos.push(`<div class="bloco"><h4><i></i>A Livia sugere</h4>
        ${N.sugestoes.map((s) => {
          const [icone, texto] = (SUG[s.tipo] || (() => ["✦", esc(s.tipo)]))(s);
          return `<div class="sugestao" data-sug="${s.id}">
            <div class="icone">${icone}</div>
            <div class="meio">${texto}<small>${esc(s.motivo)}</small></div>
            <div class="botoes">
              <button class="mini destaque" data-acao="aceitar">sim</button>
              <button class="mini" data-acao="recusar">agora não</button>
            </div></div>`;
        }).join("")}
        <p class="hint">Você decide. Recusar deixa ela quieta sobre isso por 30 dias.</p></div>`);
    }
    if (propostas.length) {
      blocos.push(`<div class="bloco"><h4><i></i>Propostas da Livia</h4>
        ${propostas.map((i) => linhaIdeia(i,
          `<button class="mini destaque" data-adotar="${i.id}">adotar</button>
           <button class="mini" data-descartar="${i.id}">descartar</button>`)).join("")}</div>`);
    }
    const secoes = [
      ["brasa", "Em brasa", "var(--t-brasa)"],
      ["morna", "Mornas", "var(--t-morna)"],
      ["esfriando", "Esfriando", "var(--t-esfriando)"],
      ["apagada", "Borda escura — apagadas", "var(--t-apagada)"],
    ];
    for (const [f, titulo, cor] of secoes) {
      const lista = porFaixa(f);
      if (!lista.length) continue;
      blocos.push(`<div class="bloco" style="--c:${cor}"><h4><i></i>${titulo}</h4>
        ${lista.map((i) => linhaIdeia(i, f === "apagada" || f === "esfriando"
          ? `<button class="mini" data-reviver="${i.id}">reacender</button>` : "")).join("")}</div>`);
    }
    const arquivadas = ideias.filter((i) => i.estagio === "arquivada").slice(0, 30);
    if (arquivadas.length) {
      blocos.push(`<div class="bloco" style="--c:var(--t-apagada)"><h4><i></i>Arquivo</h4>
        ${arquivadas.map((i) => linhaIdeia(i, `<button class="mini" data-reviver="${i.id}">reviver</button>`)).join("")}</div>`);
    }
    alvo.innerHTML = blocos.join("") || `<div class="none">Nada no fogo ainda. Crie uma faísca no Céu.</div>`;

    alvo.querySelectorAll(".linha-ideia").forEach((l) => {
      l.onclick = (e) => { if (!e.target.closest("button")) abrirGaveta(+l.dataset.id); };
    });
    alvo.querySelectorAll("[data-reviver]").forEach((b) => {
      b.onclick = async () => {
        try { await api(`/api/nebulosa/ideias/${b.dataset.reviver}/reviver`, { method: "POST" }); await recarregar(); aviso("Reacendeu. 🔥"); }
        catch (err) { falha(err); }
      };
    });
    alvo.querySelectorAll("[data-adotar]").forEach((b) => { b.onclick = () => adotar(+b.dataset.adotar); });
    alvo.querySelectorAll("[data-descartar]").forEach((b) => { b.onclick = () => apagarIdeia(+b.dataset.descartar, true); });
    alvo.querySelectorAll(".sugestao").forEach((s) => {
      s.querySelectorAll("button").forEach((b) => {
        b.onclick = () => decidirSugestao(+s.dataset.sug, b.dataset.acao);
      });
    });
  }

  async function decidirSugestao(id, acao) {
    const s = N.sugestoes.find((x) => x.id === id);
    try {
      await api(`/api/nebulosa/sugestoes/${id}`, { method: "POST", body: { acao } });
      await recarregar();
      if (acao === "aceitar" && s) pulsar([s.ideia_id]);
    } catch (err) {
      if (err.status === 409 && err.dados?.gate_faltando) {
        abrirGaveta(err.dados.ideia.id, { destino: err.dados.destino });
        aviso("Antes de subir, responda o que falta.");
      } else falha(err);
    }
  }

  async function adotar(id) {
    try {
      await api(`/api/nebulosa/ideias/${id}/adotar`, { method: "POST" });
      await recarregar();
      pulsar([id]);
      aviso(`#${id} agora é sua.`);
    } catch (err) { falha(err); }
  }

  async function apagarIdeia(id, proposta) {
    if (!proposta && !confirm("Apagar esta ideia de vez? (Arquivar guarda ela, apagar não.)")) return;
    try {
      await api(`/api/nebulosa/ideias/${id}`, { method: "DELETE" });
      if (N.sel === id) fecharGaveta();
      await recarregar();
    } catch (err) { falha(err); }
  }

  // ───────────────────────── padrões ─────────────────────────

  async function carregarPadroes() {
    try {
      const dados = await api("/api/nebulosa/padroes");
      N.padroes = dados;
      renderPadroes();
    } catch (err) { falha(err); }
  }

  function renderPadroes() {
    const alvo = $n("#padroes");
    const dados = N.padroes;
    if (!dados) { alvo.innerHTML = `<div class="none">carregando…</div>`; return; }
    if (!dados.padroes.length) {
      alvo.innerHTML = `<div class="bloco"><div class="none">
        Ainda pouca evidência.<br>
        Um padrão precisa de pelo menos <b>${dados.minimo}</b> ideias que já se resolveram
        no funil — subiram, ou foram arquivadas. Conforme você usa, o espelho aparece aqui.</div></div>`;
      return;
    }
    alvo.innerHTML = `<div class="bloco"><h4><i></i>O que o seu funil diz sobre você</h4>
      ${dados.padroes.map((p) => `
        <div class="padrao">
          <div class="t">${esc(p.titulo)}</div>
          <div class="d">${esc(p.descricao)}</div>
          <div class="m">
            <span>base: ${p.evidencia} ideias</span>
            <span class="barra-conf" title="força do padrão"><i style="width:${Math.round(p.confianca * 100)}%"></i></span>
            ${p.ja_licao ? `<span class="tag ok">já é lição</span>`
              : `<button class="mini destaque" data-licao="${esc(p.chave)}">virar lição</button>`}
          </div>
        </div>`).join("")}
      <p class="hint">Virar lição grava em data/lessons/ — a Livia passa a levar isso em conta nas respostas. Dá para editar ou apagar no painel.</p>
      </div>`;
    alvo.querySelectorAll("[data-licao]").forEach((b) => {
      b.onclick = async () => {
        try {
          await api("/api/nebulosa/padroes/licao", { method: "POST", body: { chave: b.dataset.licao } });
          aviso("Virou lição. Ela vai levar isso em conta.");
          carregarPadroes();
          if (typeof carregarDocs === "function") carregarDocs();
        } catch (err) { falha(err); }
      };
    });
  }

  // ───────────────────────── gaveta da ideia ─────────────────────────

  async function abrirGaveta(id, opcoes = {}) {
    let ideia;
    try {
      ({ ideia } = await api(`/api/nebulosa/ideias/${id}${opcoes.silencioso ? "" : "?abrir=1"}`));
    } catch (err) { falha(err); return; }
    N.sel = id;
    N.selSist = null;
    N.ideias.set(id, { ...(N.ideias.get(id) || {}), ...ideia });
    elementos.forEach((el, k) => el.classList.toggle("selecionada", k === id));
    pintarGaveta(ideia, opcoes.destino);
    gaveta.classList.add("aberta");
    if (!opcoes.silencioso && N.vista === "ceu" && elementos.has(id)) focar(id);
  }

  function fecharGaveta() {
    gaveta.classList.remove("aberta");
    N.sel = null;
    if (N.selSist) { N.selSist = null; renderSistemas(); }
    elementos.forEach((el) => el.classList.remove("selecionada"));
  }

  function gatesAte(destino, ideia) {
    // Tudo do caminho até o destino: as já respondidas aparecem para editar,
    // as faltantes aparecem para responder.
    const lista = [];
    const tabela = N.estado?.gates || {};
    const ordemAtual = FUNIL.indexOf(ideia.estagio === "arquivada" ? (ideia.estagio_antes || "faisca") : ideia.estagio);
    const ordemAlvo = FUNIL.indexOf(destino);
    for (let k = 1; k <= Math.max(ordemAlvo, ordemAtual); k++) {
      for (const g of tabela[FUNIL[k]] || []) lista.push({ ...g, estagio: FUNIL[k] });
    }
    return lista;
  }

  function campoGate(g, valor) {
    const ok = valor ? `<span class="ok">✓</span>` : "";
    if (g.tipo === "nota") {
      return `<div class="gate" data-gate="${g.chave}" data-tipo="nota">
        <label>${esc(g.pergunta)}${ok}</label>
        <div class="notas">${[1, 2, 3, 4, 5].map((n) =>
          `<button type="button" data-n="${n}" class="${String(valor) === String(n) ? "sel" : ""}">${n}</button>`).join("")}</div>
      </div>`;
    }
    return `<div class="gate" data-gate="${g.chave}" data-tipo="texto">
      <label>${esc(g.pergunta)}${ok}</label>
      <textarea rows="2">${esc(valor || "")}</textarea>
    </div>`;
  }

  function pintarGaveta(i, destinoPedido) {
    const destino = destinoPedido && FUNIL.includes(destinoPedido) ? destinoPedido : i.proximo;
    const gs = destino || i.estagio === "execucao" ? gatesAte(destino || i.estagio, i) : [];
    const faltam = gs.filter((g) => !(i.gates || {})[g.chave]);
    const ordem = FUNIL.indexOf(i.estagio);
    const arquivada = i.estagio === "arquivada";
    const ligadas = (i.ligadas || []).map((id) => N.ideias.get(id)).filter(Boolean);

    const trilha = FUNIL.map((e, k) => {
      const classe = e === i.estagio ? "aqui" : (k < ordem ? "passou" : "");
      return `<span class="${classe}">${GLIFO[e]} ${esc(nomeEstagio(e))}</span>`;
    }).join("");

    const temperaturaTxt = arquivada
      ? `Arquivada${i.motivo_arquivo ? `: <b>${esc(i.motivo_arquivo)}</b>` : ""}. Reviver devolve ao estágio onde estava.`
      : i.faixa === "apagada"
        ? `<b>Apagada.</b> Foi para a borda escura. Mexer nela ou falar dela no chat reacende.`
        : `<b>${FAIXA_NOME[i.faixa]}</b>. Se ninguém mexer, apaga em ~${Math.ceil(i.dias_ate_apagar || 0)} dia(s). Falar dela comigo também aquece.`;

    gaveta.innerHTML = `
      <div class="gaveta-topo">
        <span class="num">#${i.id}</span>
        <span class="chip">${GLIFO[i.estagio]} ${esc(i.estagio_nome)}</span>
        ${i.origem && i.origem.startsWith("livia") ? `<span class="chip livia">${i.origem === "livia:ponte" ? "nó-ponte" : "veio da conversa"}</span>` : ""}
        <button class="neb-btn" id="gvPino" title="Ancorada não se move na gravidade e puxa as parecidas">${i.ancorada ? "📌 ancorada" : "ancorar"}</button>
        <button class="x lixeira" id="gvLixo" title="apagar esta ideia (Delete)" aria-label="Apagar esta ideia">🗑</button>
        <button class="x" id="gvFechar" title="fechar (Esc)">×</button>
      </div>
      <div class="gaveta-corpo">
        ${i.proposta ? `<div class="livia-faixa" style="margin:0 0 14px">
            <b>A Livia propôs isto.</b> Só passa a valer se você adotar.
            <div class="botoes-linha"><button class="neb-btn gema" id="gvAdotar">Adotar</button>
            <button class="neb-btn" id="gvDescartar">Descartar</button></div></div>` : ""}
        <input class="titulo" id="gvTitulo" value="${esc(i.titulo)}" placeholder="Título">
        <div class="rot">A ideia</div>
        <textarea id="gvConteudo" placeholder="Escreva à vontade…">${esc(i.conteudo)}</textarea>
        <input class="campo" id="gvFonte" value="${esc(i.fonte)}" placeholder="link de referência (opcional)" style="margin-top:8px">
        <label class="campo-sistema">☉ Sistema
          <select id="gvSistema">
            <option value="">— solta no céu —</option>
            ${N.sistemas.map((x) => `<option value="${x.id}" ${x.id === i.sistema_id ? "selected" : ""}>${esc(x.nome)}</option>`).join("")}
          </select>
          ${i.sistema_id ? `<button class="mini" id="gvVerSistema">abrir</button>` : ""}
        </label>

        <div class="temperatura f-${i.faixa}">
          <span class="graus">${Math.round(i.calor)}°</span>
          <span class="txt">${temperaturaTxt}</span>
        </div>

        <div class="trilha">${trilha}</div>

        <div class="rot"><span>Notas e relatórios · as luas</span><span>${(i.notas || []).length || ""}</span></div>
        <div class="notas-lista">
          ${(i.notas || []).map((n) => `<div class="nota ${n.tipo === "relatorio" ? `relatorio ia-${n.ia}` : ""}" data-nota="${n.id}">
              ${n.tipo === "relatorio" ? `<div class="rel-topo"><span class="selo-ia">⧉ ${esc(n.ia_nome)}</span>
                ${n.link ? `<a href="${esc(n.link)}" target="_blank" rel="noopener">abrir conversa ↗</a>` : ""}</div>` : ""}
              <p tabindex="0" title="clique para editar">${esc(n.texto)}</p>
              <small>${relativo(n.criado_em)}${n.origem && n.origem !== "manual" ? ` · ${esc(n.origem.startsWith("livia") ? "da conversa" : n.origem.startsWith("juntada") ? "veio de uma junção" : n.origem)}` : ""}</small>
              <button class="x" data-apagar-nota="${n.id}" title="apagar">×</button>
            </div>`).join("")}
        </div>
        <div class="nova-nota-tipo" role="tablist">
          <button class="ativa" data-tipo-nota="nota">☾ Nota</button>
          <button data-tipo-nota="relatorio" title="Registrar trabalho feito com outra IA">⧉ Relatório</button>
        </div>
        <div class="rel-campos" hidden>
          <div class="ias">
            ${[["claude", "Claude"], ["gemini", "Gemini"], ["gpt", "ChatGPT"], ["outra", "Outra"]].map(([k, nome]) =>
              `<button type="button" class="ia-${k}" data-ia="${k}">${nome}</button>`).join("")}
          </div>
          <input class="campo" id="gvRelLink" placeholder="link da conversa (opcional)">
        </div>
        <div class="nova-nota">
          <textarea id="gvNovaNota" rows="2" placeholder="Um pensamento sobre esta ideia… (Ctrl+Enter)"></textarea>
          <div class="nova-nota-botoes">
            <button class="neb-btn" id="gvResumir" hidden title="A Livia condensa a conversa colada no que foi feito, decidido e o próximo passo">✦ Livia resume</button>
            <button class="neb-btn" id="gvAddNota">☾ Nova lua</button>
          </div>
        </div>

        ${gs.length ? `
          <div class="rot"><span>${destino ? `Para ir a ${esc(nomeEstagio(destino))}` : "Respostas"}</span>
            <span>${faltam.length ? `faltam ${faltam.length}` : "tudo respondido"}</span></div>
          <div id="gvGates">
            ${faltam.map((g) => campoGate(g, "")).join("")}
            ${gs.length > faltam.length ? `
              <details class="respondidas" ${faltam.length ? "" : "open"}>
                <summary>respostas já dadas (${gs.length - faltam.length})</summary>
                ${gs.filter((g) => (i.gates || {})[g.chave]).map((g) => campoGate(g, i.gates[g.chave])).join("")}
              </details>` : ""}
          </div>
          <div class="botoes-linha">
            <button class="neb-btn" id="gvSalvarGates">Salvar respostas</button>
            ${destino ? `<button class="neb-btn gema" id="gvSubir">Salvar e subir → ${esc(nomeEstagio(destino))}</button>` : ""}
          </div>` : ""}

        <div class="livia-faixa">
          <b>Com a Livia</b> — conversar sobre a ideia aquece e deixa ela por dentro.
          <div class="botoes-linha">
            <button class="neb-btn" id="gvConversar">💬 Conversar sobre isso</button>
            ${faltam.length && destino ? `<button class="neb-btn" id="gvEntrevista">🎙 Me entrevista</button>` : ""}
          </div>
        </div>

        <div class="rot">Ações</div>
        <div class="botoes-linha">
          ${!arquivada && ordem > 0 ? `<button class="neb-btn" id="gvDescer">← ${esc(nomeEstagio(FUNIL[ordem - 1]))}</button>` : ""}
          ${arquivada || i.faixa === "apagada"
            ? `<button class="neb-btn" id="gvReviver">♻ ${arquivada ? "Reviver" : "Reacender"}</button>`
            : `<button class="neb-btn" id="gvArquivar">Arquivar</button>`}
          <button class="neb-btn" id="gvLigar">Ligar com…</button>
          <button class="neb-btn perigo" id="gvApagar">Apagar</button>
        </div>

        ${ligadas.length ? `<div class="rot">Ligada a</div><div class="ligadas">${ligadas.map((o) =>
          `<button data-abrir="${o.id}">#${o.id} ${esc(o.titulo.slice(0, 34))}</button>`).join("")}</div>` : ""}

        ${(i.eventos || []).length ? `<div class="rot">Histórico</div><ul class="eventos">${(i.eventos || []).slice(0, 14).map((e) =>
          `<li><span>${relativo(e.criado_em)}</span><span>${esc(rotuloEvento(e))}</span></li>`).join("")}</ul>` : ""}
      </div>`;

    const g = (id) => gaveta.querySelector(id);
    g("#gvFechar").onclick = fecharGaveta;
    g("#gvPino").onclick = () => salvarCampos({ ancorada: !i.ancorada });
    const salvarTexto = () => {
      const titulo = g("#gvTitulo").value.trim(), conteudo = g("#gvConteudo").value, fonte = g("#gvFonte").value.trim();
      if (titulo !== i.titulo || conteudo.trim() !== (i.conteudo || "").trim() || fonte !== (i.fonte || ""))
        salvarCampos({ titulo, conteudo, fonte });
    };
    ["#gvTitulo", "#gvConteudo", "#gvFonte"].forEach((s) => g(s).addEventListener("blur", salvarTexto));
    g("#gvTitulo").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });

    gaveta.querySelectorAll(".gate[data-tipo='nota'] .notas button").forEach((b) => {
      b.onclick = () => {
        b.parentElement.querySelectorAll("button").forEach((x) => x.classList.remove("sel"));
        b.classList.add("sel");
      };
    });
    if (g("#gvSalvarGates")) g("#gvSalvarGates").onclick = () => salvarGates(i, null);
    if (g("#gvSubir")) g("#gvSubir").onclick = () => salvarGates(i, destino);
    if (g("#gvAdotar")) g("#gvAdotar").onclick = () => adotar(i.id);
    if (g("#gvDescartar")) g("#gvDescartar").onclick = () => apagarIdeia(i.id, true);
    if (g("#gvDescer")) g("#gvDescer").onclick = () => moverEstagio(i.id, FUNIL[ordem - 1]);
    if (g("#gvArquivar")) g("#gvArquivar").onclick = () => {
      const motivo = prompt("Por que arquivar? (opcional — vira evidência dos seus padrões)", "");
      if (motivo !== null) moverEstagio(i.id, "arquivada", motivo);
    };
    if (g("#gvReviver")) g("#gvReviver").onclick = async () => {
      try { await api(`/api/nebulosa/ideias/${i.id}/reviver`, { method: "POST" }); await recarregar(); pulsar([i.id]); abrirGaveta(i.id, { silencioso: true }); }
      catch (err) { falha(err); }
    };
    g("#gvLigar").onclick = () => {
      if (N.vista !== "ceu") trocarVista("ceu");
      N.ligarDe = i.id;
      ceu.style.cursor = "crosshair";
      aviso(`Clique na ideia que tem a ver com #${i.id}.`, { rotulo: "cancelar", fazer: () => { N.ligarDe = null; ceu.style.cursor = ""; } });
    };
    g("#gvApagar").onclick = () => apagarIdeia(i.id, false);
    g("#gvLixo").onclick = () => apagarIdeia(i.id, false);
    g("#gvConversar").onclick = () => falarComLivia(`Sobre a ideia #${i.id} «${i.titulo}»: `, false);
    if (g("#gvEntrevista")) g("#gvEntrevista").onclick = () => falarComLivia(
      `Me entrevista pra levar a ideia #${i.id} «${i.titulo}» para ${nomeEstagio(destino)}. ` +
      `Faz as perguntas que faltam, uma de cada vez, e grava as minhas respostas.`, true);
    gaveta.querySelectorAll("[data-abrir]").forEach((b) => { b.onclick = () => abrirGaveta(+b.dataset.abrir); });

    g("#gvSistema").onchange = (e) => moverParaSistema(i.id, e.target.value ? +e.target.value : null);
    if (g("#gvVerSistema")) g("#gvVerSistema").onclick = (e) => { e.preventDefault(); abrirSistema(i.sistema_id); };
    // Nota ou relatório: o mesmo campo de texto, com IA e link a mais.
    let tipoNota = "nota", iaNota = "";
    const pintarTipo = () => {
      gaveta.querySelectorAll("[data-tipo-nota]").forEach((b) => b.classList.toggle("ativa", b.dataset.tipoNota === tipoNota));
      const rel = tipoNota === "relatorio";
      g(".rel-campos").hidden = !rel;
      g("#gvResumir").hidden = !rel;
      g("#gvNovaNota").rows = rel ? 5 : 2;
      g("#gvNovaNota").placeholder = rel
        ? "O que foi feito com a IA — ou cole a conversa inteira e peça pra Livia resumir"
        : "Um pensamento sobre esta ideia… (Ctrl+Enter)";
      g("#gvAddNota").textContent = rel ? "⧉ Salvar relatório" : "☾ Nova lua";
      gaveta.querySelectorAll("[data-ia]").forEach((b) => b.classList.toggle("sel", b.dataset.ia === iaNota));
    };
    gaveta.querySelectorAll("[data-tipo-nota]").forEach((b) => { b.onclick = () => { tipoNota = b.dataset.tipoNota; pintarTipo(); }; });
    gaveta.querySelectorAll("[data-ia]").forEach((b) => { b.onclick = () => { iaNota = b.dataset.ia; pintarTipo(); }; });
    g("#gvResumir").onclick = async () => {
      const texto = g("#gvNovaNota").value.trim();
      if (!texto) { aviso("Cole a conversa no campo primeiro."); return; }
      const botao = g("#gvResumir");
      botao.disabled = true; botao.textContent = "✦ resumindo…";
      try {
        const r = await api("/api/nebulosa/relatorios/resumir", { method: "POST", body: { texto, titulo: i.titulo } });
        g("#gvNovaNota").value = r.texto;
        if (r.ia && !iaNota) iaNota = r.ia;
        pintarTipo();
        aviso(r.resumido ? "Resumi. Revise antes de salvar." : "Não consegui resumir agora — o texto ficou como estava.");
      } catch (err) { falha(err); }
      finally { botao.disabled = false; botao.textContent = "✦ Livia resume"; }
    };
    const addNota = async () => {
      const texto = g("#gvNovaNota").value.trim();
      if (!texto) return;
      if (tipoNota === "relatorio" && !iaNota) { aviso("Escolha com qual IA foi o trabalho."); return; }
      const corpo = tipoNota === "relatorio"
        ? { texto, tipo: "relatorio", ia: iaNota, link: g("#gvRelLink").value.trim() }
        : { texto };
      try {
        await api(`/api/nebulosa/ideias/${i.id}/notas`, { method: "POST", body: corpo });
        await recarregar();
        await abrirGaveta(i.id, { silencioso: true });
        pulsar([i.id]);
      } catch (err) { falha(err); }
    };
    g("#gvAddNota").onclick = addNota;
    g("#gvNovaNota").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addNota(); } });
    gaveta.querySelectorAll(".nota p").forEach((par) => {
      const editar = () => {
        if (par.isContentEditable) return;
        const antes = par.textContent;
        par.contentEditable = "true";
        par.focus();
        par.onblur = async () => {
          par.contentEditable = "false";
          const texto = par.textContent.trim();
          if (!texto || texto === antes) { par.textContent = antes; return; }
          try { await api(`/api/nebulosa/notas/${par.parentElement.dataset.nota}`, { method: "PATCH", body: { texto } }); }
          catch (err) { par.textContent = antes; falha(err); }
        };
      };
      par.onclick = editar;
      par.onkeydown = (e) => { if (e.key === "Enter" && !par.isContentEditable) { e.preventDefault(); editar(); } };
    });
    gaveta.querySelectorAll("[data-apagar-nota]").forEach((b) => {
      b.onclick = async () => {
        try {
          await api(`/api/nebulosa/notas/${b.dataset.apagarNota}`, { method: "DELETE" });
          await recarregar();
          abrirGaveta(i.id, { silencioso: true });
        } catch (err) { falha(err); }
      };
    });
  }

  function rotuloEvento(e) {
    let extra = "";
    try {
      const d = JSON.parse(e.detalhe || "{}");
      if (e.tipo === "estagio" && d.para) extra = ` → ${nomeEstagio(d.para)}`;
      if (e.tipo === "gate" && d.gate) extra = `: ${d.gate}${d.origem === "livia:conversa" ? " (na conversa)" : ""}`;
      if (e.tipo === "criar" && d.origem && d.origem !== "manual") extra = ` (${d.origem.replace("livia:", "livia · ")})`;
      if (e.tipo === "sistema") extra = d.nome ? ` → ☉ ${d.nome}` : " → solta no céu";
      if (e.tipo === "relatorio" && d.ia) extra = ` (${{ claude: "Claude", gemini: "Gemini", gpt: "ChatGPT" }[d.ia] || "outra IA"})`;
    } catch { /* detalhe livre */ }
    return (EVENTO_NOME[e.tipo] || e.tipo) + extra;
  }

  async function salvarCampos(corpo) {
    try {
      await api(`/api/nebulosa/ideias/${N.sel}`, { method: "PATCH", body: corpo });
      await recarregar();
      if (corpo.ancorada !== undefined) abrirGaveta(N.sel, { silencioso: true });
    } catch (err) { falha(err); }
  }

  async function salvarGates(i, destino) {
    const respostas = {};
    gaveta.querySelectorAll(".gate").forEach((el) => {
      const chave = el.dataset.gate;
      const valor = el.dataset.tipo === "nota"
        ? (el.querySelector(".notas .sel")?.dataset.n || "")
        : el.querySelector("textarea").value.trim();
      if (valor !== String((i.gates || {})[chave] || "")) respostas[chave] = valor;
    });
    try {
      const { ideia } = await api(`/api/nebulosa/ideias/${i.id}/gates`, {
        method: "POST", body: { respostas, promover_para: destino || "" },
      });
      await recarregar();
      if (destino) { pulsar([i.id]); aviso(`#${i.id} subiu para <b>${esc(ideia.estagio_nome)}</b>. 🔥`); }
      abrirGaveta(i.id, { silencioso: true });
    } catch (err) {
      if (err.status === 409) {
        aviso(`Ainda falta: ${err.dados.faltando.map((g) => esc(g.chave)).join(", ")}.`);
        await recarregar();
        abrirGaveta(i.id, { silencioso: true, destino });
      } else falha(err);
    }
  }

  // ───────────────────────── a ponte com o chat ─────────────────────────

  function falarComLivia(texto, enviarJa) {
    // A conversa precisa estar à vista. Em tela larga vira lado a lado;
    // em tela estreita troca para a conversa.
    if (document.body.classList.contains("modo-nebulosa")) modo(window.innerWidth >= 1080 ? "lado" : "conversa");
    const chat = window.LiviaChat;
    if (!chat) return;
    if (enviarJa) chat.perguntar(texto);
    else chat.rascunhar(texto);
  }

  function evento(evt, bolha) {
    const ids = (evt.aquecidas || []).map((a) => a.id);
    if (bolha && (ids.length || evt.pescada)) {
      const box = document.createElement("div");
      box.className = "neb-chips";
      for (const a of evt.aquecidas || []) {
        const chip = document.createElement("span");
        chip.className = "neb-chip";
        chip.innerHTML = `🔥 aqueceu <b>#${a.id} ${esc(a.titulo)}</b>`;
        chip.onclick = () => mostrar(a.id);
        box.appendChild(chip);
      }
      if (evt.pescada) {
        const p = evt.pescada;
        const chip = document.createElement("span");
        chip.className = "neb-chip pescada";
        chip.innerHTML = `✦ pesquei uma ideia: <b>«${esc(p.titulo)}»</b>
          <button class="mini destaque">adotar</button><button class="mini">deixa</button>`;
        const [sim, nao] = chip.querySelectorAll("button");
        sim.onclick = async (e) => { e.stopPropagation(); await adotar(p.id); chip.innerHTML = `✓ <b>#${p.id}</b> é sua agora — tá no céu`; chip.onclick = () => mostrar(p.id); };
        nao.onclick = async (e) => { e.stopPropagation(); await apagarIdeia(p.id, true); chip.remove(); };
        chip.onclick = () => mostrar(p.id);
        box.appendChild(chip);
      }
      bolha.appendChild(box);
    }
    recarregar().then(() => {
      if (ids.length) pulsar(ids);
      if (evt.pescada) pulsar([evt.pescada.id]);
    });
  }

  function mostrar(id) {
    if (document.body.classList.contains("modo-conversa")) modo(window.innerWidth >= 1080 ? "lado" : "nebulosa");
    if (N.vista !== "ceu") trocarVista("ceu");
    setTimeout(() => { focar(id); abrirGaveta(id, { silencioso: true }); pulsar([id]); }, 60);
  }

  // ───────────────────────── modos de tela ─────────────────────────

  function modo(m) {
    if (window.innerWidth < 1080 && m === "lado") m = "conversa";
    // Trocar de modo muda a largura do céu. Sem compensar, as ideias
    // escorregariam para o canto; com isto, o centro do que você via
    // continua no centro.
    const antes = ceu.getBoundingClientRect();
    document.body.classList.remove("modo-conversa", "modo-lado", "modo-nebulosa");
    document.body.classList.add(`modo-${m}`);
    localStorage.setItem("livia-modo", m);
    document.querySelectorAll(".modos [data-modo]").forEach((b) => b.classList.toggle("ativo", b.dataset.modo === m));
    if (m !== "conversa") requestAnimationFrame(() => {
      if (!N.cameraPronta) { enquadrar(); N.cameraPronta = true; }
      else if (antes.width && antes.height) {
        const depois = ceu.getBoundingClientRect();
        N.cam.x += (depois.width - antes.width) / 2;
        N.cam.y += (depois.height - antes.height) / 2;
        aplicarCamera();
      }
      renderizar();
    });
  }

  document.querySelectorAll(".modos [data-modo]").forEach((b) => { b.onclick = () => modo(b.dataset.modo); });

  // Divisor arrastável entre conversa e céu.
  const divisor = document.getElementById("divisor");
  if (divisor) {
    const salvo = localStorage.getItem("livia-chat-w");
    if (salvo) document.documentElement.style.setProperty("--chat-w", salvo);
    divisor.addEventListener("pointerdown", (e) => {
      divisor.setPointerCapture(e.pointerId);
      divisor.classList.add("arrastando");
      const main = document.getElementById("main");
      const inicio = main.getBoundingClientRect().left;
      const mover = (ev) => {
        const total = window.innerWidth - inicio - (document.getElementById("panel").classList.contains("hidden") ? 0 : 348);
        const pct = Math.min(70, Math.max(26, ((ev.clientX - inicio) / total) * 100));
        document.documentElement.style.setProperty("--chat-w", `${pct.toFixed(1)}%`);
      };
      const soltar = () => {
        divisor.classList.remove("arrastando");
        divisor.removeEventListener("pointermove", mover);
        localStorage.setItem("livia-chat-w", getComputedStyle(document.documentElement).getPropertyValue("--chat-w").trim());
      };
      divisor.addEventListener("pointermove", mover);
      divisor.addEventListener("pointerup", soltar, { once: true });
    });
  }

  // Atalhos. Só quando o céu está à vista e ninguém está digitando.
  document.addEventListener("keydown", (e) => {
    if (document.body.classList.contains("modo-conversa")) return;
    const ativo = document.activeElement;
    const digitando = ativo && (ativo.tagName === "INPUT" || ativo.tagName === "TEXTAREA" || ativo.isContentEditable);
    if (e.key === "Escape") {
      if (rascunho) { fecharRascunho(); return; }
      if (N.ligarDe) { N.ligarDe = null; ceu.style.cursor = ""; return; }
      if (gaveta.classList.contains("aberta") && !digitando) { fecharGaveta(); return; }
    }
    if (digitando || e.metaKey || e.altKey) return;
    if (e.ctrlKey && e.key.toLowerCase() === "z" && N.vista === "ceu") { e.preventDefault(); desfazer(); return; }
    if (e.ctrlKey) return;
    // Delete (ou Backspace no Mac) apaga a ideia aberta na gaveta — com a
    // mesma confirmação do botão, porque apagar não tem volta.
    if ((e.key === "Delete" || e.key === "Backspace") && N.sel && gaveta.classList.contains("aberta")) {
      e.preventDefault(); apagarIdeia(N.sel, false); return;
    }
    if (e.key === "n" || e.key === "N") { e.preventDefault(); rascunhoRapido(centroTela()); }
    else if (e.key === "g" || e.key === "G") { e.preventDefault(); gravidade(); }
  });

  window.addEventListener("resize", () => { if (N.vista === "ceu") aplicarCamera(); });
  new MutationObserver(() => galaxia && galaxia.redesenhar())
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  // A temperatura muda com o tempo mesmo sem ninguém mexer: um retrato
  // novo a cada dois minutos mantém o céu honesto.
  setInterval(() => { if (!document.hidden && !N.fisica && !document.body.classList.contains("modo-conversa")) recarregar(); }, 120000);

  // ───────────────────────── início ─────────────────────────

  window.Nebulosa = { evento, recarregar, abrir: mostrar, modo, gravidade, aviso: (t) => aviso(esc(t)) };

  const inicial = localStorage.getItem("livia-modo") || (window.innerWidth >= 1280 ? "lado" : "conversa");
  centrar();
  modo(inicial);
  recarregar().then(() => {
    if (!document.body.classList.contains("modo-conversa")) { enquadrar(); N.cameraPronta = true; }
    // As constelações aparecem já na abertura, se o céu estiver arrumado.
    api(`/api/nebulosa/gravidade?intensidade=${N.intensidade}`).then((g) => {
      N.grav = g; desenharFios(); desenharConstelacoes();
    }).catch(() => {});
  });
})();
