"""
0_validar_gtfs_entrada.py — Wrapper de compatibilidade (etapa de entrada)
=========================================================================
Valida a integridade do GTFS de entrada antes do pipeline (processar_gtfs).

Este arquivo apenas repassa os argumentos para 1_validar_gtfs.py --etapa entrada,
mantendo o nome de invocação usado pelo pipeline externo. Uso idêntico ao de
antes:

    python 0_validar_gtfs_entrada.py --config <arquivo.json>
    python 0_validar_gtfs_entrada.py            # GTFS auto-detectado na pasta
"""

import importlib
import sys

main = importlib.import_module("1_validar_gtfs").main

if __name__ == "__main__":
    if "--etapa" not in sys.argv:
        sys.argv[1:1] = ["--etapa", "entrada"]
    sys.exit(main())
