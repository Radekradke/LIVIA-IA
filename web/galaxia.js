// A nebulosa usa coordenadas do mesmo mundo que as ideias. A câmera pode
// passar pelo núcleo sem que a textura fique presa ao centro da janela.
function instalarGalaxia(canvas, camera) {
  const gl = canvas.getContext("webgl", { alpha: false, antialias: false });
  if (!gl) return;
  const vertex = `attribute vec2 pos;
    void main(){ gl_Position = vec4(pos, 0., 1.); }`;
  const fragment = `precision highp float;
    uniform vec2 viewport, centro;
    uniform float escala, tempo;
    float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
    float noise(vec2 p){
      vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f);
      return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),
                 mix(hash(i+vec2(0,1)),hash(i+1.),f.x),f.y);
    }
    float poeira(vec2 p){
      float v=0., a=.5;
      for(int i=0;i<5;i++){v+=a*noise(p);p=mat2(1.7,1.1,-1.1,1.7)*p+3.7;a*=.5;}
      return v;
    }
    void main(){
      vec2 pixel=vec2(gl_FragCoord.x,viewport.y-gl_FragCoord.y);
      vec2 p=(pixel-centro)/escala;
      p=mat2(.93,.37,-.37,.93)*p;
      p.y*=1.36;
      float r=length(p), a=atan(p.y,p.x+.00001);
      float dust=poeira(8.*p);
      float arms=.5+.5*cos(3.*a-7.*log(r+.12)+dust*6.);
      float gas=pow(arms,2.)*exp(-2.*r)*smoothstep(.21,.8,dust);
      vec3 color=vec3(.019,.023,.039)+mix(vec3(.28,.13,.76),vec3(.72,.16,.58),dust)*gas;
      color+=vec3(1.,.62,.19)*(exp(-r*20.)+.20*exp(-r*5.));
      vec2 field=(pixel-centro*.28)/9.;
      vec2 cell=floor(field); float id=hash(cell);
      vec2 point=vec2(hash(cell+7.),hash(cell+13.))*.7+.15;
      color+=vec3(.70,.78,1.)*step(.79,id)*exp(-17.*length(fract(field)-point))*(.7+.25*sin(tempo+id*80.));
      gl_FragColor=vec4(color,1.);
    }`;
  const programa = gl.createProgram();
  for (const [tipo, fonte] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]]) {
    const shader = gl.createShader(tipo);
    gl.shaderSource(shader, fonte);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return;
    gl.attachShader(programa, shader);
  }
  gl.linkProgram(programa);
  if (!gl.getProgramParameter(programa, gl.LINK_STATUS)) return;
  gl.useProgram(programa);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,3,-1,-1,3]), gl.STATIC_DRAW);
  const pos = gl.getAttribLocation(programa, "pos");
  gl.enableVertexAttribArray(pos);
  gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);
  const loc = Object.fromEntries(["viewport","centro","escala","tempo"].map(k => [k, gl.getUniformLocation(programa,k)]));
  const reduzido = matchMedia("(prefers-reduced-motion: reduce)");
  function desenhar(tempo) {
    const w = Math.round(canvas.clientWidth / 2), h = Math.round(canvas.clientHeight / 2);
    if (!w || !h) return;
    if (w !== canvas.width || h !== canvas.height) { canvas.width=w; canvas.height=h; }
    gl.viewport(0,0,w,h);
    gl.uniform2f(loc.viewport,w,h);
    gl.uniform2f(loc.centro,camera.x/2,camera.y/2);
    gl.uniform1f(loc.escala, 500*camera.s);
    gl.uniform1f(loc.tempo,reduzido.matches ? 0 : tempo/3500);
    gl.drawArrays(gl.TRIANGLES,0,3);
    canvas.style.opacity="1";
  }
  let frame = 0, visivel = false;
  function atualizar() {
    if (!frame && visivel && !document.hidden) frame = requestAnimationFrame(t => {frame=0; desenhar(t);});
  }
  new IntersectionObserver(([e]) => {visivel=e.isIntersecting; atualizar();}).observe(canvas);
  new ResizeObserver(atualizar).observe(canvas);
  window.setInterval(() => { if (!reduzido.matches) atualizar(); }, 150);
  document.addEventListener("visibilitychange",atualizar);
  return atualizar;
}
