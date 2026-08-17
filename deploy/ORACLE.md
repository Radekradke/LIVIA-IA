# Subir a Livia na Oracle Cloud (Always Free)

Guia do começo ao fim. As partes que exigem seus dados pessoais são suas; o
resto é copiar e colar.

Tempo: cerca de 40 minutos, sendo 30 deles esperando o cadastro.

---

## Antes de começar

Você vai precisar de:

- um cartão de crédito **para verificação de identidade** — a Oracle faz uma
  cobrança simbólica (~US$1) e estorna. Recursos "Always Free" não geram
  cobrança, mas o cadastro exige o cartão;
- um número de celular;
- uma conta no Cloudflare (gratuita, sem cartão) para o endereço HTTPS.

> **Por que a Oracle e não uma hospedagem grátis comum:** ali você tem uma
> máquina de verdade, com disco de verdade. Nas hospedagens gratuitas o disco é
> apagado a cada atualização, e as memórias da Livia iriam junto.

---

## Parte 1 — Criar a conta e a máquina (você faz)

**1.** Cadastre-se em <https://signup.oraclecloud.com>. Escolha a região mais
próxima (`Brazil East (São Paulo)` ou `Brazil Southeast (Vitória)`).

> A região **não pode ser trocada depois**. Escolha com calma.

**2.** No console, crie uma instância de computação:

| Campo | O que escolher |
|---|---|
| Imagem | **Canonical Ubuntu** (24.04 ou mais novo) |
| Forma (shape) | `VM.Standard.A1.Flex` — ARM, 1 OCPU, 6 GB. Se der erro de capacidade, use `VM.Standard.E2.1.Micro` |
| Chave SSH | **Baixe a chave privada** e guarde. Sem ela você não entra |

> **Sobre "out of capacity" no ARM:** é comum e não é erro seu. A forma
> `E2.1.Micro` tem só 1 GB de RAM, mas roda esta aplicação sem problema —
> ela é leve. Se quiser o ARM, tente de novo em outro horário.

**3.** Anote o **IP público** que aparece na página da instância.

---

## Parte 2 — Entrar na máquina

No PowerShell do Windows, na pasta onde salvou a chave:

```powershell
icacls sua-chave.key /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i sua-chave.key ubuntu@SEU_IP
```

A primeira linha corrige as permissões da chave — sem ela o SSH recusa por
"permissões abertas demais". É o primeiro tropeço de quase todo mundo no
Windows.

---

## Parte 3 — Instalar (copiar e colar)

Já dentro da VM:

```bash
git clone SEU_REPOSITORIO livia && cd livia
bash deploy/instalar.sh
```

Se o código não estiver no Git, mande os arquivos pelo `scp` a partir do
Windows, antes de entrar por SSH:

```powershell
scp -i sua-chave.key -r C:\Users\anascimento\LIVIA ubuntu@SEU_IP:~/livia
```

O script roda em três etapas e **para de propósito** entre elas:

1. **Primeira execução** — instala o Docker e pede para você sair e entrar de
   novo no SSH. Isso é necessário: sua conta só entra no grupo do Docker na
   próxima sessão.
2. **Segunda execução** — cria o `.env`, **gera uma senha forte e mostra na
   tela** (anote), e pede a chave de API. Edite com `nano .env`.
3. **Terceira execução** — sobe tudo.

---

## Parte 4 — O endereço HTTPS

A aplicação sobe escutando **só em `127.0.0.1`**. De propósito: nenhuma porta
fica exposta para robôs varrerem.

Quem fala com a internet é um túnel do Cloudflare, que sai de dentro para fora.

**Por que assim:** a Oracle tem **duas** camadas de firewall — a Security List
no console da nuvem e o `iptables` dentro do Ubuntu. Abrir só a primeira é o
erro mais comum de quem sobe algo lá, e o sintoma é cruel: tudo parece certo e
o site simplesmente não abre. O túnel não passa por nenhuma das duas, e ainda
te dá HTTPS de graça, sem domínio próprio.

**Como configurar:**

1. Vá em <https://one.dash.cloudflare.com> → **Zero Trust** → **Networks** →
   **Tunnels** → criar túnel.
2. Em *Public Hostname*, aponte o serviço para `http://livia:8100`.
3. Copie o **token** do túnel.
4. Na VM:

```bash
nano .env        # cole o token em CLOUDFLARE_TUNNEL_TOKEN
docker compose --profile tunel up -d
```

Pronto: o endereço que o Cloudflare mostrar é a sua Livia, com HTTPS, de
qualquer lugar.

---

## Dia a dia

```bash
docker compose logs -f livia      # ver o que está acontecendo
docker compose restart livia      # reiniciar
docker compose up -d --build      # atualizar depois de mudar o código
docker compose down               # parar (os dados ficam no volume)
```

**Backup:** painel lateral → aba **Jeito** → **Backup** → **Baixar**.
Faça de vez em quando mesmo aqui. Disco de VM também falha, e conta gratuita
pode ser suspensa por inatividade.

---

## Quando algo der errado

| Sintoma | Causa quase certa |
|---|---|
| SSH recusa a chave | Permissões do arquivo — rode o `icacls` da Parte 2 |
| `docker: permission denied` | Você não saiu e entrou de novo no SSH depois de instalar o Docker |
| O site não abre | Você abriu porta em vez de usar o túnel. Confira `docker compose logs tunel` |
| Livia responde "chave recusada" | `GEMINI_API_KEY` faltando ou errada no `.env` da VM — é um arquivo diferente do da sua máquina |
| Esqueci a senha | `grep LIVIA_PASSWORD .env` |
| ARM dá "out of capacity" | Normal. Use `E2.1.Micro` ou tente em outro horário |

---

## Sobre a conta gratuita

A Oracle pode **recuperar recursos Always Free ociosos**. Uma VM que você usa
todo dia não corre esse risco, mas se ficar semanas sem tocar nela, pode
perdê-la. Mais um motivo para baixar o backup de vez em quando.

E confira os termos: conta gratuita costuma ter restrição de uso comercial.
Para uso pessoal, sem problema.
