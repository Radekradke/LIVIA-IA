/* Voz da Livia: ouvir (microfone → Whisper no Groq) e falar.
 *
 * Motores pra falar, na ordem em que ela tenta:
 *  - "francisca" / "thalita" (padrão): vozes neurais do Microsoft Edge,
 *    geradas pelo servidor da Livia (Edge-TTS). As mais naturais em pt-BR,
 *    de graça — mas é acesso não oficial: se falhar, cai pra voz do aparelho.
 *
 * E os dois que rodam no próprio navegador:
 *  - "sistema" é a voz pt-BR que o aparelho já tem (no iPhone, a Luciana).
 *    Zero download, funciona em qualquer coisa, inclusive iPhone antigo.
 *  - "neural" é o Piper (voz pt_BR "faber"), rodando 100% aqui: o texto vira
 *    fonema e áudio no seu aparelho, nada vai pra servidor. Custa um download
 *    de uns 80 MB na primeira vez (fica guardado). Se qualquer coisa falhar,
 *    cai pra voz do sistema sem drama.
 *
 * O Kokoro, que é o modelo da moda, ficou de fora de propósito: a versão de
 * navegador dele só sabe pronunciar inglês — pra português ele precisaria
 * mandar o texto pra um serviço de fora.
 */
(function () {
  "use strict";

  const PIPER = "https://cdn.jsdelivr.net/npm/@diffusionstudio/vits-web@1.0.3/+esm";
  const VOZ_NEURAL = "pt_BR-faber-medium";
  const CHAVE_MOTOR = "livia-voz-motor";

  const estado = {
    gravador: null, pedacos: [], tipo: "", ouvindo: false,
    falando: null,           // botão que está tocando agora
    fila: [], audio: null, cancelado: false,
    ultimaFoiVoz: false,     // a última mensagem foi falada? então a resposta é lida
    piper: null,
  };

  const temMicrofone = () => Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
  const temFala = () => "speechSynthesis" in window;

  const MOTORES = ["francisca", "thalita", "sistema", "neural"];
  function motor() {
    let m = "francisca";
    try { m = localStorage.getItem(CHAVE_MOTOR) || "francisca"; } catch { /* modo privado */ }
    if (!MOTORES.includes(m)) m = "francisca";
    if ((m === "francisca" || m === "thalita") && window.LiviaVoz && window.LiviaVoz.edgeDisponivel === false) m = "sistema";
    return m;
  }
  function definirMotor(m) {
    try { localStorage.setItem(CHAVE_MOTOR, m); } catch { /* modo privado */ }
  }

  // ───────────────────────────── ouvir ─────────────────────────────

  // O Safari do iPhone grava em mp4/aac; Chrome e Android em webm/opus.
  // O Whisper aceita os dois — só precisa saber qual é.
  function tipoDeGravacao() {
    for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/aac", "audio/ogg"]) {
      if (MediaRecorder.isTypeSupported?.(t)) return t;
    }
    return "";
  }

  async function comecarAOuvir() {
    pararDeFalar();
    const fluxo = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    const tipo = tipoDeGravacao();
    const gravador = new MediaRecorder(fluxo, tipo ? { mimeType: tipo, audioBitsPerSecond: 48000 } : undefined);
    estado.pedacos = [];
    estado.tipo = gravador.mimeType || tipo || "audio/webm";
    gravador.ondataavailable = (e) => { if (e.data && e.data.size) estado.pedacos.push(e.data); };
    estado.gravador = gravador;
    estado.ouvindo = true;
    gravador.start(250);
  }

  function pararDeOuvir() {
    return new Promise((resolve) => {
      const g = estado.gravador;
      if (!g) { resolve(null); return; }
      g.onstop = () => {
        g.stream.getTracks().forEach((t) => t.stop());
        estado.gravador = null;
        estado.ouvindo = false;
        resolve(new Blob(estado.pedacos, { type: estado.tipo }));
      };
      g.stop();
    });
  }

  async function transcrever(blob) {
    const r = await fetch("/api/voz", {
      method: "POST", body: blob,
      headers: { "Content-Type": (blob.type || "audio/webm").split(";")[0] },
    });
    const dados = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(dados.error || `falhou (${r.status})`);
    return dados.texto;
  }

  /* Liga um botão de microfone: toca pra começar, toca de novo pra parar.
   * `aoTexto(texto)` recebe o que foi dito. Segurar o botão não é o gesto
   * aqui porque no iPhone segurar abre menu de contexto. */
  function ligarMicrofone(botao, aoTexto, { aoMudar } = {}) {
    if (!temMicrofone()) { botao.hidden = true; return; }
    let ocupado = false;
    const pintar = (fase) => {
      botao.dataset.fase = fase;
      botao.setAttribute("aria-pressed", String(fase === "ouvindo"));
      botao.title = { ouvindo: "Ouvindo… toque pra parar", pensando: "Transcrevendo…", "": "Falar" }[fase] ?? "Falar";
      aoMudar?.(fase);
    };
    pintar("");
    botao.addEventListener("click", async (e) => {
      e.preventDefault();
      if (ocupado) return;
      if (!estado.ouvindo) {
        destravar();
        try { await comecarAOuvir(); pintar("ouvindo"); }
        catch (err) {
          pintar("");
          avisar(err.name === "NotAllowedError"
            ? "Sem permissão pro microfone. No iPhone: Ajustes › Safari › Microfone."
            : `Microfone indisponível: ${err.message}`);
        }
        return;
      }
      ocupado = true;
      pintar("pensando");
      try {
        const blob = await pararDeOuvir();
        if (!blob || blob.size < 1200) { avisar("Não deu pra ouvir nada — tenta falar um pouco mais."); return; }
        const texto = await transcrever(blob);
        estado.ultimaFoiVoz = true;
        aoTexto(texto);
      } catch (err) {
        avisar(err.message);
      } finally {
        ocupado = false;
        pintar("");
      }
    });
  }

  // ───────────────────────────── falar ─────────────────────────────

  // Markdown, código e link não são pra ler em voz alta.
  function textoFalavel(bruto) {
    return String(bruto || "")
      .replace(/```[\s\S]*?```/g, " (tem um trecho de código aqui) ")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/https?:\/\/\S+/g, " link ")
      .replace(/^[#>\-*+|\s]+/gm, "")
      .replace(/[*_~|]/g, "")
      .replace(/#(\d+)/g, "número $1")
      .replace(/\s+\n/g, "\n")
      .trim();
  }

  // O iPhone corta frase longa no meio. Frases curtas, em fila, resolvem.
  function frases(texto) {
    const partes = texto.match(/[^.!?…\n]+[.!?…]*\s*/g) || [texto];
    const saida = [];
    let atual = "";
    for (const p of partes) {
      if ((atual + p).length > 220 && atual) { saida.push(atual.trim()); atual = ""; }
      atual += p;
    }
    if (atual.trim()) saida.push(atual.trim());
    return saida.filter(Boolean);
  }

  let vozSistema = null;
  function escolherVozSistema() {
    const vozes = speechSynthesis.getVoices();
    const pt = vozes.filter((v) => /^pt[-_]BR/i.test(v.lang));
    // Preferência: vozes "premium/enhanced" do iPhone e Mac, depois Google, depois qualquer pt-BR.
    vozSistema = pt.find((v) => /premium|enhanced|aprimorad/i.test(v.name))
      || pt.find((v) => /luciana|felipe|google/i.test(v.name))
      || pt[0] || vozes.find((v) => /^pt/i.test(v.lang)) || null;
  }
  if (temFala()) {
    escolherVozSistema();
    speechSynthesis.addEventListener?.("voiceschanged", escolherVozSistema);
  }

  function falarSistema(partes, aoAcabar) {
    speechSynthesis.cancel();
    let i = 0;
    const proxima = () => {
      if (estado.cancelado || i >= partes.length) { aoAcabar(); return; }
      const u = new SpeechSynthesisUtterance(partes[i++]);
      u.lang = "pt-BR";
      if (vozSistema) u.voice = vozSistema;
      u.rate = 1.04;
      u.onend = proxima;
      u.onerror = proxima;
      speechSynthesis.speak(u);
    };
    proxima();
  }

  async function carregarPiper() {
    if (!estado.piper) estado.piper = import(PIPER);
    return estado.piper;
  }

  // Gera a frase seguinte enquanto a atual toca: a espera só aparece no começo.
  async function falarNeural(partes, aoAcabar, aoProgresso) {
    const tts = await carregarPiper();
    const gerar = (t) => tts.predict({ text: t, voiceId: VOZ_NEURAL }, (p) => {
      if (p && p.total) aoProgresso?.(Math.round((p.loaded / p.total) * 100));
    });
    let proximo = gerar(partes[0]);
    for (let i = 0; i < partes.length; i++) {
      if (estado.cancelado) break;
      const wav = await proximo;
      if (i + 1 < partes.length) proximo = gerar(partes[i + 1]);
      if (estado.cancelado) break;
      await new Promise((resolve) => {
        const url = URL.createObjectURL(wav);
        const audio = new Audio(url);
        estado.audio = audio;
        audio.onended = audio.onerror = () => { URL.revokeObjectURL(url); resolve(); };
        audio.play().catch(resolve);
      });
    }
    aoAcabar();
  }

  // Um tocador só, destravado num toque do André e reaproveitado: o Safari do
  // iPhone barra play() que não nasce de um gesto, e o áudio da Edge chega
  // depois de uma ida à rede — quando o "gesto" já passou.
  const SILENCIO = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";
  let tocador = null;
  function destravar() {
    if (!tocador) tocador = new Audio();
    if (tocador.dataset?.destravado) return;
    try {
      tocador.src = SILENCIO;
      const p = tocador.play();
      if (p && p.then) p.then(() => { tocador.dataset.destravado = "1"; }).catch(() => {});
    } catch { /* sem áudio aqui */ }
  }

  function tocar(blob) {
    return new Promise((resolve) => {
      if (!tocador) tocador = new Audio();
      const url = URL.createObjectURL(blob);
      tocador.onended = tocador.onerror = () => { URL.revokeObjectURL(url); resolve(); };
      tocador.src = url;
      estado.audio = tocador;
      tocador.play().catch(() => resolve());
    });
  }

  async function sintetizarEdge(texto, voz) {
    const r = await fetch("/api/voz/falar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto, voz }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `voz ${r.status}`);
    const blob = await r.blob();
    if (!blob.size) throw new Error("áudio vazio");
    return blob;
  }

  // Busca a frase seguinte enquanto a atual toca. Se a Microsoft falhar no
  // meio, o resto sai na voz do aparelho — a resposta não fica pela metade.
  async function falarEdge(partes, voz, aoAcabar) {
    let proximo = sintetizarEdge(partes[0], voz);
    for (let i = 0; i < partes.length; i++) {
      if (estado.cancelado) break;
      let blob;
      try { blob = await proximo; }
      catch (err) {
        if (i === 0) throw err;
        if (temFala()) { falarSistema(partes.slice(i), aoAcabar); return; }
        break;
      }
      if (i + 1 < partes.length) { proximo = sintetizarEdge(partes[i + 1], voz); proximo.catch(() => {}); }
      if (estado.cancelado) break;
      await tocar(blob);
    }
    aoAcabar();
  }

  function pararDeFalar() {
    estado.cancelado = true;
    if (temFala()) speechSynthesis.cancel();
    if (estado.audio) {
      estado.audio.pause();
      if (estado.audio === tocador && tocador.onended) tocador.onended();  // solta quem esperava o fim
      estado.audio = null;
    }
    if (estado.falando) { estado.falando.dataset.fase = ""; estado.falando = null; }
  }

  async function falar(texto, botao) {
    const tocandoEste = estado.falando && estado.falando === botao;
    pararDeFalar();
    if (tocandoEste) return;               // segundo toque no mesmo botão = parar
    const partes = frases(textoFalavel(texto));
    if (!partes.length) return;
    estado.cancelado = false;
    estado.falando = botao || null;
    if (botao) botao.dataset.fase = "falando";
    const fim = () => { if (estado.falando === botao) { if (botao) botao.dataset.fase = ""; estado.falando = null; } };

    const m = motor();
    if (m === "francisca" || m === "thalita") {
      destravar();
      try {
        if (botao) botao.dataset.fase = "carregando";
        await falarEdge(partes, m, fim);
        return;
      } catch (err) {
        console.warn("[voz] Edge falhou, usando a do aparelho:", err);
        if (!estado.avisouEdge) { avisar("A voz da Microsoft não respondeu agora — usei a voz do aparelho."); estado.avisouEdge = true; }
        if (estado.cancelado) return;
        if (botao) botao.dataset.fase = "falando";
      }
      if (!temFala()) { fim(); return; }
      falarSistema(partes, fim);
      return;
    }

    if (m === "neural") {
      try {
        if (botao) botao.dataset.fase = "carregando";
        await falarNeural(partes, fim, (pct) => { if (botao) botao.title = `Baixando a voz… ${pct}%`; });
        return;
      } catch (err) {
        console.warn("[voz] neural falhou, usando a do sistema:", err);
        avisar("A voz neural não carregou aqui — usei a voz do aparelho.");
        if (estado.cancelado) return;
        if (botao) botao.dataset.fase = "falando";
      }
    }
    if (!temFala()) { avisar("Este navegador não fala."); fim(); return; }
    falarSistema(partes, fim);
  }

  // ───────────────────────────── utilidades ─────────────────────────────

  function avisar(texto) {
    // O balão da Nebulosa só serve quando o céu está à vista; no chat, o nosso.
    if (window.Nebulosa?.aviso && document.body.classList.contains("modo-nebulosa")) { window.Nebulosa.aviso(texto); return; }
    let el = document.getElementById("vozAviso");
    if (!el) {
      el = document.createElement("div");
      el.id = "vozAviso";
      el.className = "voz-aviso";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = texto;
    el.classList.add("visivel");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("visivel"), 4200);
  }

  window.LiviaVoz = {
    temMicrofone, temFala, ligarMicrofone, falar, pararDeFalar,
    motor, definirMotor, motores: MOTORES, edgeDisponivel: undefined,
    get ultimaFoiVoz() { return estado.ultimaFoiVoz; },
    set ultimaFoiVoz(v) { estado.ultimaFoiVoz = Boolean(v); },
    _textoFalavel: textoFalavel, _frases: frases,
  };
})();
