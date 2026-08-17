# Imagem para hospedar a Livia. Funciona em Render, Fly.io, Railway, Koyeb,
# ou em qualquer VPS com Docker.
#
# ATENÇÃO AO DISCO: por padrão os dados vão para /data. Se a sua hospedagem
# não montar um disco persistente ali, TUDO É APAGADO a cada reinício —
# memórias, skills, personalidade e conversas. Monte um volume em /data, ou
# baixe backups pelo painel com frequência. Ver README.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LIVIA_DATA_DIR=/data \
    LIVIA_HOST=0.0.0.0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY livia/ ./livia/
COPY web/ ./web/
COPY run.py .

RUN mkdir -p /data

EXPOSE 8100

# Não roda como root. Se a hospedagem montar o volume com outro dono, ajuste.
RUN useradd --create-home --uid 1000 livia && chown -R livia:livia /data /app
USER livia

# Sobe direto pelo uvicorn: o run.py abre navegador e faz checagens que só
# fazem sentido na sua máquina. A trava de senha continua valendo — ela está
# no middleware do servidor, não no run.py.
CMD ["sh", "-c", "uvicorn livia.server:app --host 0.0.0.0 --port ${PORT:-8100}"]
