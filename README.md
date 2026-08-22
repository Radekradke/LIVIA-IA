# Livia

Assistente pessoal de projetos, com memória, rodando na sua máquina.
Feita para 1-2 usuários. Sem servidor, sem Docker, sem conta em lugar nenhum —
e, se você quiser, **sem internet**: com o Ollama ligado ela funciona inteira
na sua máquina, incluindo a busca por significado na memória e na biblioteca.

---

## Como colocar para rodar

Você precisa de **um** provedor de IA. Escolha o caminho:

### Caminho A — tudo local (sem chave, sem cota, nada sai da máquina)

**1. Instale o Ollama** em https://ollama.com e baixe os modelos:

```bash
ollama pull qwen3:8b            # conversa
ollama pull qwen3:4b            # tarefas rápidas (triagem de memória)
ollama pull nomic-embed-text    # busca por significado
```

**2. Configure:**

```bash
copy .env.example .env
```

No `.env`, ponha `LIVIA_OLLAMA=1`. Se quiser garantir que nada saia da máquina
nunca, ponha também `LIVIA_LOCAL_ONLY=1`.

### Caminho B — nuvem gratuita

**1. Pegue a chave gratuita** em https://aistudio.google.com/apikey
(precisa de uma conta Google; não pede cartão).

**2. Configure:**

```bash
copy .env.example .env
```

Cole a chave em `GEMINI_API_KEY`.

### E então, nos dois casos

Coloque seu nome em `LIVIA_USER` — a Livia usa isso para se dirigir a você.

**3. Instale as dependências** (só na primeira vez):

```bash
python -m pip install -r requirements.txt
```

**4. Rode:**

```bash
python run.py
```

O navegador abre sozinho em http://127.0.0.1:8100.

> Dá para combinar os dois: com o Ollama ligado **e** uma chave configurada,
> ela usa o local primeiro e cai para a nuvem quando o local não dá conta.
> É a configuração padrão do `.env.example`.

---

## A ideia central

> **O modelo de IA não aprende nada. Quem aprende é este programa.**

Vale insistir nisso porque é contraintuitivo. O modelo é reiniciado do zero a
cada pergunta — ele não guarda absolutamente nada entre uma conversa e outra.

O que cria a ilusão de memória é simples: quando aparece algo que vale guardar,
o programa **escreve um arquivo de texto** em `data/memory/`. Na pergunta
seguinte, ele relê esses arquivos e cola o conteúdo junto do seu prompt. O
modelo "lembra" porque você contou de novo — automaticamente, sem você perceber.

Consequências práticas disso:

- A memória é **um monte de arquivos `.md`** que você pode abrir, editar e
  apagar no bloco de notas. Nada de banco misterioso.
- Ela funciona **independente do modelo** que estiver por trás. Trocar o Gemini
  por outro não apaga nada.
- Só as memórias **relacionadas à pergunta** entram no prompt. Ela compara
  significados, não palavras: perguntar "qual banco a gente usa?" encontra a
  memória sobre Supabase mesmo sem a palavra "Supabase" na pergunta.

Essa última parte é a mudança mais importante da versão atual. Antes, **toda**
memória entrava em **toda** conversa — o que funciona com vinte e quebra com
trezentas: o orçamento estoura, as memórias boas competem com as irrelevantes,
e acumular conhecimento passa a *piorar* as respostas. Um sistema que fica pior
quanto mais aprende está quebrado por dentro.

### As quatro coisas que ela guarda, e por que são separadas

| | O que é | Onde vive |
|---|---|---|
| **Memória** | O que é verdade sobre você | `data/memory/*.md` |
| **Skill** | Como fazer algo, ensinado por você | `data/skills/*.md` |
| **Lição** | O que ela concluiu das próprias tentativas | `data/lessons/*.md` |
| **Experiência** | O que já foi tentado e como terminou | `livia.db` |
| **Conhecimento** | Documentos e projetos que ela consulta | `data/biblioteca/` |

Misturar tudo num balaio só ("coisas que ela sabe") tornaria impossível
responder à pergunta que mais importa quando algo sai errado: **isso veio de
você ou foi ela que concluiu sozinha?**

Os arquivos `.md` continuam sendo a fonte da verdade — abra, corrija, apague.
O que fica no SQLite é só o derivado: vetores, contadores de uso, carimbos.
Apagar `livia.db` custa o histórico de conversas e as experiências, mas **não
apaga nada do que você escreveu ou ensinou**.

---

## Colocar na internet

### Primeiro: a senha

Sem `LIVIA_PASSWORD` no `.env`, o servidor **só atende quem vem da própria
máquina** — pedidos de fora levam 403. Isso vale mesmo em Docker ou hospedagem,
porque a trava está no servidor, não no `run.py`.

```
LIVIA_PASSWORD=uma-senha-longa-e-aleatoria
LIVIA_HTTPS=1          # quando o acesso for por https
```

Uma senha, um cookie assinado, 30 dias de sessão. Não é sistema de contas — é a
fechadura de uma porta que só você atravessa.

### Segundo: a armadilha do disco

**Hospedagem gratuita quase sempre tem disco efêmero.** O app sobe, funciona
por dias, e numa atualização qualquer o disco volta ao estado inicial. Memórias,
skills, personalidade e conversas somem sem aviso — justamente o que dá valor a
este projeto.

Três saídas, em ordem de honestidade:

| Caminho | Custo | Ressalva |
|---|---|---|
| VPS de verdade (Oracle Always Free, ou ~US$5/mês) | grátis a barato | Oracle pede cartão para verificar identidade, mas não cobra |
| Hospedagem grátis **com volume** montado em `/data` | grátis | Verifique se o plano gratuito ainda inclui volume — isso muda com frequência |
| Hospedagem grátis + backup manual | grátis | Você precisa lembrar de baixar o backup |

Não confie na minha lista de planos gratuitos: eles mudam toda hora. Confira no
site do provedor antes de escolher.

### O backup

Painel lateral → aba **Jeito** → **Backup**. Baixa um `.zip` com memórias,
skills, personalidade e conversas. Restaurar sobrescreve o que tiver mesmo nome
e mantém o resto — nunca apaga memória criada depois.

O zip **não** leva o `.env`: chave de API não passeia em arquivo de backup.

### Docker

```bash
docker build -t livia .
docker run -d -p 8100:8100 \
  -v livia-dados:/data \
  -e GEMINI_API_KEY=... \
  -e GROQ_API_KEY=... \
  -e LIVIA_PASSWORD=... \
  -e LIVIA_HTTPS=1 \
  livia
```

O `-v livia-dados:/data` é o que impede a perda dos dados. Sem ele, tudo some
quando o contêiner for recriado.

Hospedagens injetam a porta na variável `PORT`, e o app respeita isso.
`/saude` responde sem senha, para o health check do provedor.

### Alternativa: túnel, sem hospedar nada

Se o PC puder ficar ligado, um túnel do Cloudflare dá um endereço HTTPS público
apontando para a sua máquina. Grátis, sem cartão, e **os dados nunca saem
daqui** — o problema do disco efêmero simplesmente não existe. A limitação é
óbvia: PC desligado, endereço fora do ar.

---

## Provedores e fallback

Depender de um provedor gratuito só é frágil: quando a cota estoura, a
assistente para. Por isso a conversa tenta os provedores em ordem.

```
1. Ollama (local)          · grátis, sem cota, nada sai da máquina
   ↓ servidor desligado, modelo não baixado, timeout
2. Groq gpt-oss-120b       ~1,2s · rápido, 1000 pedidos/dia
   ↓ cota estourada, servidor fora
3. Gemini 3.6 Flash        ~4s   · abre e lê links sozinho
   ↓ falhou também
4. OpenRouter free         · só texto
   ↓
5. mensagem dizendo o que houve com cada um
```

O Ollama fica desligado por padrão (`LIVIA_OLLAMA=0`): ligar sem o servidor
instalado só faria a Livia tentar uma conexão recusada antes de seguir. Com ele
desligado, a ordem efetiva é a de sempre — Groq, Gemini, OpenRouter.

A troca é silenciosa e você vê um aviso discreto quando o reserva responde.

**Erro de configuração não aciona fallback.** Chave errada ou modelo
inexistente sobem na hora — trocar de provedor só esconderia o problema real.

**A ordem é sua.** `LIVIA_PROVIDERS=groq,gemini` inverte: a Groq é cerca de 3×
mais rápida, mas só o Gemini abre links por conta própria (a busca no
DuckDuckGo funciona nos dois, porque entra como texto no prompt).

**Capacidade é filtro, não preferência.** O modelo local não recebe uma tarefa
que ele não sabe cumprir. Ferramentas com Ollama só são oferecidas quando você
confirma em `LIVIA_OLLAMA_TOOLS=1` — vários modelos aceitam o parâmetro `tools`
e o ignoram, e a resposta sai dizendo que fez sem ter feito. Preferimos não
declarar a capacidade a declará-la e mentir.

### Modo totalmente local

```bash
LIVIA_LOCAL_ONLY=1
```

Com isso os provedores de nuvem são **removidos** da fila, não
despriorizados. Nem chat, nem embeddings, nem busca automática: a web nasce
desligada nesse modo (dá para religar com `LIVIA_WEB=1` de propósito).

Se o modelo local não estiver baixado, a mensagem diz exatamente o que rodar:

```
Ollama está ativo, mas o modelo qwen3:8b não foi encontrado. Rode no terminal:

    ollama pull qwen3:8b

(não baixo sozinha: são gigabytes na sua conexão)
```

Modelos testados na Groq, em agosto de 2026:

| Modelo | Tempo | Veredito |
|---|---|---|
| `openai/gpt-oss-120b` | ~1,2s | ✅ padrão |
| `openai/gpt-oss-20b` | ~1,2s | ✅ usado no filtro de memória |
| `groq/compound` | ~3,6s | ✅ funciona, mais lento |
| `qwen/qwen3.6-27b` | ~1,6s | ❌ vaza `<think>` na resposta e responde em inglês |

---

## Ferramentas: o que ela consegue fazer

Além de conversar, ela age. Quando a resposta depende de olhar ou mexer em
algo, ela pede a ação e usa o resultado:

| Ferramenta | Para quê |
|---|---|
| `listar_arquivos` | ver o que existe antes de chutar um nome |
| `ler_arquivo` | ler o conteúdo em vez de supor |
| `escrever_arquivo` | criar ou atualizar um arquivo de texto |
| `calcular` | conta exata — modelo erra aritmética de cabeça |

Cada ação aparece como uma linha na resposta. Ficam à vista de propósito: se
ela mexeu num arquivo seu, você precisa saber qual.

### O confinamento

**Tudo acontece dentro de uma pasta só**, definida em `LIVIA_WORKSPACE`
(padrão: `data/workspace`). Antes de qualquer leitura ou escrita o caminho é
resolvido para a forma canônica e conferido — `../`, caminho absoluto e link
simbólico são recusados.

Isso não é paranoia: o caminho vem do modelo, e modelo erra. Um `../.env`
pedido sem má intenção nenhuma leria a sua chave de API.

Se quiser que ela trabalhe num projeto real, aponte `LIVIA_WORKSPACE` para
lá — sabendo que ela poderá sobrescrever arquivos daquela pasta. **Toda
sobrescrita guarda uma cópia** da versão anterior ao lado, com carimbo de
data no nome.

### O que ela NÃO faz

**Executar código.** Não existe jeito honesto de isolar isso em Python puro:
código gerado pelo modelo teria exatamente os seus poderes na máquina. Fazer
direito exige contêiner ou aprovação humana a cada execução — e isso é
decisão sua, não padrão silencioso.

A calculadora, por isso, não usa `eval`: ela interpreta a árvore sintática e
só aceita `+ - * / // % **` sobre números. `__import__('os').system(...)` é
recusado como expressão inválida.

---

## Acesso à web

Funciona de três formas, e você não precisa pedir nas duas primeiras:

| Situação | O que acontece | Custo |
|---|---|---|
| Você cola um link | Ela abre e lê a página antes de responder | grátis |
| A pergunta depende de algo atual | Ela busca sozinha e mostra as fontes | 1 chamada extra |
| `/buscar <termo>` | Força a busca quando ela não achou necessário | — |

Por baixo são dois mecanismos diferentes:

- **Ler link:** ferramenta nativa do Gemini (`url_context`). Gratuita.
- **Buscar:** DuckDuckGo, via a biblioteca `ddgs`. A busca nativa do Google
  (`google_search`) existe na API mas devolve **429 em conta gratuita** — é
  recurso pago. O DDG faz o mesmo papel sem chave e sem cadastro.

A ressalva do DuckDuckGo: não é API oficial, é leitura da página de resultados.
Funciona bem, mas pode quebrar quando eles mudarem o HTML. Quando isso
acontecer, a busca falha sozinha e a conversa segue sem ela — nunca derruba a
resposta.

**Sobre a cota gratuita.** A detecção automática ("essa pergunta precisa da
web?") gasta uma chamada de API a mais por mensagem. Se você estiver batendo no
limite diário, ponha `LIVIA_WEB_AUTO=0` no `.env`: a web continua funcionando,
mas só quando você pede — link colado ou `/buscar`. Para desligar tudo,
`LIVIA_WEB=0`.

---

## Trocando o nome e o visual

**O nome** está no `.env`, em `LIVIA_NAME`. Troque, reinicie o servidor, e o
título da página, o cabeçalho, o ícone da aba do navegador e o prompt se
ajustam sozinhos. O ícone é um monograma gerado na hora com a primeira letra —
não existe arquivo de imagem para trocar.

**O tema** alterna no rodapé da coluna esquerda: claro ou escuro. O padrão de
fábrica é claro, e a sua escolha fica guardada no navegador. O modo escuro do
Windows não sequestra o visual sem você pedir.

---

## Mudando a personalidade dela

Aba **Jeito**, no painel lateral. É um texto livre que descreve o tom, o formato
das respostas e as manias dela. Salvou, já vale na mensagem seguinte — sem
reiniciar nada.

O arquivo fica em `data/personalidade.md`, então dá para editar no bloco de notas
também, se preferir.

**O que fica de fora dali de propósito:** as regras de honestidade (admitir
quando não sabe, não inventar dados, não fingir acesso à internet) moram no
código, em `livia/context.py`. A separação é intencional — personalidade é o que
você vai querer mexer toda semana, e não seria bom derrubar as travas de
honestidade sem querer numa dessas mexidas.

O prompt é montado em quatro camadas, nesta ordem:

```
1. Regras fixas        livia/context.py        (não editável pelo painel)
2. Personalidade       data/personalidade.md   ← aba "Jeito"
3. Memórias            data/memory/*.md        ← aba "Memórias"
4. Skills              data/skills/*.md        ← aba "Skills"
```

---

## As gavetas

| | Para que serve | Exemplo |
|---|---|---|
| **Memória** (`data/memory/`) | Fatos sobre você e seus projetos | "Prefere Postgres a MySQL em projetos novos" |
| **Skill** (`data/skills/`) | Um procedimento que você ensina uma vez | "Como fazer o deploy do projeto X: passo 1..." |
| **Lição** (`data/lessons/`) | O que ela deduziu das próprias tentativas | "Quando a Epson não conecta por WPS, tentar manual antes do reset" |

Memórias aparecem sozinhas conforme vocês conversam. Skills você escreve à mão,
no painel lateral. Lições **ela** escreve, e por isso ficam em Markdown legível:
o que ela concluiu sozinha tem que estar ao seu alcance para você discordar.

### Memória por projeto

Uma memória pode valer para você em geral ou só dentro de um projeto:

```
global                  → "Prefere Postgres nos projetos"
project:livia           → "Este projeto usa SQLite"
```

Não há contradição entre as duas: as duas são verdade, e ao falar do projeto a
específica tem prioridade. A Livia descobre de qual projeto vocês estão falando
pelos nomes que aparecem na conversa, pelas memórias já gravadas e pelas pastas
da área de trabalho — sem gastar uma chamada de IA para isso. Sem evidência
suficiente, usa a memória global; um palpite errado puxaria o contexto do
projeto errado para dentro da resposta.

### Como ela aprende com as correções

Quando você corrige a Livia, três coisas acontecem:

1. a correção vira memória, com peso alto;
2. a memória antiga que dizia o contrário é marcada como **superada** — sai das
   respostas e **continua no disco**, apontando para quem a substituiu;
3. a rodada anterior, se estava marcada como sucesso, é revista para falha.

O caso concreto:

```
antes:   "O CRM usa Firebase"
você:    "não usamos mais Firebase, migramos para Supabase"
depois:  "O CRM usa Supabase"                      (ativa)
         "O CRM usa Firebase"    → superseded_by   (no disco, fora do prompt)
```

Apagar a antiga perderia o registro de que aquilo já foi verdade — e daí a
seis meses ninguém entende por que havia código do Firebase no repositório.

### Como ela aprende com o que dá certo e com o que falha

Cada tarefa vira uma **experiência**: o que foi tentado, com quais ferramentas,
e como terminou. O que conta como sucesso é só evidência operacional — a
ferramenta rodou, o arquivo foi criado, você confirmou. **Responder não é
acertar**, e uma resposta bonita não marca nada.

Sem evidência de nenhum lado, o veredito fica indefinido e aquela experiência
não vota em nada. Um "não sei" honesto vale mais que o sucesso inventado que
vira heurística errada daqui a três meses.

Quando o mesmo tipo de situação se repete e os resultados concordam:

```
3+ experiências parecidas, 75%+ com o mesmo resultado
        ↓
sucesso → lição em data/lessons/
falha   → anti-pattern em data/lessons/
sequência de ações repetida → SKILL CANDIDATA, esperando você aprovar
```

Uma ocorrência é anedota. O mínimo é configurável em
`LIVIA_LEARNING_MIN_EXPERIENCES`.

**Skill candidata nunca vira skill sozinha.** Ela aparece no topo da aba Skills
com "aprovar" e "rejeitar". O motivo é simples: skill entra em todo prompt
futuro como procedimento a seguir, e deixá-la escrever isso sem ninguém ler
seria deixá-la mudar o próprio comportamento em silêncio.

### Faxina

`/manutencao-memoria` procura duplicatas, conflitos e memória esquecida.
Ele **só relata**. Para ela mexer de verdade:

```
/manutencao-memoria aplicar
```

Não roda em thread de fundo de propósito: uma tarefa periódica frágil que
reescreve memória sem ninguém olhando é o tipo de coisa que, quando erra,
ninguém descobre a tempo.

### Por que você lembrou disso?

```
/porque
```

Responde com as memórias, lições, skills, experiências e documentos que
entraram na última resposta — e o que pesou mais em cada uma. Sai da conta que
foi realmente feita, não de uma explicação gerada depois (que soaria melhor e
poderia ser mentira).

---

## Comandos no chat

| Comando | O que faz |
|---|---|
| `/lembrar <fato>` | Grava uma memória na hora (não gasta chamada de API) |
| `/esquecer <nome>` | Apaga a memória de vez |
| `/arquivar <nome>` | Tira do prompt sem apagar — dá para reativar |
| `/memorias` | Lista tudo que está guardado |
| `/experiencias` | O que ela já tentou, e como terminou |
| `/licoes` | O que ela concluiu sozinha |
| `/porque` | De onde veio o que ela usou na última resposta |
| `/manutencao-memoria` | Relata duplicatas e memória esquecida (`aplicar` para arrumar) |
| `/buscar <termo>` | Força uma busca na web |
| `/ajuda` | Mostra os comandos |

---

## Estrutura do projeto

```
run.py              sobe o servidor
livia/
  config.py         lê o .env; tudo que muda de máquina vive aqui
  brain.py          ÚNICA parte que sabe quais modelos de IA existem
  router.py         qual provedor atende cada tarefa (decisão local, sem IA)
  saude.py          quem está de pé, quem está de castigo
  docs.py           leitura/escrita dos arquivos .md
  store.py          as gavetas (memória, skills, lições)
  persona.py        a personalidade editável
  context.py        monta o prompt a cada pergunta  <- o coração do truque
  memoria.py        busca semântica, duplicatas, contradições, escopo
  experiencia.py    o que foi tentado, e o que virou lição
  embeddings.py     texto -> vetor (local ou nuvem)
  learner.py        decide o que virou memória depois de cada resposta
  biblioteca.py     documentos que ela consulta (RAG)
  conhecimento.py   indexar uma pasta de projeto inteira
  ferramentas.py    ler/escrever arquivo, calcular (confinado ao workspace)
  leitura.py        extrair texto de PDF, DOCX, XLSX, CSV, JSON, HTML
  objetos.py        gerar PDF, DOCX, XLSX, CSV
  web.py            buscar (DuckDuckGo ou SearXNG) e ler links
  db.py             conversas, experiências, índices e cache (SQLite)
  backup.py         exportar e restaurar tudo num zip
  knowledge.py      contrato do grafo: KnowledgeHit, dedup, orçamento
  knowledge_client.py  fala com o serviço (circuit breaker, LOCAL_ONLY)
  knowledge_router.py  escolhe vetor, grafo ou os dois
  knowledge_ingest.py  ingestão dupla, fila e estado por documento
  server.py         rotas HTTP e streaming
web/index.html      a interface inteira, num arquivo só
web/sw.js           service worker: casca offline e cache honesto
web/icones/         ícones do aplicativo instalável
services/
  knowledge/        o Knowledge Engine, com dependências próprias
    app.py          contrato HTTP do sidecar
    cognee_engine.py  ÚNICO arquivo que sabe o que é Cognee
    registro.py     a procedência, que não depende do motor
    multimodal.py   parser avançado (opcional)
data/
  memory/           suas memórias (.md)
  skills/           procedimentos que você ensinou (.md)
  lessons/          o que ela deduziu sozinha (.md)
  biblioteca/       documentos e projetos indexados
  livia.db          conversas, experiências, vetores e contadores
```

### O que é original e o que é derivado

Essa distinção governa o projeto inteiro. **Original** é o que você escreveu:
os `.md` e as conversas. **Derivado** é tudo que dá para recalcular: vetores,
índices, contadores de uso.

O derivado nunca é a única cópia de nada. Apagar `livia.db` custa o histórico
de conversas e as experiências, e obriga a reconstruir os índices — mas não
apaga uma linha do que você escreveu ou ensinou. Inverter isso transformaria um
arquivo binário na única cópia da memória de alguém.

---

## Instalar como aplicativo (PWA)

Abra a Livia no Chrome ou Edge e clique no ícone de instalar na barra de
endereço. Ela vira um aplicativo de janela própria, com ícone na barra de
tarefas — sem abas, sem barra de endereço.

### O que "offline" significa aqui — e o que não significa

Vale ser exato, porque é fácil prometer demais. **A inteligência da Livia é o
Python rodando na sua máquina.** Mesmo no modo totalmente local, quem pensa é
o servidor; o navegador só desenha. Então:

| | |
|---|---|
| Abrir o app instantâneo, sem esperar rede | ✅ |
| **Ler** conversas, memórias e lições já vistas com o servidor desligado | ✅ |
| Ser avisado com clareza do que está acontecendo | ✅ |
| **Conversar** com o servidor desligado | ❌ e nenhum cache resolve |

Com o `python run.py` parado, o app abre, mostra uma faixa explicando, troca o
indicador de "online" para "sem servidor", e deixa você navegar pelo que já
viu. Se você digitar uma pergunta, ele avisa antes de enviar — **sem apagar o
que você escreveu**.

Uma PWA mal feita é pior que nenhuma: abre, parece funcionar, e só quebra
quando a pessoa já digitou. Por isso o service worker nunca serve resposta de
chat do cache, e todo dado vindo do cache aparece marcado na interface.

### A pegadinha do endereço

Service worker só funciona em **contexto seguro**. Na prática:

| Endereço | Instala como app? |
|---|---|
| `http://127.0.0.1:8100` (mesma máquina) | ✅ localhost é confiável por definição |
| `http://localhost:8100` | ✅ |
| `http://192.168.0.10:8100` (pela rede) | ❌ o navegador recusa o worker |
| `https://...` (túnel do Cloudflare) | ✅ |

Se você acessa do celular pela rede local por IP, o app **continua
funcionando inteiro** — só não instala nem guarda nada offline. Para ter as
duas coisas no celular, use o túnel HTTPS (ver *Colocar na internet*).

### Trocando o nome

O manifesto é gerado pelo servidor, então `LIVIA_NAME=Ada` instala um
aplicativo chamado Ada, com o nome certo na janela e no ícone.

---

## Conhecimento avançado

Três coisas diferentes, que resolvem problemas diferentes:

```
Biblioteca      encontra TRECHOS parecidos com a pergunta
Knowledge Engine  conecta CONCEITOS entre documentos
Parser avançado   consegue LER documentos difíceis
```

A biblioteca sozinha responde bem "o que o capítulo 4 diz sobre X". Ela não
responde bem isto:

> Qual banco de dados aparece relacionado ao projeto em que a Alice trabalha?

Porque a resposta não está escrita em lugar nenhum. Está em dois documentos:

```
doc_a:  "Alice trabalha no Projeto Orion."
doc_b:  "O Projeto Orion utiliza PostgreSQL."
```

Nenhum dos dois se parece com a pergunta, e mesmo que a busca traga os dois,
nada diz que estão ligados. Um grafo de entidades e relações diz.

### Como fica o fluxo

```
                      pergunta
                          │
                    Query Router          ← heurística local, sem gastar IA
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
     simples          relacional          síntese
        │                 │                 │
   RAG vetorial         Grafo            os dois
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
                   deduplicar + orçamento
                          │
                    contexto final → modelo
```

Ingestão continua sendo dupla, com a biblioteca sempre na frente:

```
arquivo
   ├──→ biblioteca (trechos + vetores)   ← termina primeiro, nunca é desfeita
   └──→ Knowledge Engine (grafo)         ← entra numa fila; pode falhar
```

**Se o grafo falhar, o documento continua indexado e buscável.** O que se
perde é a capacidade de responder perguntas relacionais sobre ele.

### O modelo não é retreinado

Vale repetir, porque é a mesma ideia do resto do projeto: **nenhum documento
altera os pesos de nenhum modelo.** O grafo é um índice em disco, como os
vetores. O que muda é o que entra no prompt.

### Ligar

O motor roda como serviço separado, e isso não é preciosismo: ele traz **45
dependências obrigatórias** (openai, litellm, lancedb, gunicorn). Deixá-las
fora do `requirements.txt` é o que mantém a Livia instalável em 30 segundos.

```bash
pip install -r requirements-knowledge.txt
python -m services.knowledge.run
```

E no `.env`:

```bash
LIVIA_KNOWLEDGE=1
```

Os modelos do grafo são **independentes** dos da Livia — extrair entidades
depende de saída estruturada confiável, e o modelo bom de conversa não é
necessariamente bom nisso:

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

> ⚠️ **Configure os dois lados.** A documentação do Cognee avisa que, se você
> configurar só o LLM, o embedding *"defaults to OpenAI"* — e aí o conteúdo dos
> seus documentos vai para a nuvem sem aviso nenhum. Com `LIVIA_LOCAL_ONLY=1`
> o serviço **se recusa a subir** se detectar isso.

### Construir o grafo dos documentos que você já tem

Não precisa reenviar nada. O painel mostra:

> **23 documentos ainda sem grafo de conhecimento.** [construir agora]

Ele usa os `trechos.jsonl` que já estão em disco. Nada começa sozinho — uma
biblioteca grande levaria horas de CPU, e isso não pode acontecer num boot
sem ninguém pedir.

### Os dois índices são separados

Cada documento mostra os dois estados, e as ações têm nomes distintos:

| | O que refaz |
|---|---|
| **reindexar vetores** | os embeddings da biblioteca |
| **reconstruir conhecimento** | o grafo de entidades |

### Quando a heurística não pega

O roteador decide por regex, sem gastar chamada de modelo. Ele acerta a
maioria, não todas. Para forçar:

```
/grafo qual a relação entre esses dois assuntos?
```

### Procedência

Todo resultado do grafo carrega documento, título e página. **Resultado sem
procedência é descartado** — um grafo sabe afirmar "X causa Y" sem dizer onde
leu isso, e depois que entra no prompt é indistinguível de invenção.

O `/porque` mostra o caminho:

```
Conhecimento (por semelhança + grafo):
- doc a, p. 1
- doc b, p. 1

Relações que levaram até isso:
- Alice → trabalha em → Projeto Orion
- Projeto Orion → utiliza → PostgreSQL

Busca híbrida: 2 por texto, 3 pelo grafo, 1 repetido descartado.
```

### Fontes que discordam

Se um documento diz "usamos MySQL" e outro diz "migramos para PostgreSQL", a
Livia **não escolhe um vencedor em silêncio**. Ela mostra os dois e avisa que
divergem — escolher sozinha destruiria a informação histórica.

### Parser avançado (PDF difícil)

O `pypdf` continua sendo o caminho rápido: ele lê um PDF de texto em
milissegundos. O parser pesado só entra quando ele não consegue.

```
PDF → pypdf → saiu texto? → sim → fluxo de sempre
                          → não → parser avançado (se instalado)
                                → senão, a mensagem explicando
```

```bash
pip install "raganything>=1.3"
pip install "raganything[paddleocr]"   # OCR de página escaneada
```

E `LIVIA_PARSER_AVANCADO=1` no `.env`. Tabela, equação e figura mantêm o tipo
— uma equação que virasse `x2 + y2 = z2` em silêncio seria pior que uma
equação faltando, porque a primeira parece certa.

### Docker

```bash
docker compose --profile conhecimento up -d
```

Sem o perfil, o serviço nem é construído. Ele não publica porta nenhuma: não
tem senha, e quem o alcançasse leria o conteúdo dos documentos.

### O que não vem no backup

O grafo. Ele é inteiramente reconstruível a partir dos `trechos.jsonl`, que
**vêm** no backup. Depois de restaurar, o painel oferece construir de novo.

---

## Documentos e projetos

A aba **Livros** aceita PDF, TXT, Markdown, DOCX, XLSX, CSV, JSON e HTML. Ela
não decora o documento: quando você pergunta, procura os trechos que respondem
e lê **aqueles**, citando de onde veio.

### Importar uma pasta de projeto

A mesma aba lista as pastas da sua área de trabalho e oferece importar. O que
fica **de fora**, sempre:

- `node_modules`, `.git`, `dist`, `build`, `venv`, `coverage` e afins;
- `.env`, chaves `.pem`, `id_rsa`, `credentials.json`;
- binários e arquivos acima de 400 KB;
- **qualquer linha** que pareça carregar credencial, mesmo dentro de um arquivo
  legítimo. O resto do arquivo entra normalmente — recusar um `settings.py`
  inteiro por causa de três palavras perderia código útil.

Só dá para importar pasta **dentro** da área de trabalho. Uma importação que
aceitasse caminho absoluto seria um jeito elegante de ler o seu `~/.ssh`.

### Trocar de gerador de vetores

Vetor do Gemini e vetor do `nomic-embed-text` são incomparáveis. Comparar os
dois não dá erro — dá semelhança aleatória, e a busca passa a trazer o trecho
errado sem ninguém perceber. Por isso cada documento guarda quem gerou seus
vetores; ao trocar, ele aparece marcado com um botão **reconstruir**, e fica
fora da busca até você mandar. Nada é apagado, e como os trechos ficam em
disco, reconstruir não exige o arquivo original de volta.

---

## Conteúdo externo é dado, nunca instrução

Trecho de documento e resultado de busca entram no prompt cercados por
`<external_knowledge>`, com a regra explícita de que ordem escrita lá dentro é
texto da página, não comando. Sem isso, bastaria alguém publicar uma página
dizendo "ignore suas instruções" e esperar que ela caísse numa busca.

A mesma regra vale para o aprendizado: conteúdo de documento nunca vira lição
nem memória por conta própria. Lição sai de experiência operacional; memória
sai de conversa com você.

---

## Trocar o modelo de IA

Três cenários:

**Modelo local:** mude `LIVIA_OLLAMA_MODEL` no `.env` para o que você baixou.
Nenhum modelo é obrigatório e nenhum está no código.

**Outro modelo do Gemini:** mude `LIVIA_MODEL` no `.env`. Nada de código.

Os nomes mudam com frequência e os antigos vão sendo desativados. Se der erro
404, a mensagem no chat repassa o que o Google respondeu — ele costuma dizer
qual é o substituto. Para listar o que a sua chave enxerga:

```bash
python -c "import httpx; from livia import config; print('\n'.join(m['name'].replace('models/','') for m in httpx.get('https://generativelanguage.googleapis.com/v1beta/models', headers={'x-goog-api-key': config.GEMINI_API_KEY}).json()['models'] if 'generateContent' in m.get('supportedGenerationMethods',[])))"
```

Cuidado com os nomes terminados em `-latest` e com o `gemini-3.7-flash`: são
modelos de raciocínio pesado e levaram **60 a 95 segundos** por resposta nos
testes. Inviável para conversa. Os testados e aprovados aqui:

| Modelo | Resposta | Uso |
|---|---|---|
| `gemini-3.6-flash` | ~2,1s | conversa (padrão) |
| `gemini-3.5-flash` | ~2,6s | alternativa |
| `gemini-3.5-flash-lite` | ~1,2s | filtro de memória (padrão) |

**Outro fornecedor** (Claude, OpenAI, um modelo local): reescreva
`livia/brain.py` mantendo as duas funções públicas — `stream()` e
`structured()`. O resto do sistema não sabe nem quer saber o que tem atrás.
Suas memórias e skills continuam valendo, porque são só texto.

---

## Limites que valem saber de antemão

- **A camada gratuita do Gemini usa suas conversas para treinar o modelo.**
  Para uso pessoal costuma ser aceitável — mas não jogue senha, chave de API ou
  dado de cliente ali dentro. O filtro de memória foi instruído a nunca gravar
  esse tipo de coisa, mas instrução não é garantia. Se isso incomoda,
  `LIVIA_LOCAL_ONLY=1` resolve de vez: nada sai da máquina.
- **Os provedores de nuvem têm limite diário.** Se estourar, espera alguns
  minutos; a mensagem de erro avisa quando é esse o caso. O Ollama não tem
  cota — o limite dele é a sua máquina.
- **Modelo local é mais lento e menos capaz que o da nuvem.** Um `qwen3:8b`
  numa máquina sem GPU leva dezenas de segundos por resposta. A configuração
  padrão (local primeiro, nuvem como reserva) existe justamente para você
  escolher caso a caso sem trocar nada.
- **Nem todo modelo local sabe usar ferramentas.** Por isso `LIVIA_OLLAMA_TOOLS`
  nasce em 0: com ele desligado, tarefas com ferramenta vão para a nuvem e o
  resto continua local. Ligue depois de confirmar que o seu modelo aguenta.
- **A busca por significado precisa de um gerador de vetores.** Sem Ollama e
  sem chave do Gemini, ela volta a carregar a memória inteira até o orçamento —
  funciona, mas perde a seleção por relevância.
- **A varredura de credencial na importação de projeto pega o caso comum, não
  todos.** Ela reconhece formatos conhecidos de token e variáveis com valor
  longo. Um segredo escrito de forma criativa pode passar. Confira o que você
  importa.
- **PDF escaneado não é lido.** Sem camada de texto não há o que extrair, e
  OCR não está incluído. Ela diz isso em vez de devolver vazio.
- **A memória cresce sem parar, mas agora isso pesa menos.** Só o que tem a ver
  com a pergunta entra no prompt, então cem memórias não atrapalham como
  atrapalhavam. Ainda assim, `/manutencao-memoria` de vez em quando mantém a
  casa em ordem.
