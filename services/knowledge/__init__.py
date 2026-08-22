"""LIVIA Knowledge Engine — grafo de entidades e relações, como sidecar.

Roda separado por um motivo medido: o motor de referência (Cognee) traz 45
dependências obrigatórias, entre elas openai, litellm e lancedb. Dentro da
Livia isso multiplicaria a instalação e arrastaria uma nuvem inteira para um
projeto que usa numpy e SQLite de propósito.

Instalar:   pip install -r requirements-knowledge.txt
Rodar:      python -m services.knowledge.run
"""

__version__ = "0.1.0"
