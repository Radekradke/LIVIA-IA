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

titulo "3/5  Configuração"
if [[ -f .env ]]; then
  verde ".env já existe, mantendo"
else
  cp .env.example .env
  # Senha forte gerada aqui mesmo — ninguém precisa inventar uma.
  SENHA="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 24)"
  sed -i "s|^LIVIA_PASSWORD=.*|LIVIA_PASSWORD=${SENHA}|" .env
  sed -i "s|^LIVIA_HTTPS=.*|LIVIA_HTTPS=1|" .env
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

titulo "4/5  Firewall do sistema"
# A pegadinha clássica da Oracle: a Security List do console é só metade.
# A imagem do Ubuntu vem com iptables bloqueando quase tudo. Como o acesso
# aqui é por túnel (que sai de dentro para fora), não precisamos abrir porta
# nenhuma — mas deixamos registrado, porque é onde todo mundo trava.
verde "nada a abrir: o acesso é por túnel, que não precisa de porta de entrada"

titulo "5/5  Subindo"
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

  Backup: pelo painel da Livia, aba "Jeito" > Backup > Baixar.
  Faça isso de vez em quando mesmo aqui — disco de VM também falha.
FIM
