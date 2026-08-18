#!/usr/bin/env bash
# Instala a Livia numa VM Ubuntu recém-criada (Oracle Cloud, ou qualquer outra).
#
# Uso, já dentro da VM:
#     bash deploy/instalar.sh
#
# O script é conservador: mostra o que vai fazer, não apaga nada e pode ser
# rodado de novo sem estragar o que já existe.

set -euo pipefail

verde()  { printf '\033[0;32m%s\033[0m\n' "$1"; }
alerta() { printf '\033[0;33m%s\033[0m\n' "$1"; }
erro()   { printf '\033[0;31m%s\033[0m\n' "$1" >&2; }
titulo() { echo; printf '\033[1m== %s\033[0m\n' "$1"; }

[[ $EUID -eq 0 ]] && { erro "Não rode como root. Use o usuário normal (ubuntu)."; exit 1; }
cd "$(dirname "$0")/.."

titulo "1/5  Sistema"
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl git

titulo "2/5  Docker"
if command -v docker >/dev/null 2>&1; then
  verde "já instalado: $(docker --version)"
else
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  alerta "Docker instalado. Você precisa sair e entrar de novo no SSH para"
  alerta "usar docker sem sudo. Faça isso e rode este script outra vez."
  exit 0
fi

titulo "3/6  Memória"
# A E2.1.Micro do plano gratuito tem 1 GB de RAM. Instalar numpy, reportlab e
# pypdf nesse espaço faz o kernel matar o build por falta de memória — e a
# mensagem que aparece não diz isso, então a pessoa fica sem saber o que houve.
# 2 GB de swap resolvem e não custam nada.
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')

if (( RAM_MB < 2048 )) && (( SWAP_MB < 512 )); then
  alerta "Só ${RAM_MB} MB de RAM e nenhuma swap. Criando 2 GB de swap,"
  alerta "senão a instalação das bibliotecas é morta pelo sistema."
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  verde "swap de 2 GB ativa e permanente"
elif (( SWAP_MB >= 512 )); then
  verde "swap já existe (${SWAP_MB} MB)"
else
  verde "${RAM_MB} MB de RAM, folgado"
fi

titulo "4/6  Configuração"
if [[ -f .env ]]; then
  verde ".env já existe, mantendo"
else
  cp .env.example .env
  # Senha forte gerada aqui mesmo — ninguém precisa inventar uma.
  SENHA="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 24)"
  sed -i "s|^LIVIA_PASSWORD=.*|LIVIA_PASSWORD=${SENHA}|" .env
  verde ".env criado"
  echo
  alerta "  SENHA DE ACESSO: ${SENHA}"
  alerta "  Anote agora. Ela está no .env, mas anote."
fi

echo
if ! grep -q '^GEMINI_API_KEY=.\+' .env && ! grep -q '^GROQ_API_KEY=.\+' .env; then
  alerta "Falta a chave de API. Edite o .env antes de continuar:"
  alerta "    nano .env"
  alerta "Preencha GEMINI_API_KEY (e GROQ_API_KEY, se tiver)."
  alerta "Depois rode este script de novo."
  exit 0
fi
verde "chave de API encontrada"

titulo "5/6  Firewall do sistema"
# A pegadinha clássica da Oracle: a Security List do console é só metade.
# A imagem do Ubuntu vem com iptables bloqueando quase tudo. Como o acesso
# aqui é por túnel (que sai de dentro para fora), não precisamos abrir porta
# nenhuma — mas deixamos registrado, porque é onde todo mundo trava.
verde "nada a abrir: o acesso é por túnel, que não precisa de porta de entrada"

titulo "6/6  Subindo"
if grep -q '^CLOUDFLARE_TUNNEL_TOKEN=.\+' .env; then
  docker compose --profile tunel up -d --build
  verde "app + túnel no ar"
  echo
  echo "  Acesse pelo endereço que o Cloudflare mostra no painel do túnel."
else
  docker compose up -d --build
  verde "app no ar, escutando só em 127.0.0.1:8100"
  echo
  alerta "  Sem túnel configurado — ainda não dá para acessar de fora."
  alerta "  Crie um túnel em https://one.dash.cloudflare.com"
  alerta "  (Zero Trust > Networks > Tunnels), aponte para http://livia:8100,"
  alerta "  cole o token em CLOUDFLARE_TUNNEL_TOKEN no .env e rode:"
  alerta "      docker compose --profile tunel up -d"
fi

echo
titulo "Comandos do dia a dia"
cat <<'FIM'
  docker compose logs -f livia      ver o que está acontecendo
  docker compose restart livia      reiniciar
  docker compose down               parar (os dados ficam)
  docker compose up -d --build      atualizar depois de mudar o código

  Se algo parecer errado: painel > aba "Estado". Ela diz qual provedor
  está fora, se dá para escrever no disco e quantas conversas existem.

  Backup: painel > aba "Jeito" > Backup > Baixar.
  Faça isso de vez em quando mesmo aqui — disco de VM também falha.
FIM
