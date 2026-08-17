# Livia

Assistente pessoal de projetos, com memória, rodando na sua máquina.
Feita para 1-2 usuários. Sem servidor, sem Docker, sem conta em lugar nenhum
além da chave gratuita do Gemini.

---

## Como colocar para rodar

**1. Pegue a chave gratuita** em https://aistudio.google.com/apikey
(precisa de uma conta Google; não pede cartão).

**2. Configure:**

```bash
copy .env.example .env
```

Abra o `.env` e cole a chave em `GEMINI_API_KEY`. Coloque também seu nome em
`LIVIA_USER` — a Livia usa isso para se dirigir a você.

**3. Instale as dependências** (só na primeira vez):

```bash
python -m pip install -r requirements.txt
```

**4. Rode:**

```bash
python run.py
```

O navegador abre sozinho em http://127.0.0.1:8100.

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
- Cada memória entra em **toda** conversa futura. Por isso o filtro é rígido:
  memória demais deixa a assistente pior, não melhor.

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

## Dois provedores, um fallback

Depender de um provedor gratuito só é frágil: quando a cota estoura, a
assistente para. Por isso a conversa tenta os provedores em ordem.

```
1. Gemini 3.6 Flash        ~4s   · abre e lê links sozinho
   ↓ cota estourada, servidor fora, timeout
2. Groq gpt-oss-120b       ~1,2s · não lê links, mas a busca continua funcionando
   ↓ falhou também
3. mensagem dizendo o que houve com cada um
```

A troca é silenciosa e você vê um aviso discreto quando o reserva responde.

**Erro de configuração não aciona fallback.** Chave errada ou modelo
inexistente sobem na hora — trocar de provedor só esconderia o problema real.

**A ordem é sua.** `LIVIA_PROVIDERS=groq,gemini` inverte: a Groq é cerca de 3×
mais rápida, mas só o Gemini abre links por conta própria (a busca no
DuckDuckGo funciona nos dois, porque entra como texto no prompt).

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

## As duas gavetas

| | Para que serve | Exemplo |
|---|---|---|
| **Memória** (`data/memory/`) | Fatos soltos sobre você e seus projetos | "Prefere Postgres a MySQL em projetos novos" |
| **Skill** (`data/skills/`) | Um procedimento que você ensina uma vez | "Como fazer o deploy do projeto X: passo 1..." |

Memórias aparecem sozinhas conforme vocês conversam. Skills você escreve à mão,
no painel lateral — são coisas que você quer que ela faça **sempre daquele jeito**.

### Como ela aprende com os erros

Quando você corrige a Livia, o filtro de memória reconhece a correção e grava.
Da próxima vez, a correção já está no prompt e ela não repete o erro.

Se quiser forçar, use o comando direto:

```
/lembrar quando eu falo "o projeto", é sempre o LIVIA, não o outro
```

---

## Comandos no chat

| Comando | O que faz |
|---|---|
| `/lembrar <fato>` | Grava uma memória na hora (não gasta chamada de API) |
| `/esquecer <nome>` | Apaga a memória com esse nome |
| `/memorias` | Lista tudo que está guardado |
| `/ajuda` | Mostra os comandos |

---

## Estrutura do projeto

```
run.py              sobe o servidor
livia/
  config.py         lê o .env; tudo que muda de máquina vive aqui
  brain.py          ÚNICA parte que sabe qual modelo de IA é usado
  docs.py           leitura/escrita dos arquivos .md
  store.py          as duas gavetas (memória e skills)
  persona.py        a personalidade editável
  context.py        remonta o prompt a cada pergunta  <- o coração do truque
  learner.py        decide o que virou memória depois de cada resposta
  db.py             histórico das conversas (SQLite)
  server.py         rotas HTTP e streaming
web/index.html      a interface inteira, num arquivo só
data/               seus dados (memórias, skills, conversas)
```

---

## Trocar o modelo de IA

Dois cenários:

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

- **Não tem acesso à internet nem aos seus arquivos.** É uma conversa, não um
  agente que executa coisas. Dá para adicionar depois, mas hoje não tem.
- **A camada gratuita do Gemini usa suas conversas para treinar o modelo.**
  Para uso pessoal costuma ser aceitável — mas não jogue senha, chave de API ou
  dado de cliente ali dentro. O filtro de memória foi instruído a nunca gravar
  esse tipo de coisa, mas a instrução não é uma garantia.
- **Tem limite diário de requisições.** Se estourar, espera alguns minutos.
  A mensagem de erro avisa quando é esse o caso.
- **A memória cresce sem parar.** Quando passar de algumas dezenas de itens,
  vale abrir o painel e podar o que não faz mais sentido. Acima de ~100 itens
  o sistema para de carregar tudo e passa a usar só um índice — funciona, mas
  perde precisão. Esse é o ponto em que vale trocar por busca vetorial.
