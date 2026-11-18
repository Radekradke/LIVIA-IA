/* Galáxia — o fundo vivo da Nebulosa, desenhado na GPU.
 *
 * Um único shader de fragmento pinta, pixel a pixel:
 *
 *   1. GÁS     ruído fractal (fbm) com deformação de domínio: o ruído
 *              deforma as coordenadas de outro ruído, e é isso que faz
 *              filamento em vez de mancha. Duas camadas, com poeira
 *              escura cortando a luz — as "faixas de poeira" das fotos
 *              de nebulosa de verdade.
 *   2. BRAÇOS  uma espiral logarítmica em volta da origem do céu, que é
 *              onde as ideias moram. O centro tem um núcleo pálido; os
 *              braços se desfazem perto da borda.
 *   3. ESTRELAS três camadas, cada uma numa profundidade: a do fundo quase
 *              não se mexe quando a câmera anda, a da frente acompanha.
 *              É a paralaxe que vende a distância.
 *   4. BORDA   para além do raio do céu, o escuro toma conta e um anel
 *              tênue marca o horizonte. É a "borda escura" das ideias
 *              apagadas, agora feita de espaço, e não de CSS.
 *   5. ONDA    o pulso de gravidade: uma onda que sai do centro e entorta
 *              o espaço por onde passa, como lente gravitacional.
 *
 * Paleta de propósito melancólica: índigo, violeta gasto, um verde-água
 * de poeira fria e um resto de rosa, todos baixos. A cor quente fica para
 * as ideias em brasa, que assim são as únicas coisas que de fato brilham.
 *
 * Custo: roda em meia resolução (gás é macio, não precisa de mais) e a no
 * máximo 30 quadros por segundo; para quando a aba some, quando o céu
 * está escondido e quando o sistema pede menos movimento. Sem WebGL, nada
 * quebra: o CSS antigo continua sendo o céu.
 */
"use strict";

(function () {
  const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }`;

  const FRAG = `
precision highp float;

uniform vec2  uRes;      // tamanho do canvas, em pixels do canvas
uniform float uEscala;   // pixels do canvas por pixel CSS
uniform vec3  uCam;      // câmera da Nebulosa: x, y (px CSS) e zoom
uniform float uTempo;
uniform float uBorda;    // raio do céu, em unidades de mundo
uniform vec2  uPulso;    // x = segundos desde o pulso, y = força (0 a 1)
uniform float uClaro;    // 1 no tema claro

// ── ruído ───────────────────────────────────────────────────────────
float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float ruido(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  mat2 gira = mat2(0.8, 0.6, -0.6, 0.8);
  for (int k = 0; k < 5; k++) {
    v += a * ruido(p);
    p = gira * p * 2.03 + 17.1;
    a *= 0.5;
  }
  return v;
}

// ── estrelas: uma por célula, com chance, brilho e cor próprios ─────
vec3 estrelas(vec2 p, float grade, float limiar, float tamanho) {
  vec2 celula = floor(p / grade);
  vec2 local = fract(p / grade) - 0.5;
  float h = hash(celula);
  if (h < limiar) return vec3(0.0);
  vec2 centro = vec2(hash(celula + 3.1), hash(celula + 7.7)) - 0.5;
  float d = length(local - centro * 0.8) * grade;
  float brilho = smoothstep(tamanho, 0.0, d);
  // cintilar lento e fora de sincronia — nenhuma estrela pisca junto
  brilho *= 0.6 + 0.4 * sin(uTempo * (0.4 + h * 1.7) + h * 40.0);
  // cor: a maioria fria; algumas cor de lua; raras, rosadas
  float c = hash(celula + 11.0);
  vec3 cor = c < 0.72 ? vec3(0.82, 0.86, 1.0)
           : c < 0.93 ? vec3(1.0, 0.94, 0.82)
           : vec3(1.0, 0.78, 0.82);
  // as mais fortes ganham um halo fraquinho
  float halo = smoothstep(tamanho * 4.0, 0.0, d) * 0.12 * step(0.985, h);
  return cor * (brilho + halo) * (0.35 + 0.65 * (h - limiar) / (1.0 - limiar));
}

void main() {
  // pixel do canvas → pixel CSS → coordenada de mundo da Nebulosa
  vec2 frag = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y) / uEscala;
  vec2 tela = uRes / uEscala;
  vec2 meio = tela * 0.5;
  vec2 mundo = (frag - uCam.xy) / uCam.z;

  // paralaxe: cada camada vive numa profundidade d (0 = presa à tela,
  // 1 = presa ao mundo). A câmera anda d vezes; o zoom pesa z^d.
  #define CAMADA(d) ((frag - meio - (uCam.xy - meio) * (d)) / pow(uCam.z, (d)))

  // ── a onda de gravidade ──
  float r0 = length(mundo);
  float frente = uPulso.x * 1500.0;
  float faixa = exp(-pow((r0 - frente) / 140.0, 2.0)) * uPulso.y;
  vec2 dirMundo = r0 > 0.001 ? mundo / r0 : vec2(0.0);
  vec2 lente = dirMundo * sin((r0 - frente) * 0.03) * 70.0 * faixa;
  mundo += lente;

  // ── gás de fundo (profundidade média) ──
  vec2 g = CAMADA(0.55) * 0.0016 + lente * 0.0008;
  float t = uTempo * 0.006;
  vec2 q = vec2(fbm(g + t), fbm(g + vec2(5.2, 1.3) - t));
  vec2 w = vec2(fbm(g + 3.2 * q + vec2(1.7, 9.2) + t * 0.5),
                fbm(g + 3.2 * q + vec2(8.3, 2.8)));
  float gas = fbm(g + 2.6 * w);
  float poeira = fbm(g * 1.7 + 4.0 * q + 12.0);

  vec3 tinta   = vec3(0.020, 0.022, 0.045);
  vec3 indigo  = vec3(0.105, 0.110, 0.270);
  vec3 violeta = vec3(0.260, 0.205, 0.400);
  vec3 agua    = vec3(0.140, 0.270, 0.320);
  vec3 rosa    = vec3(0.400, 0.250, 0.330);
  vec3 lavanda = vec3(0.600, 0.620, 0.900);

  vec3 cor = mix(tinta, indigo, smoothstep(0.25, 0.75, gas));
  cor = mix(cor, violeta, smoothstep(0.45, 0.95, gas * q.x * 1.6));
  cor = mix(cor, agua,    smoothstep(0.55, 1.0, w.y) * 0.55);
  cor = mix(cor, rosa,    smoothstep(0.6, 1.0, q.y * gas * 1.8) * 0.35);
  cor += lavanda * pow(smoothstep(0.62, 1.0, gas), 3.0) * 0.55;
  cor *= mix(1.0, 0.32, smoothstep(0.48, 0.75, poeira));   // faixas de poeira
  cor *= 1.35;   // o gás de fundo, um pouco mais presente longe do centro

  // ── a galáxia em volta do centro do céu (presa ao mundo) ──
  float r = length(mundo) / uBorda;
  float ang = atan(mundo.y, mundo.x);
  float espiral = cos(2.0 * (ang - log(r + 0.04) * 2.3) + fbm(mundo * 0.004) * 2.5 - uTempo * 0.01);
  float bracos = pow(0.5 + 0.5 * espiral, 4.0) * smoothstep(1.05, 0.08, r);
  float gasBraco = fbm(mundo * 0.0042 + 30.0);
  cor += mix(indigo, lavanda, 0.35) * bracos * (0.25 + 0.9 * gasBraco) * 0.85;
  cor += vec3(0.80, 0.80, 0.98) * exp(-r * r * 30.0) * 0.22;     // núcleo pálido
  cor += vec3(0.55, 0.50, 0.75) * exp(-r * r * 4.0) * 0.07;      // bojo difuso

  // ── estrelas em três profundidades ──
  vec3 brilhos = estrelas(CAMADA(0.12), 34.0, 0.90, 1.1) * 0.55
               + estrelas(CAMADA(0.35), 58.0, 0.93, 1.4) * 0.8
               + estrelas(CAMADA(0.75), 110.0, 0.955, 1.9);
  // mais estrelas onde tem braço: a galáxia é feita delas
  brilhos += estrelas(mundo, 26.0, 0.80, 1.0) * bracos * 1.4;
  cor += brilhos;

  // ── a borda escura e o horizonte ──
  float alem = smoothstep(0.86, 1.12, r);
  cor *= mix(1.0, 0.14, alem);
  float horizonte = exp(-pow((r - 1.0) * uBorda / 7.0, 2.0));
  cor += vec3(0.62, 0.64, 0.95) * horizonte * 0.13;

  // ── a frente da onda brilha um pouco ao passar ──
  cor += vec3(0.70, 0.72, 1.0) * faixa * 0.26;
  cor += vec3(0.85, 0.80, 1.0) * exp(-pow((r0 - frente + 90.0) / 22.0, 2.0)) * uPulso.y * 0.45;

  // vinheta + grão (o grão evita degraus no degradê escuro)
  vec2 v = frag / tela - 0.5;
  cor *= 1.0 - dot(v, v) * 0.9;
  cor += (hash(gl_FragCoord.xy + fract(uTempo)) - 0.5) / 255.0 * 3.0;

  if (uClaro > 0.5) {
    // Tema claro: a mesma estrutura, como tinta aguada em papel frio.
    vec3 papel = vec3(0.930, 0.937, 0.965);
    float tinta2 = clamp(dot(cor, vec3(0.45, 0.35, 0.55)) * 1.6, 0.0, 1.0);
    cor = mix(papel, vec3(0.33, 0.35, 0.62), tinta2 * 0.55);
  }

  gl_FragColor = vec4(cor, 1.0);
}`;

  function compilar(gl, tipo, fonte) {
    const s = gl.createShader(tipo);
    gl.shaderSource(s, fonte);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      console.warn("[galaxia] shader:", gl.getShaderInfoLog(s));
      return null;
    }
    return s;
  }

  function criar(canvas) {
    const gl = canvas.getContext("webgl", { antialias: false, alpha: false, preserveDrawingBuffer: false })
      || canvas.getContext("experimental-webgl");
    if (!gl) return null;

    const vs = compilar(gl, gl.VERTEX_SHADER, VERT);
    const fs = compilar(gl, gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return null;
    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.warn("[galaxia] link:", gl.getProgramInfoLog(prog));
      return null;
    }
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const u = {};
    for (const nome of ["uRes", "uEscala", "uCam", "uTempo", "uBorda", "uPulso", "uClaro"])
      u[nome] = gl.getUniformLocation(prog, nome);

    const reduzido = window.matchMedia("(prefers-reduced-motion: reduce)");
    const estado = { cam: { x: 0, y: 0, s: 1 }, borda: 1000, pulso: -1, sujo: true, visivel: () => true };
    const t0 = performance.now();
    let ultimo = 0;
    let quadro = null;

    function redimensionar() {
      // Meia resolução: o gás é macio, e isso corta o custo pela metade
      // ao quadrado. As estrelas continuam nítidas o bastante.
      const fator = Math.min(window.devicePixelRatio || 1, 2) * 0.5;
      const w = Math.max(1, Math.round(canvas.clientWidth * fator));
      const h = Math.max(1, Math.round(canvas.clientHeight * fator));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        gl.viewport(0, 0, w, h);
      }
      return fator;
    }

    function desenhar(agora) {
      const fator = redimensionar();
      const tempo = reduzido.matches ? 0 : (agora - t0) / 1000;
      let pulsoT = 0, pulsoF = 0;
      if (estado.pulso >= 0) {
        pulsoT = (agora - estado.pulso) / 1000;
        pulsoF = Math.max(0, 1 - pulsoT / 2.6);
        if (pulsoF <= 0) estado.pulso = -1;
      }
      gl.uniform2f(u.uRes, canvas.width, canvas.height);
      gl.uniform1f(u.uEscala, fator);
      gl.uniform3f(u.uCam, estado.cam.x, estado.cam.y, estado.cam.s);
      gl.uniform1f(u.uTempo, tempo);
      gl.uniform1f(u.uBorda, estado.borda);
      gl.uniform2f(u.uPulso, pulsoT, pulsoF);
      gl.uniform1f(u.uClaro, document.documentElement.dataset.theme === "light" ? 1 : 0);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      estado.sujo = false;
    }

    function laco(agora) {
      quadro = requestAnimationFrame(laco);
      if (document.hidden || !estado.visivel() || canvas.clientWidth === 0) return;
      const animando = !reduzido.matches || estado.pulso >= 0;
      // 30 quadros por segundo bastam para gás que anda devagar
      if (!estado.sujo && (!animando || agora - ultimo < 33)) return;
      ultimo = agora;
      desenhar(agora);
    }
    quadro = requestAnimationFrame(laco);

    canvas.addEventListener("webglcontextlost", (e) => { e.preventDefault(); cancelAnimationFrame(quadro); });

    return {
      camera(cam, borda) {
        estado.cam.x = cam.x; estado.cam.y = cam.y; estado.cam.s = cam.s;
        if (borda) estado.borda = borda;
        estado.sujo = true;
      },
      pulso() { estado.pulso = performance.now(); estado.sujo = true; },
      quandoVisivel(fn) { estado.visivel = fn; },
      redesenhar() { estado.sujo = true; },
    };
  }

  window.Galaxia = { criar };
})();
