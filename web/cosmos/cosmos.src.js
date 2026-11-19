/* Cosmos — o fundo da Nebulosa em 3D, com Three.js.
 *
 * Fonte. O arquivo que o navegador carrega é web/cosmos.js, gerado daqui
 * pelo esbuild (ver web/cosmos/README.md). Edite este, gere aquele.
 *
 * O QUE TEM NA CENA
 * -----------------
 *   GALÁXIA   ~70 mil estrelas em quatro braços espirais logarítmicos, com
 *             bojo no centro, regiões rosadas de formação de estrela e
 *             ~2.600 nuvens enormes e quase transparentes que, somadas,
 *             desenham o brilho contínuo dos braços. O padrão gira inteiro,
 *             devagar, e cada estrela oscila no lugar; tudo no shader de
 *             vértice — a CPU não move partícula nenhuma.
 *   ESTRELAS  milhares de estrelas de fundo numa casca bem longe. Como a
 *             câmera é perspectiva de verdade, elas quase não andam quando
 *             você arrasta o céu, e a galáxia anda junto com as ideias:
 *             a paralaxe sai de graça da geometria.
 *   POEIRA    partículas grandes e tênues entre a câmera e a galáxia,
 *             flutuando. Passam na frente quando você anda — é o que faz
 *             parecer que você está DENTRO, e não olhando um papel de parede.
 *   GÁS       o shader de nebulosa (ruído fractal deformado), agora como
 *             fundo de tudo, com estrelas cadentes raras e o horizonte
 *             escuro na borda do céu.
 *   BLOOM     o brilho que vaza das partes claras (UnrealBloomPass).
 *
 * ALINHAMENTO COM AS IDEIAS
 * -------------------------
 * As ideias são HTML por cima do canvas. Para elas ficarem em cima da
 * galáxia certa, a câmera é posicionada de modo que o plano z = 0 da cena
 * coincida pixel a pixel com o "mundo" da Nebulosa: o centro da tela olha
 * para o mesmo ponto, e a distância é a que faz a altura visível em z = 0
 * ser a altura da tela dividida pelo zoom. Dar zoom é, literalmente,
 * a câmera voar para perto do disco.
 *
 * O mouse não mexe na câmera (isso desalinharia as ideias); ele inclina a
 * galáxia e o campo de estrelas de leve, que é o que o olho lê como
 * profundidade.
 *
 * CUSTO
 * -----
 * Resolução limitada a 1.5× e reduzida sozinha se o quadro passar de
 * ~28 ms; menos partículas em máquina modesta; pausa quando a aba some ou
 * o céu está escondido; com prefers-reduced-motion nada gira e só redesenha
 * quando a câmera muda. Sem WebGL2, quem desenha é o galaxia.js de antes.
 */

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

// ─────────────────────────────── gás de fundo ───────────────────────────────

// Paletas: em vez de reescrever as cores de cada camada, gira o MATIZ de
// tudo de uma vez (em YIQ, que separa brilho de cor) e ajusta a saturação.
// Cinza e branco não têm cor, então as estrelas brancas continuam brancas —
// só o gás, os braços e a fumaça mudam de tinta.
const MATIZ = /* glsl */ `
uniform vec2 uMatiz;   // x = giro do matiz (rad), y = saturação
vec3 matiz(vec3 c) {
  const mat3 paraYIQ = mat3(0.299, 0.596, 0.211, 0.587, -0.274, -0.523, 0.114, -0.322, 0.312);
  const mat3 deYIQ = mat3(1.0, 1.0, 1.0, 0.956, -0.272, -1.106, 0.621, -0.647, 1.703);
  vec3 yiq = paraYIQ * c;
  float cs = cos(uMatiz.x), sn = sin(uMatiz.x);
  yiq.yz = mat2(cs, sn, -sn, cs) * yiq.yz * uMatiz.y;
  return max(deYIQ * yiq, 0.0);
}`;

// nome: [giro do matiz, saturação]
const PALETAS = {
  crepusculo: [0, 1],
  magenta: [0, 1],
  aurora: [0, 1],
  brasa: [0, 1],
};

const GAS_VERT = /* glsl */ `
void main() { gl_Position = vec4(position.xy, 1.0, 1.0); }`;

const GAS_FRAG = /* glsl */ `
precision highp float;
uniform vec2  uRes;
uniform float uEscala;
uniform vec3  uCam;
uniform float uTempo;
uniform float uBorda;
uniform vec2  uPulso;
uniform float uClaro;
${MATIZ}

float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float ruido(vec2 p) {
  vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5; mat2 g = mat2(0.8, 0.6, -0.6, 0.8);
  for (int k = 0; k < 5; k++) { v += a * ruido(p); p = g * p * 2.03 + 17.1; a *= 0.5; }
  return v;
}

// Estrela cadente: a cada ~11 s, numa posição e direção sorteadas.
float cadente(vec2 uv, float t) {
  float ciclo = floor(t / 11.0);
  float fase = fract(t / 11.0);
  float h = hash(vec2(ciclo, 3.7));
  if (fase > 0.09 || h < 0.35) return 0.0;
  vec2 ini = vec2(hash(vec2(ciclo, 1.3)), hash(vec2(ciclo, 2.9)) * 0.5 + 0.1);
  vec2 dir = normalize(vec2(0.8 + hash(vec2(ciclo, 5.1)) * 0.6, 0.45));
  float k = fase / 0.09;
  vec2 cabeca = ini + dir * k * 0.55;
  vec2 d = uv - cabeca;
  float ao_longo = dot(d, -dir);
  float lado = length(d + dir * ao_longo);
  float rastro = smoothstep(0.0, 0.002, 0.0025 - lado) * smoothstep(0.16, 0.0, ao_longo) * step(0.0, ao_longo);
  return rastro * sin(k * 3.14159) * 0.9;
}

void main() {
  vec2 frag = vec2(gl_FragCoord.x, uRes.y - gl_FragCoord.y) / uEscala;
  vec2 tela = uRes / uEscala;
  vec2 meio = tela * 0.5;
  vec2 mundo = (frag - uCam.xy) / uCam.z;

  float r0 = length(mundo);
  float frente = uPulso.x * 1500.0;
  float faixa = exp(-pow((r0 - frente) / 160.0, 2.0)) * uPulso.y;

  // o gás vive atrás de tudo: anda 35% do que a câmera anda
  vec2 g = (frag - meio - (uCam.xy - meio) * 0.35) / pow(uCam.z, 0.35) * 0.0014;
  g += (r0 > 0.0 ? mundo / r0 : vec2(0.0)) * faixa * 0.05;
  float t = uTempo * 0.018;
  vec2 q = vec2(fbm(g + vec2(t, -t * 0.6)), fbm(g + vec2(5.2, 1.3) - t));
  vec2 w = vec2(fbm(g + 3.0 * q + vec2(1.7, 9.2) + t * 0.7), fbm(g + 3.0 * q + vec2(8.3, 2.8) - t * 0.4));
  float gas = fbm(g + 2.6 * w);
  float poeira = fbm(g * 1.8 + 4.0 * q + 12.0 + t * 0.3);

  vec3 tinta   = vec3(0.010, 0.012, 0.030);
  vec3 indigo  = vec3(0.090, 0.095, 0.240);
  vec3 violeta = vec3(0.230, 0.170, 0.360);
  vec3 agua    = vec3(0.110, 0.230, 0.290);
  vec3 rosa    = vec3(0.360, 0.200, 0.300);

  vec3 cor = mix(tinta, indigo, smoothstep(0.28, 0.8, gas));
  cor = mix(cor, violeta, smoothstep(0.45, 0.95, gas * q.x * 1.6));
  cor = mix(cor, agua, smoothstep(0.55, 1.0, w.y) * 0.5);
  cor = mix(cor, rosa, smoothstep(0.6, 1.0, q.y * gas * 1.8) * 0.3);
  cor *= mix(1.0, 0.35, smoothstep(0.5, 0.78, poeira));
  cor *= 1.15;

  // estrelas cadentes, em coordenada de tela (estão "no céu", não no mapa)
  vec2 uv = frag / tela;
  cor += vec3(0.85, 0.88, 1.0) * cadente(uv, uTempo);

  // além da borda do céu, o escuro; no raio, um horizonte tênue
  float r = r0 / uBorda;
  cor *= mix(1.0, 0.2, smoothstep(0.9, 1.15, r));
  cor += vec3(0.55, 0.58, 0.95) * exp(-pow((r - 1.0) * uBorda / 8.0, 2.0)) * 0.10;
  cor += vec3(0.7, 0.72, 1.0) * faixa * 0.12;

  vec2 v = uv - 0.5;
  cor *= 1.0 - dot(v, v) * 0.8;
  cor += (hash(gl_FragCoord.xy + fract(uTempo)) - 0.5) / 255.0 * 3.0;
  cor = matiz(cor);

  if (uClaro > 0.5) {
    vec3 papel = vec3(0.930, 0.937, 0.965);
    float k = clamp(dot(cor, vec3(0.45, 0.35, 0.55)) * 2.2, 0.0, 1.0);
    cor = mix(papel, vec3(0.36, 0.38, 0.66), k * 0.5);
  }
  gl_FragColor = vec4(cor, 1.0);
}`;

// ─────────────────────────────── fumaça ───────────────────────────────
// Fiapos de poeira cósmica no plano do disco. Ruído "dobrado" (ridged) vira
// filamento; um domain-warp lento faz os fiapos se enrolarem e escorrerem.
// Rotação rígida, de propósito: rotação diferencial enrolaria o ruído cada
// vez mais com o passar das horas (o mesmo problema que a galáxia teve).
const FUMACA_VERT = /* glsl */ `
varying vec2 vP;
void main() {
  vP = position.xy;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const FUMACA_FRAG = /* glsl */ `
precision highp float;
uniform float uTempo;
uniform float uSemente;
uniform float uForca;
uniform float uClaro;
varying vec2 vP;
${MATIZ}

float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float ruido(vec2 p) {
  vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5; mat2 g = mat2(0.8, 0.6, -0.6, 0.8);
  for (int k = 0; k < OITAVAS; k++) { v += a * ruido(p); p = g * p * 2.07 + 11.3; a *= 0.5; }
  return v;
}
mat2 giro(float a) { float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }

void main() {
  vec2 p = vP * 1.15;                  // r = 1 na borda da galáxia
  float r = length(p);
  float t = uTempo;
  vec2 q = giro(t * 0.006 + uSemente) * p;
  // um leve torcer espiral fixo, pra fumaça acompanhar os braços
  q = giro(1.4 * log(r + 0.08)) * q;
  q *= 2.4;
  vec2 w = vec2(fbm(q + vec2(t * 0.014, uSemente)), fbm(q + vec2(5.2, 1.3) - t * 0.011));
  float n = fbm(q * 1.35 + 2.4 * w + uSemente * 3.0);
  float fio = pow(1.0 - abs(2.0 * n - 1.0), 7.0);       // filamentos finos
  float veu = smoothstep(0.55, 0.95, n);                 // e um véu ralo em volta
  // manchas grandes: a fumaça aparece em trechos, não cobre o disco todo
  float mancha = smoothstep(0.42, 0.72, fbm(p * 1.6 + vec2(uSemente * 2.1, -t * 0.004)));
  float dens = (fio * 0.8 + veu * 0.2) * mancha;
  // some no miolo (o bojo já é claro) e antes da borda
  float mascara = smoothstep(1.02, 0.35, r) * smoothstep(0.04, 0.22, r);

  vec3 cor = mix(vec3(0.46, 0.42, 0.88), vec3(0.34, 0.62, 0.80), smoothstep(0.3, 0.8, w.x));
  cor = mix(cor, vec3(0.78, 0.52, 0.76), smoothstep(0.62, 0.92, n) * 0.35);
  float a = dens * mascara * uForca;
  cor = matiz(cor);
  if (uClaro > 0.5) { cor = vec3(0.30, 0.32, 0.62); a *= 0.45; }
  gl_FragColor = vec4(cor, a);
}`;

// ─────────────────────────────── partículas ───────────────────────────────

const ESTRELA_FRAG = /* glsl */ `
precision highp float;
uniform float uClaro;
${MATIZ}
varying vec3 vCor;
varying float vAlfa;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  if (d > 0.5) discard;
  // núcleo firme + halo gaussiano: de perto é uma estrela, de longe um brilho
  float a = exp(-d * d * 22.0) + exp(-d * d * 120.0) * 0.8;
  if (uClaro > 0.5) {
    gl_FragColor = vec4(mix(vec3(0.25, 0.27, 0.55), vCor * 0.5, 0.35), a * vAlfa * 0.55);
  } else {
    gl_FragColor = vec4(matiz(vCor) * a * vAlfa, a * vAlfa);
  }
}`;

const NUVEM_FRAG = /* glsl */ `
precision highp float;
uniform float uClaro;
${MATIZ}
varying vec3 vCor;
varying float vAlfa;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  if (d > 0.5) discard;
  float a = exp(-d * d * 9.0) * (1.0 - d * 2.0);
  if (uClaro > 0.5) gl_FragColor = vec4(vec3(0.34, 0.36, 0.66), a * vAlfa * 0.10);
  else gl_FragColor = vec4(matiz(vCor) * a * vAlfa * 0.11, a * vAlfa * 0.11);
}`;

const GALAXIA_VERT = /* glsl */ `
attribute float aRaio;
attribute float aAngulo;
attribute float aAltura;
attribute float aTamanho;
attribute vec3  aCor;
attribute float aFase;
uniform float uTempo;
uniform float uProj;     // pixels por unidade de mundo a distância 1
uniform float uBorda;
uniform vec2  uPulso;
uniform float uMax;
varying vec3 vCor;
varying float vAlfa;
void main() {
  float r = aRaio * uBorda;
  // O padrão espiral gira inteiro, devagar (uma volta a cada ~5 min), e
  // cada estrela oscila um pouco em volta do seu lugar — mais no miolo.
  // Rotação diferencial pura enrolaria os braços até virarem anéis em
  // poucos minutos (o "problema do enrolamento" das galáxias de verdade,
  // que lá é resolvido por ondas de densidade).
  float ang = aAngulo + uTempo * 0.021
            + sin(uTempo * 0.06 + aFase * 6.2832) * 0.025 / (0.15 + aRaio);
  float y = aAltura * uBorda;

  // a onda de gravidade: o disco ondula quando ela passa
  float frente = uPulso.x * 1500.0;
  float faixa = exp(-pow((r - frente) / 160.0, 2.0)) * uPulso.y;
  y += sin((r - frente) * 0.025) * 70.0 * faixa;
  r += faixa * 40.0;

  vec4 mv = modelViewMatrix * vec4(cos(ang) * r, y, sin(ang) * r, 1.0);
  gl_Position = projectionMatrix * mv;
  float cintila = 0.75 + 0.25 * sin(uTempo * (0.6 + aFase * 2.0) + aFase * 30.0);
  // aTamanho é em unidades de mundo: de longe a estrela encolhe, de perto
  // cresce — e é por isso que dar zoom parece voar para dentro do disco.
  gl_PointSize = clamp(aTamanho * uProj / -mv.z, 1.2, uMax);
  vCor = aCor * (1.0 + faixa * 1.5);
  vAlfa = cintila * smoothstep(0.0, 120.0, -mv.z);
}`;

const FUNDO_VERT = /* glsl */ `
attribute float aTamanho;
attribute vec3  aCor;
attribute float aFase;
uniform float uTempo;
uniform float uPx;
varying vec3 vCor;
varying float vAlfa;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = aTamanho * uPx;
  vCor = aCor;
  vAlfa = 0.55 + 0.45 * sin(uTempo * (0.3 + aFase) + aFase * 50.0);
}`;

const POEIRA_VERT = /* glsl */ `
attribute float aTamanho;
attribute float aFase;
uniform float uTempo;
uniform float uPx;
uniform vec3  uAlvo;
uniform float uDist;
varying vec3 vCor;
varying float vAlfa;
void main() {
  // a poeira flutua presa à câmera (em volta do ponto que você está olhando),
  // mas desliza devagar; passa na frente das ideias quando você anda.
  vec3 p = position * vec3(uDist * 1.6, uDist * 1.0, uDist * 0.75);
  p.x += sin(uTempo * 0.05 + aFase * 6.28) * 40.0;
  p.y += cos(uTempo * 0.04 + aFase * 9.0) * 30.0;
  vec4 mv = modelViewMatrix * vec4(uAlvo + p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = clamp(aTamanho * uPx * (600.0 / -mv.z), 2.0, 90.0);
  vCor = vec3(0.62, 0.64, 0.95);
  vAlfa = 0.06 * smoothstep(0.0, 200.0, -mv.z);
}`;

// ─────────────────────────────── geração ───────────────────────────────

function gauss() {
  // Box–Muller: espalhamento que concentra no meio, como poeira de verdade
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function misturar(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

function gerarGalaxia(n) {
  const raio = new Float32Array(n), angulo = new Float32Array(n), altura = new Float32Array(n);
  const tamanho = new Float32Array(n), cor = new Float32Array(n * 3), fase = new Float32Array(n);
  const BRACOS = 4, GIRO = 3.3;
  const nucleo = [1.0, 0.92, 0.80], meio = [0.66, 0.69, 1.0], borda = [0.30, 0.27, 0.62], rosa = [1.0, 0.55, 0.78];

  for (let i = 0; i < n; i++) {
    let r, a, y, c, s;
    const sorte = Math.random();
    if (sorte < 0.16) {
      // bojo: uma bola achatada de estrelas velhas, amareladas
      r = Math.abs(gauss()) * 0.07;
      a = Math.random() * Math.PI * 2;
      y = gauss() * 0.035 * (1 - r * 4);
      c = misturar(nucleo, [1, 0.8, 0.6], Math.random() * 0.5).map((v) => v * 0.7);
      s = 2.5 + Math.random() * 3;
    } else {
      r = Math.pow(Math.random(), 1.35) * 1.05 + 0.02;
      const braco = (i % BRACOS) / BRACOS * Math.PI * 2;
      const dispersao = gauss() * (0.07 + r * 0.16);
      a = braco + Math.log(r + 0.08) * GIRO + dispersao;
      y = gauss() * 0.018 * (1.2 - r);
      const t = Math.min(1, r / 0.9);
      c = t < 0.4 ? misturar(nucleo, meio, t / 0.4) : misturar(meio, borda, (t - 0.4) / 0.6);
      s = 2.2 + Math.random() * 3.6;
      // berçários: pontos rosados, um pouco maiores, só perto do braço
      if (Math.random() < 0.025 && Math.abs(dispersao) < 0.1 && r > 0.18) { c = rosa; s *= 1.4; }
      // umas poucas gigantes azuis, bem brilhantes
      if (Math.random() < 0.004) { c = [0.8, 0.88, 1.0]; s *= 3.2; }
    }
    raio[i] = r; angulo[i] = a; altura[i] = y; tamanho[i] = s; fase[i] = Math.random();
    const brilho = 0.55 + Math.random() * 0.45;
    cor[i * 3] = c[0] * brilho; cor[i * 3 + 1] = c[1] * brilho; cor[i * 3 + 2] = c[2] * brilho;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
  geo.setAttribute("aRaio", new THREE.BufferAttribute(raio, 1));
  geo.setAttribute("aAngulo", new THREE.BufferAttribute(angulo, 1));
  geo.setAttribute("aAltura", new THREE.BufferAttribute(altura, 1));
  geo.setAttribute("aTamanho", new THREE.BufferAttribute(tamanho, 1));
  geo.setAttribute("aCor", new THREE.BufferAttribute(cor, 3));
  geo.setAttribute("aFase", new THREE.BufferAttribute(fase, 1));
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e7);
  return geo;
}

// As nuvens dos braços: poucas partículas ENORMES e quase transparentes,
// seguindo a mesma espiral. Somadas, viram o brilho contínuo do braço — é
// isso que desenha a forma da galáxia, e não as estrelas uma a uma.
function gerarNuvens(n) {
  const raio = new Float32Array(n), angulo = new Float32Array(n), altura = new Float32Array(n);
  const tamanho = new Float32Array(n), cor = new Float32Array(n * 3), fase = new Float32Array(n);
  const BRACOS = 4, GIRO = 3.3;
  const paleta = [[0.55, 0.58, 1.0], [0.70, 0.55, 0.95], [0.95, 0.55, 0.80], [0.45, 0.75, 0.90], [1.0, 0.85, 0.70]];
  for (let i = 0; i < n; i++) {
    const centro = i < n * 0.08;
    const r = centro ? Math.abs(gauss()) * 0.07 : Math.pow(Math.random(), 1.1) * 0.95 + 0.04;
    const braco = (i % BRACOS) / BRACOS * Math.PI * 2;
    raio[i] = r;
    angulo[i] = centro ? Math.random() * 6.283 : braco + Math.log(r + 0.08) * GIRO + gauss() * (0.05 + r * 0.1);
    altura[i] = gauss() * 0.01;
    tamanho[i] = centro ? 140 + Math.random() * 160 : (60 + Math.random() * 150) * (1.1 - r * 0.5);
    fase[i] = Math.random();
    const c = centro ? paleta[4] : paleta[Math.floor(Math.random() * 4)];
    const b = centro ? 0.7 : 0.6 + Math.random() * 0.6;
    cor[i * 3] = c[0] * b; cor[i * 3 + 1] = c[1] * b; cor[i * 3 + 2] = c[2] * b;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
  geo.setAttribute("aRaio", new THREE.BufferAttribute(raio, 1));
  geo.setAttribute("aAngulo", new THREE.BufferAttribute(angulo, 1));
  geo.setAttribute("aAltura", new THREE.BufferAttribute(altura, 1));
  geo.setAttribute("aTamanho", new THREE.BufferAttribute(tamanho, 1));
  geo.setAttribute("aCor", new THREE.BufferAttribute(cor, 3));
  geo.setAttribute("aFase", new THREE.BufferAttribute(fase, 1));
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e7);
  return geo;
}

function gerarFundo(n) {
  const pos = new Float32Array(n * 3), tamanho = new Float32Array(n), cor = new Float32Array(n * 3), fase = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2, r = 30000 + Math.random() * 15000;
    const s = Math.sqrt(1 - u * u);
    pos[i * 3] = s * Math.cos(th) * r; pos[i * 3 + 1] = s * Math.sin(th) * r; pos[i * 3 + 2] = u * r;
    const k = Math.random();
    const c = k < 0.7 ? [0.82, 0.86, 1.0] : k < 0.92 ? [1.0, 0.93, 0.8] : [1.0, 0.72, 0.7];
    const b = 0.4 + Math.pow(Math.random(), 3) * 0.9;
    cor[i * 3] = c[0] * b; cor[i * 3 + 1] = c[1] * b; cor[i * 3 + 2] = c[2] * b;
    tamanho[i] = 1.2 + Math.pow(Math.random(), 6) * 4.5;
    fase[i] = Math.random();
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aTamanho", new THREE.BufferAttribute(tamanho, 1));
  geo.setAttribute("aCor", new THREE.BufferAttribute(cor, 3));
  geo.setAttribute("aFase", new THREE.BufferAttribute(fase, 1));
  return geo;
}

function gerarPoeira(n) {
  const pos = new Float32Array(n * 3), tamanho = new Float32Array(n), fase = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    pos[i * 3] = Math.random() - 0.5; pos[i * 3 + 1] = Math.random() - 0.5;
    pos[i * 3 + 2] = 0.15 + Math.random() * 0.75;          // entre o disco e a câmera
    tamanho[i] = 10 + Math.random() * 40; fase[i] = Math.random();
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aTamanho", new THREE.BufferAttribute(tamanho, 1));
  geo.setAttribute("aFase", new THREE.BufferAttribute(fase, 1));
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e7);
  return geo;
}

// ─────────────────────────────── a cena ───────────────────────────────

function criar(canvas) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: "high-performance" });
  } catch (erro) {
    console.warn("[cosmos] sem WebGL2:", erro);
    return null;
  }
  const modesta = (navigator.hardwareConcurrency || 4) <= 4 || /Mobi|Android/i.test(navigator.userAgent);
  let proporcao = Math.min(window.devicePixelRatio || 1, modesta ? 1 : 1.5);
  renderer.setPixelRatio(proporcao);
  // Sem tone mapping nem conversão de cor no fim: as cores dos shaders já
  // foram escolhidas no espaço da tela. Converter de novo deixaria o escuro
  // da madrugada lavado, cinza.
  renderer.toneMapping = THREE.NoToneMapping;

  const cena = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(50, 1, 10, 80000);
  const FOV = THREE.MathUtils.degToRad(50);

  // gás: um triângulo que cobre a tela, desenhado antes de tudo
  const uGas = {
    uRes: { value: new THREE.Vector2(1, 1) }, uEscala: { value: 1 }, uCam: { value: new THREE.Vector3(0, 0, 1) },
    uTempo: { value: 0 }, uBorda: { value: 1000 }, uPulso: { value: new THREE.Vector2(0, 0) }, uClaro: { value: 0 },
    uMatiz: { value: new THREE.Vector2(0, 1) },
  };
  const triangulo = new THREE.BufferGeometry();
  triangulo.setAttribute("position", new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
  const gas = new THREE.Mesh(triangulo, new THREE.ShaderMaterial({
    vertexShader: GAS_VERT, fragmentShader: GAS_FRAG, uniforms: uGas, depthWrite: false, depthTest: false,
  }));
  gas.frustumCulled = false;
  gas.renderOrder = -10;
  cena.add(gas);

  const uComum = { uTempo: uGas.uTempo, uPx: { value: proporcao }, uClaro: uGas.uClaro, uMatiz: uGas.uMatiz };
  function materialPontos(vert, extras = {}, frag = ESTRELA_FRAG) {
    return new THREE.ShaderMaterial({
      vertexShader: vert, fragmentShader: frag,
      uniforms: { ...uComum, ...extras },
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
  }

  const fundo = new THREE.Points(gerarFundo(modesta ? 4000 : 9000), materialPontos(FUNDO_VERT));
  fundo.renderOrder = -5;
  cena.add(fundo);

  // A galáxia fica inclinada: um disco visto em perspectiva, não de cima.
  const grupo = new THREE.Group();
  grupo.rotation.set(-0.9, 0, 0.32);
  const uProj = { value: 1 };
  const uGal = { uBorda: { value: 1000 }, uPulso: uGas.uPulso, uProj, uMax: { value: 48 } };
  const uNuv = { uBorda: uGal.uBorda, uPulso: uGas.uPulso, uProj, uMax: { value: 700 } };
  const nuvens = new THREE.Points(gerarNuvens(modesta ? 1400 : 2600), materialPontos(GALAXIA_VERT, uNuv, NUVEM_FRAG));
  nuvens.renderOrder = -2;
  grupo.add(nuvens);
  const galaxia = new THREE.Points(gerarGalaxia(modesta ? 32000 : 70000), materialPontos(GALAXIA_VERT, uGal));
  grupo.add(galaxia);

  // Fumaça: duas lâminas no plano do disco, um pouco acima e abaixo, com
  // sementes diferentes — a diferença de altura dá profundidade quando a
  // galáxia inclina. Em aparelho modesto, uma lâmina e menos oitavas.
  const placa = new THREE.PlaneGeometry(2, 2);
  const fumacas = (modesta ? [[0, 0.09, 1.3]] : [[0.035, 0.075, 1.3], [-0.03, 0.05, 7.9]]).map(([z, forca, semente]) => {
    const m = new THREE.Mesh(placa, new THREE.ShaderMaterial({
      vertexShader: FUMACA_VERT,
      fragmentShader: FUMACA_FRAG.replace("OITAVAS", modesta ? "4" : "5"),
      uniforms: { uTempo: uGas.uTempo, uClaro: uGas.uClaro, uMatiz: uGas.uMatiz, uSemente: { value: semente }, uForca: { value: forca } },
      transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    }));
    m.userData.z = z;
    m.rotation.x = Math.PI / 2;          // o disco da galáxia é o plano xz
    m.renderOrder = -1;
    m.frustumCulled = false;
    grupo.add(m);
    return m;
  });
  cena.add(grupo);

  const uPo = { uAlvo: { value: new THREE.Vector3() }, uDist: { value: 1000 } };
  const poeira = new THREE.Points(gerarPoeira(modesta ? 60 : 140), materialPontos(POEIRA_VERT, uPo));
  poeira.renderOrder = 5;
  cena.add(poeira);

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(cena, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.85, 0.65, 0.12);
  composer.addPass(bloom);

  const reduzido = window.matchMedia("(prefers-reduced-motion: reduce)");
  const estado = { paleta: [0, 1], cam: { x: 0, y: 0, s: 1 }, borda: 1000, pulso: -1, sujo: true, visivel: () => true, mouse: { x: 0, y: 0 }, inclina: { x: 0, y: 0 } };
  const t0 = performance.now();
  let ultimo = 0, lentos = 0, claroAtual = -1;

  window.addEventListener("pointermove", (e) => {
    estado.mouse.x = e.clientX / window.innerWidth - 0.5;
    estado.mouse.y = e.clientY / window.innerHeight - 0.5;
  }, { passive: true });

  function tamanhoTela() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return null;
    const atual = renderer.getSize(new THREE.Vector2());
    if (atual.x !== w || atual.y !== h) {
      renderer.setSize(w, h, false);
      composer.setSize(w, h);
      bloom.resolution.set(w / 2, h / 2);
    }
    return { w, h };
  }

  function aplicarTema() {
    const claro = document.documentElement.dataset.theme === "light" ? 1 : 0;
    if (claro === claroAtual) return;
    claroAtual = claro;
    uGas.uClaro.value = claro;
    for (const p of [fundo, nuvens, galaxia, poeira, ...fumacas]) {
      p.material.blending = claro ? THREE.NormalBlending : THREE.AdditiveBlending;
      p.material.needsUpdate = true;
    }
    bloom.enabled = !claro;
  }

  function desenhar(agora) {
    const t = tamanhoTela();
    if (!t) return;
    aplicarTema();
    const tempo = reduzido.matches ? 0 : (agora - t0) / 1000;
    uGas.uTempo.value = tempo;
    // a paleta muda devagar, como o céu mudando de hora
    const m = uGas.uMatiz.value, alvo = estado.paleta;
    const dh = alvo[0] - m.x, ds = alvo[1] - m.y;
    if (reduzido.matches) m.set(alvo[0], alvo[1]);      // sem animação: troca na hora
    else if (Math.abs(dh) > 0.002 || Math.abs(ds) > 0.002) { m.x += dh * 0.08; m.y += ds * 0.08; }

    // a câmera: plano z=0 da cena = mundo da Nebulosa, pixel a pixel
    const { x, y, s } = estado.cam;
    const alvoX = (t.w / 2 - x) / s, alvoY = -((t.h / 2 - y) / s);
    const dist = (t.h / s) / (2 * Math.tan(FOV / 2));
    camera.aspect = t.w / t.h;
    camera.position.set(alvoX, alvoY, dist);
    camera.lookAt(alvoX, alvoY, 0);
    camera.near = Math.max(1, dist * 0.02);
    camera.updateProjectionMatrix();
    fundo.position.copy(camera.position);   // a casca de estrelas viaja com você

    // o mouse inclina a galáxia de leve; a inclinação segue com atraso
    estado.inclina.x += (estado.mouse.y * 0.06 - estado.inclina.x) * 0.04;
    estado.inclina.y += (estado.mouse.x * 0.08 - estado.inclina.y) * 0.04;
    grupo.rotation.x = -0.9 + estado.inclina.x;
    grupo.rotation.y = estado.inclina.y;
    fundo.rotation.y = tempo * 0.0015 + estado.inclina.y * 0.3;
    fundo.rotation.x = estado.inclina.x * 0.3;

    uProj.value = (t.h * proporcao) / (2 * Math.tan(FOV / 2));
    uGas.uRes.value.set(t.w * proporcao, t.h * proporcao);
    uGas.uEscala.value = proporcao;
    uGas.uCam.value.set(x, y, s);
    uGas.uBorda.value = estado.borda;
    uGal.uBorda.value = estado.borda * 1.5;
    for (const f of fumacas) {
      const raio = estado.borda * 1.5;
      f.scale.set(raio, raio, 1);
      f.position.y = f.userData.z * raio;  // um pouco acima/abaixo do disco
    }
    uPo.uAlvo.value.set(alvoX, alvoY, 0);
    uPo.uDist.value = dist;

    let pulsoT = 0, pulsoF = 0;
    if (estado.pulso >= 0) {
      pulsoT = (agora - estado.pulso) / 1000;
      pulsoF = Math.max(0, 1 - pulsoT / 2.8);
      if (pulsoF <= 0) estado.pulso = -1;
    }
    uGas.uPulso.value.set(pulsoT, pulsoF);
    bloom.strength = 0.85 + pulsoF * 1.1;

    const inicio = performance.now();
    composer.render();
    // Quadro lento demais várias vezes seguidas: baixa a resolução.
    if (performance.now() - inicio > 28 && ++lentos > 20 && proporcao > 0.6) {
      proporcao = Math.max(0.6, proporcao - 0.25);
      renderer.setPixelRatio(proporcao);
      uComum.uPx.value = proporcao;
      renderer.setSize(t.w, t.h, false);
      composer.setPixelRatio(proporcao);
      composer.setSize(t.w, t.h);
      lentos = 0;
    }
    estado.sujo = false;
  }

  let quadro = null;
  function laco(agora) {
    quadro = requestAnimationFrame(laco);
    if (document.hidden || !estado.visivel() || canvas.clientWidth === 0) return;
    const animando = !reduzido.matches || estado.pulso >= 0;
    if (!estado.sujo && (!animando || agora - ultimo < 15)) return;
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
    paletas: Object.keys(PALETAS),
    matiz(giro, saturacao) { estado.paleta = [giro, saturacao]; estado.sujo = true; },
    cores(nome) {
      estado.paleta = PALETAS[nome] || PALETAS.crepusculo;
      estado.sujo = true;
    },
  };
}

// Avisa a Nebulosa. Se o Three não conseguir (sem WebGL2), fica o galaxia.js.
window.Cosmos = { criar };
window.dispatchEvent(new Event("cosmos-pronto"));
