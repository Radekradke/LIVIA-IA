"""Livia — assistente pessoal que aprende com o uso, em arquivos de texto.

As peças que valem entender antes de mexer:

  brain.py        fala com os modelos. ÚNICA parte que sabe quais existem.
  router.py       decide quem atende cada tarefa, por capacidade, sem IA.
  store.py        as gavetas: memória, skills e lições, como arquivos .md.
  memoria.py      acha por significado, resolve duplicata e contradição.
  experiencia.py  registra o que foi tentado e transforma padrão em lição.
  embeddings.py   texto -> vetor, local (Ollama) ou nuvem (Gemini).
  context.py      monta o prompt a cada pergunta, com o que tem a ver com ela.

O PRINCÍPIO
-----------
O modelo nunca aprende nada. Ele é reiniciado do zero a cada pergunta. Quem
aprende é este programa: ele escreve arquivos e recoloca o conteúdo certo no
prompt seguinte.

Isso tem uma consequência que orienta o projeto inteiro: o que o usuário
escreveu é ORIGINAL e vive em Markdown que ele pode abrir e corrigir; vetor,
índice e contador são DERIVADOS e vivem no SQLite. O derivado nunca é a única
cópia de nada. Apagar o banco custa histórico e obriga a reindexar — não apaga
uma linha do que ele ensinou.
"""

__version__ = "0.2.0"
