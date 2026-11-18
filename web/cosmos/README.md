# cosmos — a galáxia 3D da Nebulosa

`cosmos.src.js` é a fonte. O navegador carrega `web/cosmos.js`, que é ela
empacotada junto com as partes do [Three.js](https://threejs.org) que usa
(licença MIT, o aviso vai dentro do arquivo gerado).

Empacotado, e não por CDN, de propósito: a Livia é instalável e abre sem
internet; uma galáxia que depende de servidor de terceiros sumiria
justamente quando o app está offline.

## Gerar de novo depois de editar

```bash
cd web/cosmos
npm install          # só da primeira vez: three + esbuild
npm run build        # escreve ../cosmos.js
```

O `package.json` fixa a versão do Three; subir a versão é trocar ali e
rodar o build. O resultado é um arquivo só, minificado, com só o que é usado.
