# Fontes da interface

Servidas pela Livia, em `/fontes/`, e não pelo Google.

Um `<link>` para `fonts.googleapis.com` quebraria duas promessas do projeto de
uma vez: contaria ao Google toda vez que o André abrisse a assistente, e
deixaria a tipografia sumir quando ele estivesse sem rede. São 76 KB no total,
e o service worker os guarda junto do resto da casca offline.

| Arquivo | Família | Papel | Licença |
|---|---|---|---|
| `marcellus.woff2` | Marcellus | títulos importantes | SIL OFL 1.1 |
| `geist.woff2` | Geist (variável, 100–900) | interface e texto | SIL OFL 1.1 |
| `geistmono.woff2` | Geist Mono (variável, 100–900) | dados e código | SIL OFL 1.1 |

Todos são o subconjunto `latin` do Google Fonts, que cobre o Latin-1 inteiro —
`ã`, `õ`, `ç`, `á`, `ê` e companhia. O `latin-ext` foi deixado de fora porque
não acrescenta nada para o português e custaria mais 25 KB.

Geist e Geist Mono são variáveis: um arquivo por família cobre todos os pesos,
e por isso o `@font-face` declara `font-weight: 100 900`.

Para atualizar, pegue a URL do `.woff2` no CSS que o Google Fonts serve e baixe
o arquivo. Se o nome mudar, ajuste também a lista de `permitidas` em
`livia/server.py` e a casca em `web/sw.js` — as duas usam lista fechada.
