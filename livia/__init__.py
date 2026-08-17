"""Livia — assistente pessoal com memória em arquivos.

O sistema tem três peças que valem entender antes de mexer:

  brain.py    fala com o modelo de IA. É a ÚNICA parte que sabe qual modelo é.
  store.py    guarda memórias e skills como arquivos .md em data/.
  context.py  remonta o prompt a cada pergunta, relendo esses arquivos.

O modelo nunca aprende nada. Quem aprende é o programa: ele escreve arquivos
e recoloca o conteúdo no prompt da próxima vez.
"""

__version__ = "0.1.0"
