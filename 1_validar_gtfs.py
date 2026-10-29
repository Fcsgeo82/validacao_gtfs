"""
validar_gtfs.py — Validação GTFS antes e depois do pipeline
============================================================
Uso:
    python validar_gtfs.py --etapa entrada   # pré-pipeline (alias: 0_validar_gtfs_entrada.py)
    python validar_gtfs.py --etapa saida     # padrão: pós-pipeline + cruzamento com a OS
    python validar_gtfs.py --etapa todas     # roda as duas etapas

Argumentos:
    --etapa       entrada | saida | todas (padrão: saida)
    --config      JSON inline ou caminho para arquivo .json
    --gtfs        caminho do .zip do GTFS
    --os          caminho do CSV da OS (sep=';', decimal=',')
    --output-dir  pasta dos relatórios da etapa de saída
    --estudo      rótulo do estudo na etapa de saída
    --apenas-pdf  só (re)gera o PDF consolidado a partir dos resultados salvos
    --sem-pdf     não gera o PDF consolidado ao final

Chaves do --config:
    Entrada: BASE_DADOS, PASTA_RESULTADOS, ano_gtfs, mes_gtfs, estudo_gtfs,
             gtfs_processar (sppo|brt|rio), endereco_gtfs, pasta_gtfs
    Saída  : gtfs_path, os_path, output_dir, estudo

Sem argumentos, GTFS e OS são auto-detectados na pasta do script.
Saídas (na pasta do projeto): CSV detalhado da entrada, CSV resumo da saída
(formato Google Sheets), JSONs de resultado por etapa e o PDF consolidado
`relatorio_consolidado_gtfs.pdf` para leitura da chefia.
Exit code: 0 = OK; 1 = FALHA (entrada) ou ERRO (saída).
"""

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent

# ============================================================================
# CONSTANTES DE FALLBACK
# ============================================================================

# Etapa de entrada (pré-pipeline)
BASE_DADOS = "C:/R_SMTR/dados"
PASTA_RESULTADOS = str(SCRIPT_DIR)  # relatórios detalhados ficam na pasta do projeto
ANO_GTFS = "2026"
MES_GTFS = "10"
ESTUDO_GTFS = "02"
GTFS_PROCESSAR = "sppo"

# Etapa de saída (pós-pipeline)
GTFS_PATH = r"C:\github_repositories\validacao_gtfs\GTFS.zip"
OS_PATH = r"C:\github_repositories\validacao_gtfs\OS.csv"
OUTPUT_DIR = r"C:\github_repositories\validacao_gtfs"
ESTUDO = "validacao_gtfs"

# Relatórios consolidados (pasta do projeto)
RESULTADOS_JSON_ENTRADA = "resultados_entrada.json"
RESULTADOS_JSON_SAIDA = "resultados_saida.json"
ARQUIVO_PDF_CONSOLIDADO = "relatorio_consolidado_gtfs.pdf"

# ============================================================================
# ESTRUTURA DE RESULTADO (etapa de saída)
# ============================================================================

@dataclass
class Resultado:
    categoria: str
    check: str
    status: str          # OK | AVISO | ERRO
    n_afetados: int = 0
    detalhes: str = ""

# ============================================================================
# COLUNAS ESPERADAS POR ARQUIVO GTFS
# ============================================================================

COLUNAS_ESPERADAS = {
    "agency.txt": [
        "agency_id", "agency_name", "agency_url",
        "agency_timezone", "agency_lang",
    ],
    "feed_info.txt": [
        "feed_publisher_name", "feed_id", "feed_publisher_url",
        "feed_lang", "feed_start_date", "feed_end_date", "feed_version",
    ],
    "calendar.txt": [
        "service_id", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday", "sunday",
        "start_date", "end_date",
    ],
    "calendar_dates.txt": ["service_id", "date", "exception_type"],
    "routes.txt": [
        "route_id", "agency_id", "route_short_name",
        "route_long_name", "route_type",
    ],
    "trips.txt": [
        "trip_id", "route_id", "service_id",
        "trip_headsign", "trip_short_name", "direction_id", "shape_id",
    ],
    "stop_times.txt": [
        "trip_id", "stop_sequence", "stop_id",
        "arrival_time", "departure_time",
    ],
    "stops.txt": [
        "stop_id", "stop_name", "stop_lat", "stop_lon",
    ],
    "shapes.txt": [
        "shape_id", "shape_pt_sequence",
        "shape_pt_lat", "shape_pt_lon",
    ],
    "frequencies.txt": [
        "trip_id", "start_time", "end_time", "headway_secs",
    ],
    "fare_attributes.txt": [
        "fare_id", "price", "currency_type", "payment_method", "transfers",
    ],
    "fare_rules.txt": ["fare_id"],
}

# Colunas base obrigatórias da OS (generate_os.py)
COLUNAS_OS_OBRIGATORIAS = [
    "Serviço", "Vista", "Consórcio",
    "Extensão de Ida", "Extensão de Volta",
    "Partidas Ida - Dia Útil", "Partidas Volta - Dia Útil",
    "Viagens - Dia Útil", "Quilometragem - Dia Útil",
]

FAIXAS_OS = [
    "00h à 01h", "01h à 02h", "02h à 03h", "03h à 04h",
    "04h à 05h", "05h à 06h", "06h à 09h", "09h à 12h",
    "12h à 15h", "15h à 18h", "18h à 21h", "21h à 22h",
    "22h à 23h", "23h à 24h",
]

# ============================================================================
# INFRAESTRUTURA COMPARTILHADA
# ============================================================================

def log_msg(msg):
    print(f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] {msg}")

def _texto(v):
    """Converte valor de série/panda em string ('' para NaN/vazio)."""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v)

def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    return str(o)

def salvar_resultados_json(etapa, payload):
    """Persiste o resultado da etapa em JSON na pasta do projeto (alimenta o PDF)."""
    nome = RESULTADOS_JSON_ENTRADA if etapa == "entrada" else RESULTADOS_JSON_SAIDA
    caminho = SCRIPT_DIR / nome
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_default)
    return caminho

def carregar_resultados_json(etapa):
    """Lê o JSON de resultado de uma etapa (None se ausente/inválido)."""
    nome = RESULTADOS_JSON_ENTRADA if etapa == "entrada" else RESULTADOS_JSON_SAIDA
    try:
        with open(SCRIPT_DIR / nome, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

def detectar_gtfs_na_pasta(pasta):
    """
    Procura arquivos .zip que sejam GTFS válido (contem routes/trips/stops.txt)
    na pasta informada. Retorna os candidatos do mais recente para o mais antigo.
    """
    candidatos = []
    if not pasta.exists():
        return candidatos

    for zip_path in pasta.glob("*.zip"):
        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                nomes = z.namelist()
                if any(n in nomes for n in ('routes.txt', 'trips.txt', 'stops.txt')):
                    candidatos.append(zip_path)
        except zipfile.BadZipFile:
            log_msg(f"Aviso: ignorando ZIP inválido: {zip_path.name}")

    candidatos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidatos

def extrair_metadata_gtfs(nome_arquivo):
    """
    Extrai operador/ano/mes/estudo do nome do GTFS (ex.: rio_2026-10-01Q.zip).
    Retorna dict vazio se o nome não seguir o padrão.
    """
    m = re.match(r'^(?P<operador>[A-Za-z0-9]+)_(?P<ano>\d{4})-(?P<mes>\d{2})-(?P<estudo>\d{2})Q',
                 Path(nome_arquivo).name)
    if not m:
        return {}
    return {
        "gtfs_processar": m.group("operador").lower(),
        "ano_gtfs": m.group("ano"),
        "mes_gtfs": m.group("mes"),
        "estudo_gtfs": m.group("estudo"),
    }

def parse_config() -> dict:
    parser = argparse.ArgumentParser(description="Validação GTFS — etapas de entrada e saída do pipeline")
    parser.add_argument("--config", type=str, help="JSON inline ou caminho para arquivo .json")
    parser.add_argument("--etapa", type=str, choices=["entrada", "saida", "todas"],
                        default=None, help="Etapa a executar (padrão: saida)")
    parser.add_argument("--gtfs", type=str, help="Caminho do arquivo GTFS (.zip)")
    parser.add_argument("--os", dest="os_path", type=str, help="Caminho do arquivo CSV da OS")
    parser.add_argument("--output-dir", type=str, help="Diretório de saída do relatório (etapa de saída)")
    parser.add_argument("--estudo", type=str, help="Nome do estudo (etapa de saída)")
    parser.add_argument("--apenas-pdf", action="store_true",
                        help="Apenas (re)gerar o PDF consolidado a partir dos resultados salvos")
    parser.add_argument("--sem-pdf", action="store_true",
                        help="Não gerar o PDF consolidado ao final da execução")
    args, _ = parser.parse_known_args()

    cfg = {
        # entrada
        "BASE_DADOS": BASE_DADOS,
        "PASTA_RESULTADOS": PASTA_RESULTADOS,
        "ano_gtfs": ANO_GTFS,
        "mes_gtfs": MES_GTFS,
        "estudo_gtfs": ESTUDO_GTFS,
        "gtfs_processar": GTFS_PROCESSAR,
        "endereco_gtfs": "",
        "pasta_gtfs": str(SCRIPT_DIR),
        # saída
        "gtfs_path": "",
        "os_path": "",
        "output_dir": OUTPUT_DIR,
        "estudo": ESTUDO,
        # controle
        "etapa": "saida",
        "apenas_pdf": args.apenas_pdf,
        "sem_pdf": args.sem_pdf,
        "_explicitos": set(),
    }

    if args.config:
        config_str = args.config.strip()
        if os.path.isfile(config_str):
            with open(config_str, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
        else:
            try:
                user_cfg = json.loads(config_str)
            except json.JSONDecodeError:
                user_cfg = json.loads(config_str.replace("'", '"'))
        cfg.update(user_cfg)
        cfg["_explicitos"] |= set(user_cfg)

    if args.etapa:
        cfg["etapa"] = args.etapa
        cfg["_explicitos"].add("etapa")
    if args.gtfs:
        cfg["gtfs_path"] = args.gtfs
        cfg["endereco_gtfs"] = args.gtfs
        cfg["_explicitos"] |= {"gtfs_path", "endereco_gtfs"}
    if args.os_path:
        cfg["os_path"] = args.os_path
        cfg["_explicitos"].add("os_path")
    if args.output_dir:
        cfg["output_dir"] = args.output_dir
        cfg["_explicitos"].add("output_dir")
    if args.estudo:
        cfg["estudo"] = args.estudo
        cfg["_explicitos"].add("estudo")

    return cfg

def resolver_gtfs(cfg):
    """
    Resolve o GTFS de entrada: caminho explícito -> auto-detecção na pasta -> fallback.
    Retorna (Path, auto_detectado: bool).
    """
    explicito = cfg.get("endereco_gtfs") or cfg.get("gtfs_path") or ""
    if explicito:
        log_msg(f"GTFS informado via config: {explicito}")
        return Path(explicito), False

    pastas = [Path(cfg.get("pasta_gtfs") or SCRIPT_DIR)]
    if cfg.get("output_dir"):
        pastas.append(Path(cfg["output_dir"]))

    vistos = set()
    for pasta in pastas:
        chave = pasta.resolve() if pasta.exists() else pasta
        if chave in vistos:
            continue
        vistos.add(chave)
        candidatos = detectar_gtfs_na_pasta(pasta)
        if candidatos:
            escolhido = candidatos[0]
            if len(candidatos) > 1:
                log_msg(f"Aviso: {len(candidatos)} GTFS encontrados em {pasta}; "
                        f"usando o mais recente ({escolhido.name}):")
                for c in candidatos:
                    log_msg(f"  - {c.name}")
            else:
                log_msg(f"GTFS detectado automaticamente: {escolhido.name}")
            return escolhido, True

    if cfg["etapa"] == "entrada":
        fallback = Path(cfg["BASE_DADOS"]) / (
            f"gtfs/{cfg['ano_gtfs']}/{cfg['gtfs_processar']}_"
            f"{cfg['ano_gtfs']}-{cfg['mes_gtfs']}-{cfg['estudo_gtfs']}Q.zip")
    else:
        fallback = Path(GTFS_PATH)
    log_msg(f"Nenhum GTFS auto-detectado; usando caminho padrão: {fallback}")
    return fallback, False

def resolver_os(cfg) -> str:
    """Resolve o CSV da OS: caminho explícito -> auto-descoberta na pasta de saída."""
    candidato = cfg.get("os_path") or ""
    if candidato and os.path.exists(candidato):
        return candidato

    pasta = Path(cfg.get("output_dir") or SCRIPT_DIR)
    if pasta.is_dir():
        cands = (
            glob.glob(os.path.join(str(pasta), "tabela_programacao_OS.csv")) +
            glob.glob(os.path.join(str(pasta), "tabela_*.csv")) +
            [f for f in glob.glob(os.path.join(str(pasta), "*.csv"))
             if not os.path.basename(f).startswith(("relatorio_", "validacao_gtfs_"))]
        )
        if cands:
            return cands[0]
    return candidato

def load_gtfs(gtfs_path) -> dict:
    """Carrega todos os arquivos .txt do GTFS em um dict de DataFrames (chaves com .txt)."""
    log_msg(f"Lendo GTFS: {gtfs_path}")
    gtfs = {}
    with zipfile.ZipFile(gtfs_path, "r") as z:
        for name in z.namelist():
            if name.endswith(".txt"):
                try:
                    df = pd.read_csv(z.open(name), dtype=str)
                    df.columns = df.columns.str.strip()
                    gtfs[name] = df
                except pd.errors.EmptyDataError:
                    gtfs[name] = pd.DataFrame()
                except Exception as e:
                    log_msg(f"Aviso: não foi possível ler {name}: {e}")
    return gtfs

def load_os(os_path: str) -> pd.DataFrame:
    """Carrega o CSV da OS (sep=';', decimal=',')."""
    try:
        df = pd.read_csv(os_path, sep=";", dtype=str, encoding="utf-8-sig")
        df.columns = df.columns.str.strip()
        return df
    except UnicodeDecodeError:
        df = pd.read_csv(os_path, sep=";", dtype=str, encoding="latin-1")
        df.columns = df.columns.str.strip()
        return df

# ============================================================================
# ETAPA ENTRADA — FUNÇÕES AUXILIARES
# ============================================================================

def remover_acentos(texto):
    """Remove acentos de uma string."""
    if pd.isna(texto):
        return ""
    return unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('ASCII')

def validar_padrao_excepcionalidade(trip_headsign):
    """
    Valida se o texto entre colchetes em trip_headsign segue o padrão:
    - Sem acentos
    - Espaços substituídos por _
    - Sem letras maiúsculas
    - Apenas caracteres a-z, 0-9, _
    """
    if pd.isna(trip_headsign):
        return True, None

    texto = str(trip_headsign)

    matches = re.findall(r'\[([^\]]+)\]', texto)
    if not matches:
        return True, None  # Não é uma trip de excepcionalidade

    erros = []
    for match in matches:
        if remover_acentos(match) != match:
            erros.append(f"Acentos: '{match}'")
        if ' ' in match:
            erros.append(f"Espacos (devem ser _): '{match}'")
        if match != match.lower():
            erros.append(f"Maiusculas: '{match}'")
        if not re.match(r'^[a-z0-9_]+$', match):
            erros.append(f"Caracteres invalidos (a-z, 0-9, _): '{match}'")

    return len(erros) == 0, erros

def gerar_sugestao_correcao(trip_headsign):
    """Gera sugestão de correção para o trip_headsign fora do padrão."""
    matches = re.findall(r'\[([^\]]+)\]', str(trip_headsign))
    if not matches:
        return trip_headsign

    sugestoes = []
    for match in matches:
        corrigido = remover_acentos(match)
        corrigido = corrigido.replace(' ', '_')
        corrigido = corrigido.lower()
        corrigido = re.sub(r'[^a-z0-9_]', '', corrigido)
        sugestoes.append(f"[{corrigido}]")

    resultado = str(trip_headsign)
    for orig, sug in zip(matches, sugestoes):
        resultado = resultado.replace(f"[{orig}]", sug)
    return resultado

# ============================================================================
# ETAPA ENTRADA — VALIDAÇÕES
# ============================================================================

def validar_routes_com_trips(routes_df, trips_df):
    """
    Validação 1: Garantir que existam trips para cada route (via route_id).
    Retorna: (sucesso, lista_erros, dataframe_detalhado)
    """
    log_msg("=" * 60)
    log_msg("VALIDACAO 1: Routes com trips associadas")
    log_msg("=" * 60)

    if routes_df.empty:
        log_msg("AVISO: routes.txt esta vazio!")
        return False, [], pd.DataFrame()

    if trips_df.empty:
        log_msg("AVISO: trips.txt esta vazio!")
        return False, [], pd.DataFrame()

    routes_com_trips = set(trips_df['route_id'].dropna().unique())
    routes_sem_trips = routes_df[~routes_df['route_id'].isin(routes_com_trips)].copy()

    erros = []
    detalhes = []

    if len(routes_sem_trips) > 0:
        for _, row in routes_sem_trips.iterrows():
            route_id = row['route_id']
            route_short_name = row.get('route_short_name', 'N/A')
            route_long_name = row.get('route_long_name', 'N/A')
            route_type = row.get('route_type', 'N/A')

            erro = f"Route sem trips: route_id={route_id}, short_name={route_short_name}, long_name={route_long_name}"
            erros.append(erro)

            detalhes.append({
                'validacao': 'Routes sem trips',
                'status': 'FALHA',
                'route_id': route_id,
                'route_short_name': route_short_name,
                'route_long_name': route_long_name,
                'route_type': route_type,
                'trip_id': '',
                'service_id': '',
                'trip_headsign': '',
                'problema': 'Route sem nenhuma trip associada',
                'sugestao': 'Verificar se a route deve existir ou adicionar trips no trips.txt'
            })

        log_msg(f"FALHA: {len(erros)} route(s) sem trips associadas")
        for erro in erros[:10]:
            log_msg(f"  - {erro}")
        if len(erros) > 10:
            log_msg(f"  ... e mais {len(erros) - 10} routes (veja CSV completo)")
        return False, erros, pd.DataFrame(detalhes)
    else:
        log_msg(f"OK: Todos os {len(routes_df)} routes possuem pelo menos uma trip")
        for _, row in routes_df.iterrows():
            n_trips = len(trips_df[trips_df['route_id'] == row['route_id']])
            detalhes.append({
                'validacao': 'Routes sem trips',
                'status': 'OK',
                'route_id': row['route_id'],
                'route_short_name': row.get('route_short_name', 'N/A'),
                'route_long_name': row.get('route_long_name', 'N/A'),
                'route_type': row.get('route_type', 'N/A'),
                'trip_id': '',
                'service_id': '',
                'trip_headsign': '',
                'problema': '',
                'sugestao': f'{n_trips} trip(s) associada(s)'
            })
        return True, [], pd.DataFrame(detalhes)

def validar_trips_excepcionalidade(trips_df):
    """
    Validação 2: Garantir que trips de excepcionalidade (com [] no trip_headsign)
    sigam o padrão da equipe: sem acentos, espaços = _, sem maiúsculas.
    Retorna: (sucesso, lista_erros, dataframe_detalhado)
    """
    log_msg("=" * 60)
    log_msg("VALIDACAO 2: Padrao de trip_headsign em trips de excepcionalidade")
    log_msg("=" * 60)

    if trips_df.empty:
        log_msg("AVISO: trips.txt esta vazio!")
        return False, [], pd.DataFrame()

    if 'trip_headsign' not in trips_df.columns:
        log_msg("AVISO: Coluna trip_headsign nao encontrada em trips.txt")
        return False, [], pd.DataFrame()

    mask_excep = trips_df['trip_headsign'].notna() & trips_df['trip_headsign'].str.contains(r'\[', regex=True)
    trips_excep = trips_df[mask_excep].copy()

    log_msg(f"Total de trips com '[' no trip_headsign: {len(trips_excep)}")

    if trips_excep.empty:
        log_msg("OK: Nenhuma trip de excepcionalidade encontrada")
        return True, [], pd.DataFrame()

    erros = []
    detalhes = []

    for _, row in trips_excep.iterrows():
        trip_id = row['trip_id']
        trip_headsign = row['trip_headsign']
        route_id = row.get('route_id', 'N/A')
        service_id = row.get('service_id', 'N/A')

        valido, erros_trip = validar_padrao_excepcionalidade(trip_headsign)
        sugestao = gerar_sugestao_correcao(trip_headsign)

        if not valido:
            for erro in erros_trip:
                erros.append(f"trip_id={trip_id}, route_id={route_id}, headsign='{trip_headsign}' -> {erro}")

            detalhes.append({
                'validacao': 'Padrao excepcionalidade',
                'status': 'FALHA',
                'route_id': route_id,
                'route_short_name': '',
                'route_long_name': '',
                'route_type': '',
                'trip_id': trip_id,
                'service_id': service_id,
                'trip_headsign': trip_headsign,
                'problema': ' | '.join(erros_trip),
                'sugestao': f"Corrigir para: {sugestao}"
            })
        else:
            detalhes.append({
                'validacao': 'Padrao excepcionalidade',
                'status': 'OK',
                'route_id': route_id,
                'route_short_name': '',
                'route_long_name': '',
                'route_type': '',
                'trip_id': trip_id,
                'service_id': service_id,
                'trip_headsign': trip_headsign,
                'problema': '',
                'sugestao': 'Padrao correto'
            })

    if erros:
        log_msg(f"FALHA: {len(erros)} problema(s) em {trips_excep['trip_id'].nunique()} trip(s) de excepcionalidade")
        for erro in erros[:10]:
            log_msg(f"  - {erro}")
        if len(erros) > 10:
            log_msg(f"  ... e mais {len(erros) - 10} problemas (veja CSV completo)")
        return False, erros, pd.DataFrame(detalhes)
    else:
        log_msg(f"OK: Todas as {len(trips_excep)} trips de excepcionalidade seguem o padrao")
        return True, [], pd.DataFrame(detalhes)

def validar_trips_sem_route_id(trips_df):
    """Validação extra: Verificar trips sem route_id."""
    log_msg("-" * 60)
    log_msg("VALIDACAO EXTRA: Trips sem route_id")
    log_msg("-" * 60)

    if trips_df.empty:
        return True, []

    trips_sem_route = trips_df[trips_df['route_id'].isna() | (trips_df['route_id'] == "")]

    if len(trips_sem_route) > 0:
        log_msg(f"AVISO: {len(trips_sem_route)} trip(s) sem route_id")
        for _, row in trips_sem_route.head(10).iterrows():
            log_msg(f"  - trip_id={row['trip_id']}, service_id={row.get('service_id', 'N/A')}")
        return True, []
    else:
        log_msg("OK: Todas as trips possuem route_id preenchido")
        return True, []

def validar_service_ids(trips_df):
    """Validação extra: Verificar service_ids usados."""
    log_msg("-" * 60)
    log_msg("VALIDACAO EXTRA: Service IDs utilizados")
    log_msg("-" * 60)

    if trips_df.empty:
        return True, []

    service_ids = trips_df['service_id'].dropna().unique()
    log_msg(f"Service IDs encontrados: {sorted(service_ids)}")

    for sid in sorted(service_ids):
        count = len(trips_df[trips_df['service_id'] == sid])
        log_msg(f"  - {sid}: {count} trip(s)")
    return True, []

# ============================================================================
# ETAPA ENTRADA — EXPORTAÇÃO E RESUMO
# ============================================================================

def exportar_relatorio_csv(detalhes_todas_validacoes, pasta_saida, nome_arquivo_base):
    """Exporta o relatório detalhado da entrada para CSV."""
    pasta_saida.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    arquivo_csv = pasta_saida / f"{nome_arquivo_base}_{timestamp}.csv"

    if not detalhes_todas_validacoes.empty:
        detalhes_todas_validacoes.to_csv(arquivo_csv, index=False, encoding='utf-8-sig')
        log_msg(f"Relatorio CSV salvo em: {arquivo_csv}")
    else:
        df_vazio = pd.DataFrame(columns=[
            'validacao', 'status', 'route_id', 'route_short_name', 'route_long_name',
            'route_type', 'trip_id', 'service_id', 'trip_headsign', 'problema', 'sugestao'
        ])
        df_vazio.to_csv(arquivo_csv, index=False, encoding='utf-8-sig')
        log_msg(f"Relatorio CSV vazio salvo em: {arquivo_csv}")

    return arquivo_csv

def imprimir_resumo_consolidado(detalhes_df):
    """Imprime um resumo consolidado e legível da etapa de entrada no console."""
    print("\n" + "=" * 80)
    print("RESUMO CONSOLIDADO DE VALIDACOES")
    print("=" * 80)

    if detalhes_df.empty:
        print("Nenhum dado para exibir.")
        return

    for validacao in detalhes_df['validacao'].unique():
        df_val = detalhes_df[detalhes_df['validacao'] == validacao]
        total = len(df_val)
        ok = len(df_val[df_val['status'] == 'OK'])
        falha = len(df_val[df_val['status'] == 'FALHA'])
        aviso = len(df_val[df_val['status'] == 'AVISO'])

        status_icon = "OK" if falha == 0 else "FALHA"
        print(f"\n  {status_icon} {validacao}")
        print(f"     Total: {total} | OK: {ok} | FALHA: {falha} | AVISO: {aviso}")

        if falha > 0:
            falhas = df_val[df_val['status'] == 'FALHA']
            for _, row in falhas.head(5).iterrows():
                ident = row['trip_id'] if row['trip_id'] else row['route_id']
                print(f"     - {ident}: {row['problema']}")
                if row['sugestao']:
                    print(f"       -> Sugestao: {row['sugestao']}")
            if len(falhas) > 5:
                print(f"     ... e mais {len(falhas) - 5} problema(s) (veja CSV)")

    print("\n" + "=" * 80)

# ============================================================================
# ETAPA SAÍDA — CATEGORIA 1 — PADRÃO DE ARQUIVOS GTFS
# ============================================================================

def check_arquivos_gtfs(gtfs: dict) -> List[Resultado]:
    resultados = []
    cat = "1. Padrão GTFS"

    arquivos_esperados = set(COLUNAS_ESPERADAS.keys())
    arquivos_presentes = set(gtfs.keys())
    ausentes = arquivos_esperados - arquivos_presentes
    if ausentes:
        resultados.append(Resultado(cat, "Arquivos obrigatórios presentes",
            "ERRO", len(ausentes),
            f"Ausentes: {sorted(ausentes)}"))
    else:
        resultados.append(Resultado(cat, "Arquivos obrigatórios presentes", "OK"))

    for arquivo, cols_esperadas in COLUNAS_ESPERADAS.items():
        if arquivo not in gtfs:
            continue
        cols_presentes = set(gtfs[arquivo].columns.tolist())
        faltando = [c for c in cols_esperadas if c not in cols_presentes]
        if faltando:
            resultados.append(Resultado(cat, f"Colunas em {arquivo}",
                "ERRO", len(faltando),
                f"Colunas ausentes: {faltando}"))
        else:
            resultados.append(Resultado(cat, f"Colunas em {arquivo}", "OK"))

    for arquivo, df in gtfs.items():
        if len(df) == 0:
            resultados.append(Resultado(cat, f"Arquivo vazio: {arquivo}",
                "AVISO", 0, "Arquivo presente mas sem linhas de dados"))

    if "stops.txt" in gtfs:
        stops = gtfs["stops.txt"]
        def is_invalid_coord(series):
            numeric = pd.to_numeric(series, errors="coerce")
            return numeric.isna() | (numeric == 0)
        lat_inv = is_invalid_coord(stops.get("stop_lat", pd.Series()))
        lon_inv = is_invalid_coord(stops.get("stop_lon", pd.Series()))
        invalidos = stops[lat_inv | lon_inv]
        if len(invalidos) > 0:
            resultados.append(Resultado(cat, "Stops com coordenadas inválidas/nulas",
                "ERRO", len(invalidos),
                f"stop_ids: {invalidos['stop_id'].tolist()[:20]}"))
        else:
            resultados.append(Resultado(cat, "Stops com coordenadas inválidas/nulas", "OK"))

    if "agency.txt" in gtfs:
        tzs = gtfs["agency.txt"]["agency_timezone"].dropna().unique().tolist()
        if len(tzs) > 1:
            resultados.append(Resultado(cat, "Consistência de agency_timezone",
                "AVISO", len(tzs),
                f"Múltiplos fusos horários: {tzs}"))
        else:
            resultados.append(Resultado(cat, "Consistência de agency_timezone", "OK"))

    return resultados

# ============================================================================
# ETAPA SAÍDA — CATEGORIA 2 — INTEGRIDADE INTERNA DO GTFS
# ============================================================================

def check_integridade(gtfs: dict) -> List[Resultado]:
    resultados = []
    cat = "2. Integridade GTFS"

    trips  = gtfs.get("trips.txt", pd.DataFrame())
    st     = gtfs.get("stop_times.txt", pd.DataFrame())
    freq   = gtfs.get("frequencies.txt", pd.DataFrame())
    routes = gtfs.get("routes.txt", pd.DataFrame())
    shapes = gtfs.get("shapes.txt", pd.DataFrame())

    if trips.empty:
        resultados.append(Resultado(cat, "trips.txt disponível", "ERRO", 0, "trips.txt ausente ou vazio"))
        return resultados

    trip_ids = set(trips["trip_id"].dropna().unique())

    if not st.empty:
        ids_com_st = set(st["trip_id"].dropna().unique())
        sem_st = trip_ids - ids_com_st
        if sem_st:
            resultados.append(Resultado(cat, "Trips sem stop_times",
                "ERRO", len(sem_st),
                f"trip_ids: {sorted(sem_st)[:30]}"))
        else:
            resultados.append(Resultado(cat, "Trips sem stop_times", "OK"))
    else:
        resultados.append(Resultado(cat, "Trips sem stop_times",
            "ERRO", 0, "stop_times.txt ausente"))

    if not st.empty:
        ids_orfaos = set(st["trip_id"].dropna().unique()) - trip_ids
        if ids_orfaos:
            resultados.append(Resultado(cat, "stop_times órfãos (sem trip)",
                "ERRO", len(ids_orfaos),
                f"trip_ids: {sorted(ids_orfaos)[:30]}"))
        else:
            resultados.append(Resultado(cat, "stop_times órfãos (sem trip)", "OK"))

    trips_reg = trips[trips["service_id"] != "EXCEP"]
    trip_ids_reg = set(trips_reg["trip_id"].dropna().unique())

    ids_com_freq = set(freq["trip_id"].dropna().unique()) if not freq.empty else set()

    if not st.empty and "departure_time" in st.columns:
        st_com_horario = st[st["departure_time"].notna() & (st["departure_time"].str.strip() != "")]
        ids_com_timetable = set(st_com_horario["trip_id"].dropna().unique())
    else:
        ids_com_timetable = set()

    trips_com_programacao = (ids_com_freq | ids_com_timetable) & trip_ids_reg
    sem_programacao = trip_ids_reg - trips_com_programacao

    n_freq = len(ids_com_freq & trip_ids_reg)
    n_timetable = len((trip_ids_reg - ids_com_freq) & ids_com_timetable)

    if sem_programacao:
        resultados.append(Resultado(cat, "Trips regulares sem programação (sem frequencies e sem timetable)",
            "ERRO", len(sem_programacao),
            f"trip_ids: {sorted(sem_programacao)[:30]}"))
    else:
        detalhe = f"{n_freq} viagens por frequencies, {n_timetable} viagens por timetable em stop_times"
        resultados.append(Resultado(cat, "Trips regulares com programação (frequencies ou timetable)", "OK", 0, detalhe))

    if not routes.empty:
        route_ids_validos = set(routes["route_id"].dropna().unique())
        sem_route = trips[~trips["route_id"].isin(route_ids_validos)]
        if len(sem_route) > 0:
            resultados.append(Resultado(cat, "Trips com route_id inválido",
                "ERRO", len(sem_route),
                f"trip_ids: {sem_route['trip_id'].tolist()[:30]}"))
        else:
            resultados.append(Resultado(cat, "Trips com route_id inválido", "OK"))

    if not shapes.empty:
        shape_ids_validos = set(shapes["shape_id"].dropna().unique())
        sem_shape = trips[trips["shape_id"].isna() | ~trips["shape_id"].isin(shape_ids_validos)]
        if len(sem_shape) > 0:
            resultados.append(Resultado(cat, "Trips com shape_id nulo ou inválido",
                "ERRO", len(sem_shape),
                f"trip_ids: {sem_shape['trip_id'].tolist()[:30]}"))
        else:
            resultados.append(Resultado(cat, "Trips com shape_id nulo ou inválido", "OK"))
    else:
        resultados.append(Resultado(cat, "Trips com shape_id nulo ou inválido",
            "ERRO", 0, "shapes.txt ausente"))

    if not routes.empty:
        route_ids_com_trip = set(trips["route_id"].dropna().unique())
        routes_sem_trip = set(routes["route_id"].dropna().unique()) - route_ids_com_trip
        if routes_sem_trip:
            resultados.append(Resultado(cat, "Routes sem trips",
                "AVISO", len(routes_sem_trip),
                f"route_ids: {sorted(routes_sem_trip)[:30]}"))
        else:
            resultados.append(Resultado(cat, "Routes sem trips", "OK"))

    return resultados

# ============================================================================
# ETAPA SAÍDA — CATEGORIA 3 — CALENDÁRIO E EXCEP
# ============================================================================

def check_calendario(gtfs: dict) -> List[Resultado]:
    resultados = []
    cat = "3. Calendário"

    trips     = gtfs.get("trips.txt", pd.DataFrame())
    cal       = gtfs.get("calendar.txt", pd.DataFrame())
    cal_dates = gtfs.get("calendar_dates.txt", pd.DataFrame())

    if not trips.empty and not cal.empty:
        service_ids_cal = set(cal["service_id"].dropna().unique())
        if not cal_dates.empty:
            service_ids_cal |= set(cal_dates["service_id"].dropna().unique())
        service_ids_trips = set(trips["service_id"].dropna().unique())
        sem_calendario = service_ids_trips - service_ids_cal
        if sem_calendario:
            resultados.append(Resultado(cat, "service_ids sem definição no calendário",
                "ERRO", len(sem_calendario),
                f"service_ids: {sorted(sem_calendario)}"))
        else:
            resultados.append(Resultado(cat, "service_ids sem definição no calendário", "OK"))

    if not trips.empty:
        excep_trips = trips[trips["service_id"] == "EXCEP"].copy()
        if excep_trips.empty:
            resultados.append(Resultado(cat, "EXCEP vinculado a trips com [evento] ou padrão TP",
                "AVISO", 0,
                "Nenhuma trip com service_id=EXCEP encontrada"))
        else:
            tem_colchete = excep_trips["trip_headsign"].fillna("").str.contains(r"\[")
            eh_padrao_tp = excep_trips["trip_short_name"].fillna("").str.upper().str.match(r"^\d{4}TP$")

            invalidos = excep_trips[~(tem_colchete | eh_padrao_tp)]
            n_eventos = tem_colchete.sum()
            n_tp = eh_padrao_tp.sum()

            if len(invalidos) > 0:
                resultados.append(Resultado(cat,
                    "EXCEP vinculado a trips com [evento] ou padrão TP",
                    "ERRO", len(invalidos),
                    f"Trips EXCEP sem [evento] e fora do padrão TP: "
                    f"{invalidos[['trip_id','trip_short_name','trip_headsign']].head(20).to_dict('records')}"))
            else:
                detalhe = f"{n_eventos} trips com [evento], {n_tp} trips no padrão TP"
                resultados.append(Resultado(cat,
                    "EXCEP vinculado a trips com [evento] ou padrão TP", "OK", 0, detalhe))

        trips_alt = trips[
            trips["trip_headsign"].fillna("").str.contains(r"\[")
        ]
        nao_excep = trips_alt[trips_alt["service_id"] != "EXCEP"]
        if len(nao_excep) > 0:
            resultados.append(Resultado(cat,
                "Trips [evento] com service_id != EXCEP",
                "ERRO", len(nao_excep),
                f"Exemplos: {nao_excep[['trip_id','trip_headsign','service_id']].head(20).to_dict('records')}"))
        else:
            resultados.append(Resultado(cat,
                "Trips [evento] com service_id != EXCEP", "OK"))

    return resultados

# ============================================================================
# ETAPA SAÍDA — CATEGORIA 4 — EXTENSÕES
# ============================================================================

def check_extensoes(gtfs: dict) -> List[Resultado]:
    resultados = []
    cat = "4. Extensões"

    trips  = gtfs.get("trips.txt", pd.DataFrame())
    st     = gtfs.get("stop_times.txt", pd.DataFrame())
    shapes = gtfs.get("shapes.txt", pd.DataFrame())

    if not st.empty and not shapes.empty and not trips.empty:
        st_ext = st.copy()
        st_ext["shape_dist_traveled"] = pd.to_numeric(
            st_ext.get("shape_dist_traveled", pd.Series(dtype=float)), errors="coerce"
        )
        st_ext_max = (
            st_ext.groupby("trip_id")["shape_dist_traveled"]
            .max()
            .reset_index()
            .rename(columns={"shape_dist_traveled": "ext_st"})
        )

        shapes_ext = shapes.copy()
        shapes_ext["shape_dist_traveled"] = pd.to_numeric(
            shapes_ext["shape_dist_traveled"], errors="coerce"
        )
        shapes_max = (
            shapes_ext.groupby("shape_id")["shape_dist_traveled"]
            .max()
            .reset_index()
            .rename(columns={"shape_dist_traveled": "ext_shape"})
        )

        comp = (
            trips[["trip_id", "shape_id"]]
            .merge(st_ext_max, on="trip_id", how="left")
            .merge(shapes_max, on="shape_id", how="left")
        )
        comp["dif_pct"] = (
            (comp["ext_st"] - comp["ext_shape"]).abs() / comp["ext_shape"].replace(0, float("nan")) * 100
        )
        divergentes = comp[comp["dif_pct"] > 5].dropna(subset=["dif_pct"])
        if len(divergentes) > 0:
            exemplos = [
                {"trip_id": r["trip_id"], "ext_st": round(float(r["ext_st"]), 1),
                 "ext_shape": round(float(r["ext_shape"]), 1), "dif_pct": round(float(r["dif_pct"]), 1)}
                for _, r in divergentes.head(10).iterrows()
            ]
            resultados.append(Resultado(cat,
                "Divergência extensão stop_times vs shapes (>5%)",
                "AVISO", len(divergentes),
                f"Exemplos: {exemplos}"))
        else:
            resultados.append(Resultado(cat, "Divergência extensão stop_times vs shapes (>5%)", "OK"))

    if not st.empty and "shape_dist_traveled" in st.columns:
        sem_dist = st[st["shape_dist_traveled"].isna() | (st["shape_dist_traveled"].str.strip() == "")]
        n_trips_sem = sem_dist["trip_id"].nunique()
        if n_trips_sem > 0:
            resultados.append(Resultado(cat,
                "Trips sem shape_dist_traveled em stop_times",
                "AVISO", n_trips_sem,
                f"Trips: {sem_dist['trip_id'].unique().tolist()[:20]}"))
        else:
            resultados.append(Resultado(cat, "Trips sem shape_dist_traveled em stop_times", "OK"))

    return resultados

# ============================================================================
# ETAPA SAÍDA — CATEGORIA 5 — SHAPE_IDS COM NOTAÇÃO CIENTÍFICA (+)
# ============================================================================

def check_shape_ids(gtfs: dict) -> List[Resultado]:
    resultados = []
    cat = "5. Shape IDs"

    shapes = gtfs.get("shapes.txt", pd.DataFrame())
    trips  = gtfs.get("trips.txt", pd.DataFrame())

    for nome, df in [("shapes.txt", shapes), ("trips.txt", trips)]:
        if df.empty or "shape_id" not in df.columns:
            continue
        ids_problematicos = df[
            df["shape_id"].fillna("").str.contains(r"\+")
        ]["shape_id"].unique()
        if len(ids_problematicos) > 0:
            resultados.append(Resultado(cat,
                f"shape_id com '+' em {nome}",
                "ERRO", len(ids_problematicos),
                f"shape_ids: {ids_problematicos[:30].tolist()}"))
        else:
            resultados.append(Resultado(cat, f"shape_id com '+' em {nome}", "OK"))

    if not trips.empty and not shapes.empty and "shape_id" in trips.columns:
        shape_ids_shapes = set(shapes["shape_id"].dropna().unique())
        trips_sem_shape = trips[
            trips["shape_id"].notna() &
            ~trips["shape_id"].isin(shape_ids_shapes)
        ]
        if len(trips_sem_shape) > 0:
            resultados.append(Resultado(cat,
                "shape_ids em trips sem correspondência em shapes",
                "ERRO", trips_sem_shape["shape_id"].nunique(),
                f"shape_ids: {trips_sem_shape['shape_id'].unique()[:20].tolist()}"))
        else:
            resultados.append(Resultado(cat, "shape_ids em trips sem correspondência em shapes", "OK"))

    return resultados

# ============================================================================
# ETAPA SAÍDA — CATEGORIA 6 — PADRÃO DE CABEÇALHO DA OS
# ============================================================================

def check_cabecalho_os(os_df: pd.DataFrame) -> List[Resultado]:
    resultados = []
    cat = "6. Cabeçalho OS"

    if os_df is None or os_df.empty:
        resultados.append(Resultado(cat, "OS disponível", "AVISO", 0, "Arquivo OS não carregado ou vazio"))
        return resultados

    cols_presentes = set(os_df.columns.tolist())
    cols_norm = [c.lower() for c in cols_presentes]

    eh_formato_sentido = any("sentido" in c for c in cols_norm) and any("extens" in c for c in cols_norm)
    eh_formato_ida_volta = any("extens" in c and "ida" in c for c in cols_norm)

    if eh_formato_sentido:
        cols_esperadas = ["Serviço", "Vista", "Consórcio", "Sentido", "Extensão"]
        formato_nome = "Programação por Sentido (tabela_programacao_OS)"
    elif eh_formato_ida_volta:
        cols_esperadas = ["Serviço", "Vista", "Consórcio", "Extensão de Ida", "Extensão de Volta"]
        formato_nome = "Resumo por Dia (tabela_dia_util / sabado / domingo)"
    else:
        cols_esperadas = ["Serviço", "Vista", "Consórcio"]
        formato_nome = "Desconhecido"

    faltando = []
    for c in cols_esperadas:
        if not any(c.lower() in col for col in cols_norm):
            faltando.append(c)

    if faltando:
        resultados.append(Resultado(cat, f"Colunas base da OS ({formato_nome})",
            "ERRO", len(faltando),
            f"Ausentes: {faltando}"))
    else:
        resultados.append(Resultado(cat, f"Colunas base da OS ({formato_nome})", "OK"))

    col_servico = next((c for c in os_df.columns if "servi" in c.lower()), None)
    if col_servico:
        vazios = os_df[os_df[col_servico].isna() | (os_df[col_servico].str.strip() == "")]
        if len(vazios) > 0:
            resultados.append(Resultado(cat, "Linhas com Serviço vazio na OS",
                "ERRO", len(vazios),
                "OS contém linhas sem identificação de serviço"))
        else:
            resultados.append(Resultado(cat, "Linhas com Serviço vazio na OS", "OK"))

    return resultados

# ============================================================================
# ETAPA SAÍDA — CATEGORIA 7 — CRUZAMENTO OS × GTFS
# ============================================================================

def check_os_vs_gtfs(gtfs: dict, os_df: pd.DataFrame) -> List[Resultado]:
    resultados = []
    cat = "7. OS × GTFS"

    if os_df is None or os_df.empty:
        resultados.append(Resultado(cat, "OS disponível para cruzamento",
            "AVISO", 0, "Arquivo OS não informado; cruzamento não realizado"))
        return resultados

    trips  = gtfs.get("trips.txt", pd.DataFrame())
    routes = gtfs.get("routes.txt", pd.DataFrame())

    if trips.empty or routes.empty:
        resultados.append(Resultado(cat, "GTFS disponível para cruzamento",
            "ERRO", 0, "trips.txt ou routes.txt ausentes"))
        return resultados

    col_servico = next((c for c in os_df.columns if "servi" in c.lower()), None)
    if not col_servico:
        resultados.append(Resultado(cat, "Coluna 'Serviço' identificada na OS",
            "ERRO", 0, "Coluna de serviço não encontrada"))
        return resultados

    linhas_os = set(os_df[col_servico].dropna().str.strip().unique())
    linhas_gtfs = set(trips["trip_short_name"].dropna().str.strip().unique())

    os_sem_gtfs = linhas_os - linhas_gtfs
    if os_sem_gtfs:
        resultados.append(Resultado(cat, "Linhas OS ausentes no GTFS",
            "ERRO", len(os_sem_gtfs),
            f"Linhas: {sorted(os_sem_gtfs)[:30]}"))
    else:
        resultados.append(Resultado(cat, "Linhas OS ausentes no GTFS", "OK"))

    trips_reg = trips[trips["service_id"] != "EXCEP"]
    linhas_reg = set(trips_reg["trip_short_name"].dropna().str.strip().unique())
    gtfs_sem_os = linhas_reg - linhas_os
    if gtfs_sem_os:
        resultados.append(Resultado(cat, "Linhas GTFS regulares ausentes na OS",
            "AVISO", len(gtfs_sem_os),
            f"Linhas: {sorted(gtfs_sem_os)[:30]}"))
    else:
        resultados.append(Resultado(cat, "Linhas GTFS regulares ausentes na OS", "OK"))

    trips_excep = trips[trips["service_id"] == "EXCEP"]
    linhas_excep = set(trips_excep["trip_short_name"].dropna().unique())
    excep_sem_os = linhas_excep - linhas_os
    if excep_sem_os:
        resultados.append(Resultado(cat, "Linhas EXCEP (alternativos) sem linha na OS",
            "AVISO", len(excep_sem_os),
            f"Linhas: {sorted(excep_sem_os)[:30]}"))
    else:
        resultados.append(Resultado(cat, "Linhas EXCEP (alternativos) sem linha na OS", "OK"))

    col_ext_ida = next((c for c in os_df.columns if "extens" in c.lower() and "ida" in c.lower()), None)
    col_ext_volta = next((c for c in os_df.columns if "extens" in c.lower() and "volta" in c.lower()), None)
    col_sentido = next((c for c in os_df.columns if "sentido" in c.lower()), None)
    col_extensao = next((c for c in os_df.columns if "extens" in c.lower() and not ("ida" in c.lower() or "volta" in c.lower())), None)
    shapes = gtfs.get("shapes.txt", pd.DataFrame())

    if not shapes.empty:
        shapes_num = shapes.copy()
        shapes_num["shape_dist_traveled"] = pd.to_numeric(shapes_num["shape_dist_traveled"], errors="coerce")
        ext_shape = (
            shapes_num.groupby("shape_id")["shape_dist_traveled"]
            .max()
            .reset_index()
            .rename(columns={"shape_dist_traveled": "ext_gtfs"})
        )
        trips_ext = trips.merge(ext_shape, on="shape_id", how="left")

        ext_gtfs_dir = (
            trips_ext[trips_ext["service_id"] != "EXCEP"]
            .groupby(["trip_short_name", "direction_id"])["ext_gtfs"]
            .max()
            .reset_index()
        )

        divergencias_ext = []

        if col_ext_ida and col_ext_volta:
            os_ext = os_df[[col_servico, col_ext_ida, col_ext_volta]].copy()
            os_ext.columns = ["trip_short_name", "ext_os_ida", "ext_os_volta"]
            os_ext["ext_os_ida"] = pd.to_numeric(os_ext["ext_os_ida"].str.replace(",", "."), errors="coerce")
            os_ext["ext_os_volta"] = pd.to_numeric(os_ext["ext_os_volta"].str.replace(",", "."), errors="coerce")

            for _, row in os_ext.iterrows():
                linha = row["trip_short_name"]
                for dir_id, ext_os in [("0", row["ext_os_ida"]), ("1", row["ext_os_volta"])]:
                    match = ext_gtfs_dir[
                        (ext_gtfs_dir["trip_short_name"] == linha) &
                        (ext_gtfs_dir["direction_id"] == dir_id)
                    ]
                    if match.empty or pd.isna(ext_os):
                        continue
                    ext_gtfs_val = match["ext_gtfs"].values[0]
                    if pd.isna(ext_gtfs_val) or ext_gtfs_val == 0:
                        continue
                    dif_pct = abs(ext_os - ext_gtfs_val) / ext_gtfs_val * 100
                    if dif_pct > 5:
                        divergencias_ext.append({
                            "linha": linha, "dir": dir_id,
                            "ext_os": float(ext_os), "ext_gtfs": float(ext_gtfs_val),
                            "dif_pct": round(float(dif_pct), 1)
                        })

        elif col_sentido and col_extensao:
            os_ext = os_df[[col_servico, col_sentido, col_extensao]].copy()
            os_ext.columns = ["trip_short_name", "sentido", "ext_os"]
            os_ext["ext_os"] = pd.to_numeric(os_ext["ext_os"].str.replace(",", "."), errors="coerce")
            sentido_map = {"ida": "0", "0": "0", "volta": "1", "1": "1"}

            for _, row in os_ext.iterrows():
                linha = row["trip_short_name"]
                dir_id = sentido_map.get(str(row["sentido"]).strip().lower())
                ext_os = row["ext_os"]
                if dir_id is None or pd.isna(ext_os):
                    continue
                match = ext_gtfs_dir[
                    (ext_gtfs_dir["trip_short_name"] == linha) &
                    (ext_gtfs_dir["direction_id"] == dir_id)
                ]
                if match.empty:
                    continue
                ext_gtfs_val = match["ext_gtfs"].values[0]
                if pd.isna(ext_gtfs_val) or ext_gtfs_val == 0:
                    continue
                dif_pct = abs(ext_os - ext_gtfs_val) / ext_gtfs_val * 100
                if dif_pct > 5:
                    divergencias_ext.append({
                        "linha": linha, "dir": dir_id,
                        "ext_os": float(ext_os), "ext_gtfs": float(ext_gtfs_val),
                        "dif_pct": round(float(dif_pct), 1)
                    })

        if divergencias_ext:
            resultados.append(Resultado(cat, "Divergência extensão OS × GTFS (>5%)",
                "AVISO", len(divergencias_ext),
                f"Exemplos: {divergencias_ext[:10]}"))
        else:
            resultados.append(Resultado(cat, "Divergência extensão OS × GTFS (>5%)", "OK"))

    return resultados

# ============================================================================
# ETAPA SAÍDA — CONSOLIDAÇÃO E EXPORTAÇÃO
# ============================================================================

def consolidar(resultados: List[Resultado]) -> pd.DataFrame:
    rows = []
    for r in resultados:
        rows.append({
            "categoria": r.categoria,
            "check": r.check,
            "status": r.status,
            "n_afetados": r.n_afetados,
            "detalhes": r.detalhes,
        })
    return pd.DataFrame(rows)

def exportar_csv(df: pd.DataFrame, output_dir: str, estudo: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"relatorio_validacao_{estudo}.csv")
    df.to_csv(path, sep=";", decimal=",", index=False, encoding="utf-8-sig")
    return path

def imprimir_resumo(df: pd.DataFrame):
    total  = len(df)
    ok     = (df["status"] == "OK").sum()
    avisos = (df["status"] == "AVISO").sum()
    erros  = (df["status"] == "ERRO").sum()

    print("\n" + "=" * 60)
    print("RESUMO DA VALIDACAO")
    print("=" * 60)
    print(f"  Total de checks : {total}")
    print(f"  [OK] Sucessos   : {ok}")
    print(f"  [AVISO] Avisos  : {avisos}")
    print(f"  [ERRO] Falhas   : {erros}")
    print("=" * 60)

    if avisos + erros > 0:
        print("\nProblemas identificados:")
        problemas = df[df["status"].isin(["AVISO", "ERRO"])][
            ["categoria", "check", "status", "n_afetados", "detalhes"]
        ]
        for _, row in problemas.iterrows():
            print(f"- [{row['status']}] {row['categoria']} > {row['check']} (afetados: {row['n_afetados']}): {row['detalhes']}")
    print()

# ============================================================================
# EXECUÇÃO DAS ETAPAS
# ============================================================================

def executar_entrada(cfg, gtfs, gtfs_path, auto_detectado) -> int:
    """Executa as validações da etapa de entrada. Retorna o total de FALHAS."""
    print("\n" + "=" * 80)
    print("              VALIDACAO DE GTFS DE ENTRADA")
    print("=" * 80)
    print(f"Arquivo GTFS: {gtfs_path}")
    print(f"Pasta resultados: {cfg['PASTA_RESULTADOS']}")
    print("=" * 80 + "\n")

    for fname in ["routes.txt", "trips.txt", "stop_times.txt",
                  "calendar.txt", "calendar_dates.txt", "shapes.txt"]:
        if fname not in gtfs:
            log_msg(f"Aviso: Arquivo {fname} nao encontrado no GTFS")

    routes_df = gtfs.get("routes.txt", pd.DataFrame())
    trips_df = gtfs.get("trips.txt", pd.DataFrame())

    log_msg(f"Routes carregadas: {len(routes_df)}")
    log_msg(f"Trips carregadas: {len(trips_df)}")
    print()

    meta = extrair_metadata_gtfs(gtfs_path.name) if auto_detectado else {}

    def nomear(chave):
        return cfg[chave] if chave in cfg["_explicitos"] else meta.get(chave, cfg[chave])

    gtfs_processar = nomear("gtfs_processar")
    ano_gtfs = nomear("ano_gtfs")
    mes_gtfs = nomear("mes_gtfs")
    estudo_gtfs = nomear("estudo_gtfs")

    todos_detalhes = []
    sucessos = []

    ok, erros, detalhes = validar_routes_com_trips(routes_df, trips_df)
    sucessos.append(ok)
    if not detalhes.empty:
        todos_detalhes.append(detalhes)
    print()

    ok, erros, detalhes = validar_trips_excepcionalidade(trips_df)
    sucessos.append(ok)
    if not detalhes.empty:
        todos_detalhes.append(detalhes)
    print()

    validar_trips_sem_route_id(trips_df)
    print()
    validar_service_ids(trips_df)
    print()

    if todos_detalhes:
        detalhes_consolidado = pd.concat(todos_detalhes, ignore_index=True)
    else:
        detalhes_consolidado = pd.DataFrame()

    imprimir_resumo_consolidado(detalhes_consolidado)

    arquivo_csv = exportar_relatorio_csv(
        detalhes_consolidado,
        Path(cfg["PASTA_RESULTADOS"]),
        f"validacao_gtfs_entrada_{gtfs_processar}_{ano_gtfs}{mes_gtfs}{estudo_gtfs}Q"
    )

    if not detalhes_consolidado.empty:
        total_falhas = int((detalhes_consolidado['status'] == 'FALHA').sum())
        total_ok = int((detalhes_consolidado['status'] == 'OK').sum())
        total_aviso = int((detalhes_consolidado['status'] == 'AVISO').sum())

        por_validacao = []
        for validacao, grupo in detalhes_consolidado.groupby('validacao', sort=False):
            f = int((grupo['status'] == 'FALHA').sum())
            por_validacao.append({
                "validacao": str(validacao),
                "total": int(len(grupo)),
                "ok": int((grupo['status'] == 'OK').sum()),
                "falha": f,
                "situacao": "OK" if f == 0 else "FALHA",
            })

        pendencias = []
        for _, r in detalhes_consolidado[detalhes_consolidado['status'] == 'FALHA'].iterrows():
            pendencias.append({
                "identificacao": _texto(r.get('trip_id')) or _texto(r.get('route_id')),
                "linha": _texto(r.get('route_short_name')),
                "validacao": _texto(r.get('validacao')),
                "problema": _texto(r.get('problema')),
                "sugestao": _texto(r.get('sugestao')),
            })
    else:
        total_falhas = 0
        total_ok = 0
        total_aviso = 0
        por_validacao = []
        pendencias = []

    salvar_resultados_json("entrada", {
        "gtfs": str(gtfs_path),
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "relatorio_csv": str(arquivo_csv),
        "resumo": {
            "total": int(len(detalhes_consolidado)),
            "ok": total_ok,
            "falha": total_falhas,
            "aviso": total_aviso,
        },
        "por_validacao": por_validacao,
        "pendencias": pendencias,
    })

    if not all(sucessos):
        total_falhas = max(total_falhas, 1)

    print(f"\nRESULTADO FINAL:")
    print(f"  Validacoes OK: {total_ok}")
    print(f"  Problemas (FALHA): {total_falhas}")
    print(f"  Relatorio completo: {arquivo_csv}")
    print("=" * 80)

    if total_falhas == 0:
        log_msg("SUCESSO: Todas as validacoes criticas passaram!")
    else:
        log_msg(f"FALHA: {total_falhas} problema(s) critico(s) encontrado(s)!")

    return total_falhas

def executar_saida(cfg, gtfs, gtfs_path) -> int:
    """Executa as validações da etapa de saída. Retorna o total de ERROS."""
    os_path = resolver_os(cfg)

    print(f"\n============================================================")
    print(f"Validação GTFS/OS — Estudo: {cfg['estudo']}")
    print(f"  GTFS : {gtfs_path}")
    print(f"  OS   : {os_path or '(não informada)'}")
    print(f"  Saída: {cfg['output_dir']}")
    print(f"============================================================\n")

    os_df = None
    if os_path and os.path.exists(os_path):
        print("Carregando OS...")
        os_df = load_os(os_path)
    else:
        print(f"  [AVISO] OS não encontrada em '{os_path}' — checks que cruzam OS serão marcados como AVISO.")

    print("Executando validações...")
    resultados: List[Resultado] = []
    resultados += check_arquivos_gtfs(gtfs)
    resultados += check_integridade(gtfs)
    resultados += check_calendario(gtfs)
    resultados += check_extensoes(gtfs)
    resultados += check_shape_ids(gtfs)
    resultados += check_cabecalho_os(os_df if os_df is not None else pd.DataFrame())
    resultados += check_os_vs_gtfs(gtfs, os_df if os_df is not None else pd.DataFrame())

    df_resultado = consolidar(resultados)
    caminho = exportar_csv(df_resultado, cfg["output_dir"], cfg["estudo"])
    imprimir_resumo(df_resultado)
    print(f"Relatório exportado para Google Sheets em: {caminho}\n")

    salvar_resultados_json("saida", {
        "gtfs": str(gtfs_path),
        "os": os_path or "",
        "estudo": cfg["estudo"],
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "relatorio_csv": str(caminho),
        "resumo": {
            "total": int(len(df_resultado)),
            "ok": int((df_resultado["status"] == "OK").sum()),
            "aviso": int((df_resultado["status"] == "AVISO").sum()),
            "erro": int((df_resultado["status"] == "ERRO").sum()),
        },
        "checks": [
            {
                "categoria": r.categoria,
                "check": r.check,
                "status": r.status,
                "n_afetados": int(r.n_afetados),
                "detalhes": r.detalhes,
            }
            for r in resultados
        ],
    })

    return int((df_resultado["status"] == "ERRO").sum())

# ============================================================================
# RELATÓRIO PDF CONSOLIDADO (leitura da chefia)
# ============================================================================

def _sanitizar_pdf(t):
    """Normaliza caracteres fora do Latin-1 para as fontes do PDF."""
    t = str(t)
    for a, b in (("→", "->"), ("←", "<-"), ("—", "-"), ("–", "-"),
                 ("×", " x "), ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"), ("…", "...")):
        t = t.replace(a, b)
    try:
        t.encode("latin-1")
    except UnicodeEncodeError:
        t = t.encode("latin-1", "replace").decode("latin-1")
    return t

def _truncar_pdf(t, largura_mm, tam_fonte=8):
    """Trunca o texto para caber na largura da coluna do PDF."""
    max_chars = max(5, int(largura_mm * 5.669 / tam_fonte) - 1)
    t = _sanitizar_pdf(t)
    if len(t) <= max_chars:
        return t
    return t[:max_chars - 3] + "..."

def _parecer(n_critico, n_aviso):
    """Parecer executivo: (rótulo, cor texto, cor fundo)."""
    if n_critico > 0:
        return "REPROVADO", (178, 34, 34), (253, 237, 237)
    if n_aviso > 0:
        return "APROVADO COM RESSALVAS", (178, 102, 0), (255, 244, 224)
    return "APROVADO", (30, 120, 60), (232, 245, 236)

def _cor_status(status):
    if status == "OK":
        return (232, 245, 236)
    if status == "AVISO":
        return (255, 244, 224)
    return (253, 237, 237)

def gerar_relatorio_pdf():
    """Gera o PDF consolidado com as duas etapas (dados dos JSONs salvos)."""
    ent = carregar_resultados_json("entrada")
    sai = carregar_resultados_json("saida")
    if not ent and not sai:
        log_msg("PDF consolidado: nenhum resultado de etapa salvo — nada a gerar")
        return None

    try:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos
    except ImportError:
        log_msg("AVISO: fpdf2 não instalado (pip install fpdf2) — PDF não gerado")
        return None

    FAM = "Helvetica"
    fontes = []
    for estilo, arq in (("", "arial.ttf"), ("B", "arialbd.ttf")):
        p = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / arq
        if p.exists():
            fontes.append((estilo, str(p)))
    if len(fontes) == 2:
        FAM = "Arial"

    class PDF(FPDF):
        def header(self):
            self.set_font(FAM, "B", 8)
            self.set_text_color(90, 90, 90)
            self.cell(0, 5, "Relatório Consolidado de Validação GTFS",
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_draw_color(0, 102, 153)
            self.set_line_width(0.3)
            y = self.get_y()
            self.line(self.l_margin, y, self.w - self.r_margin, y)
            self.ln(3)
            self.set_text_color(0, 0, 0)

        def footer(self):
            self.set_y(-14)
            self.set_font(FAM, "", 7)
            self.set_text_color(120, 120, 120)
            self.cell(0, 4, "Gerado automaticamente por validar_gtfs.py",
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.cell(0, 4, f"Pagina {self.page_no()}/{self.pages_count}",
                      align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_text_color(0, 0, 0)

    LARG = 180  # largura útil da A4 (210 - 2*15)

    pdf = PDF(format="A4")
    pdf.set_title("Relatório Consolidado de Validação GTFS")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=18)
    if FAM == "Arial":
        for estilo, caminho_fonte in fontes:
            pdf.add_font(FAM, estilo, caminho_fonte)
    pdf.add_page()

    def quebra(alt=20):
        if pdf.get_y() > pdf.h - pdf.b_margin - alt:
            pdf.add_page()

    def secao(n, titulo):
        quebra(22)
        pdf.ln(4)
        pdf.set_font(FAM, "B", 12)
        pdf.set_text_color(0, 102, 153)
        pdf.cell(0, 8, f"{n}. {titulo}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)

    def nota(texto, tam=8, cor=(100, 100, 100)):
        quebra(12)
        pdf.set_font(FAM, "", tam)
        pdf.set_text_color(*cor)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(LARG, 4.5, _sanitizar_pdf(texto),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)

    def tabela(cabecalhos, larguras, linhas, cores=None, tam=8, alt_hdr=7, alt_row=6):
        limite = pdf.h - pdf.b_margin - alt_row - 6

        def desenhar_cabecalho():
            pdf.set_font(FAM, "B", tam)
            pdf.set_fill_color(0, 102, 153)
            pdf.set_text_color(255, 255, 255)
            for h, w in zip(cabecalhos, larguras):
                pdf.cell(w, alt_hdr, _truncar_pdf(h, w, tam), border=1, fill=True,
                         new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.ln(alt_hdr)
            pdf.set_text_color(0, 0, 0)

        quebra(alt_hdr + 2 * alt_row)
        desenhar_cabecalho()
        for i, linha in enumerate(linhas):
            if pdf.get_y() > limite:
                pdf.add_page()
                desenhar_cabecalho()
            pdf.set_font(FAM, "", tam)
            cor = cores[i] if cores and i < len(cores) and cores[i] else (
                (245, 247, 250) if i % 2 else (255, 255, 255))
            pdf.set_fill_color(*cor)
            for valor, w in zip(linha, larguras):
                pdf.cell(w, alt_row, _truncar_pdf(valor, w, tam), border=1, fill=True,
                         new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.ln(alt_row)
        pdf.set_fill_color(255, 255, 255)
        pdf.ln(2)

    # --- Cabeçalho do relatório ---
    pdf.set_font(FAM, "B", 18)
    pdf.set_text_color(0, 102, 153)
    pdf.cell(0, 12, "Relatório Consolidado de Validação GTFS",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(FAM, "", 11)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 7, "Análise das etapas de entrada e saída do pipeline de processamento",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    res_ent = (ent or {}).get("resumo", {})
    res_sai = (sai or {}).get("resumo", {})
    n_falha_ent = int(res_ent.get("falha", 0))
    n_aviso_ent = int(res_ent.get("aviso", 0))
    n_erro_sai = int(res_sai.get("erro", 0))
    n_aviso_sai = int(res_sai.get("aviso", 0))
    rotulo, cor_txt, cor_fundo = _parecer(n_falha_ent + n_erro_sai,
                                          n_aviso_ent + n_aviso_sai)

    etapas_ok = ", ".join([n for n, d in (("entrada", ent), ("saida", sai)) if d])
    tabela(["Identificação", "Detalhe"], [55, 125], [
        ["GTFS analisado", (ent or sai or {}).get("gtfs", "")],
        ["Etapa de entrada (pré-pipeline)",
         (ent or {}).get("gerado_em", "não executada / sem dados salvos")],
        ["Etapa de saída (pós-pipeline)",
         (sai or {}).get("gerado_em", "não executada / sem dados salvos")],
        ["Emissão deste relatório", datetime.now().strftime("%d/%m/%Y %H:%M")],
        ["Etapas consolidadas", etapas_ok],
    ])

    # --- Sumário executivo ---
    secao(1, "Sumário executivo")

    if ent:
        resumo_ent = (f"{res_ent.get('total', 0)} verificações — "
                      f"{res_ent.get('ok', 0)} OK, {res_ent.get('falha', 0)} FALHA")
    else:
        resumo_ent = "etapa não executada (sem dados salvos)"

    if sai:
        resumo_sai = (f"{res_sai.get('total', 0)} verificações — "
                      f"{res_sai.get('ok', 0)} OK, {res_sai.get('aviso', 0)} AVISO, "
                      f"{res_sai.get('erro', 0)} ERRO")
    else:
        resumo_sai = "etapa não executada (sem dados salvos)"

    tabela(["Indicador", "Resultado"], [55, 125], [
        ["Entrada (pré-pipeline)", resumo_ent],
        ["Saída (pós-pipeline)", resumo_sai],
        ["Relatório detalhado da entrada (CSV)", (ent or {}).get("relatorio_csv", "—")],
        ["Relatório resumo da saída (CSV)", (sai or {}).get("relatorio_csv", "—")],
    ])

    quebra(30)
    y0 = pdf.get_y()
    pdf.set_fill_color(*cor_fundo)
    pdf.set_draw_color(*cor_txt)
    pdf.set_line_width(0.5)
    pdf.rect(pdf.l_margin, y0, LARG, 16, style="DF")
    pdf.set_xy(pdf.l_margin, y0)
    pdf.set_font(FAM, "B", 13)
    pdf.set_text_color(*cor_txt)
    pdf.cell(LARG, 16, f"PARECER GERAL: {rotulo}", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.set_line_width(0.2)
    pdf.ln(3)

    # --- Etapa de entrada ---
    secao(2, "Validação de entrada (pré-pipeline)")
    if ent:
        nota(f"GTFS: {ent.get('gtfs', '')}  |  Executado em: {ent.get('gerado_em', '')}")
        pv = ent.get("por_validacao", [])
        tabela(["Verificação", "Total", "OK", "Falha", "Situação"],
               [64, 18, 18, 18, 62],
               [[p["validacao"], str(p["total"]), str(p["ok"]), str(p["falha"]), p["situacao"]]
                for p in pv],
               cores=[(232, 245, 236) if p["situacao"] == "OK" else (253, 237, 237)
                      for p in pv])

        pend = ent.get("pendencias", [])
        if pend:
            pdf.set_font(FAM, "B", 9)
            pdf.cell(0, 6, f"Pendências críticas: {len(pend)} — exibindo até 40",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1)
            tabela(["Nº", "Identificação", "Linha", "Verificação", "Problema", "Sugestão de correção"],
                   [8, 28, 14, 32, 48, 50],
                   [[str(i), p["identificacao"], p["linha"], p["validacao"],
                     p["problema"], p["sugestao"]]
                    for i, p in enumerate(pend[:40], 1)])
        else:
            nota("Nenhuma pendência crítica na etapa de entrada.")
    else:
        nota("Etapa de entrada não executada neste consolidado (sem dados salvos). "
             "Execute: python validar_gtfs.py --etapa entrada")

    # --- Etapa de saída ---
    secao(3, "Validação de saída (pós-pipeline)")
    if sai:
        nota(f"GTFS: {sai.get('gtfs', '')}  |  OS: {sai.get('os') or 'não informada'}  |  "
             f"Estudo: {sai.get('estudo', '')}  |  Executado em: {sai.get('gerado_em', '')}")
        checks = sai.get("checks", [])
        tabela(["Nº", "Categoria", "Verificação", "Status", "Afetados"],
               [8, 34, 72, 30, 36],
               [[str(i), c["categoria"], c["check"], c["status"], str(c["n_afetados"])]
                for i, c in enumerate(checks, 1)],
               cores=[_cor_status(c["status"]) for c in checks])

        achados = [c for c in checks
                   if c["status"] in ("AVISO", "ERRO") and _texto(c.get("detalhes"))]
        if achados:
            pdf.set_font(FAM, "B", 9)
            pdf.cell(0, 6, f"Principais achados (AVISO/ERRO): {len(achados)}",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1)
            tabela(["Status", "Categoria", "Achado"], [22, 45, 113],
                   [[c["status"], c["categoria"], c["detalhes"]] for c in achados[:30]],
                   cores=[_cor_status(c["status"]) for c in achados[:30]])
    else:
        nota("Etapa de saída não executada neste consolidado (sem dados salvos). "
             "Execute: python validar_gtfs.py --etapa saida")

    # --- Conclusões ---
    secao(4, "Conclusões e recomendações")
    bullets = []
    if ent:
        if n_falha_ent:
            bullets.append(f"Etapa de entrada: {n_falha_ent} ocorrência(s) crítica(s) — "
                           "correção obrigatória antes do processamento (sugestões no item 2 "
                           "e no relatório CSV detalhado).")
        else:
            bullets.append("Etapa de entrada: aprovada — nenhuma pendência crítica; "
                           "GTFS apto a seguir para o processamento.")
    else:
        bullets.append("Etapa de entrada sem dados neste consolidado — executar a validação "
                       "de entrada para o parecer completo.")
    if sai:
        if n_erro_sai:
            bullets.append(f"Etapa de saída: {n_erro_sai} verificação(ões) com ERRO — "
                           "não se recomenda a liberação até a regularização (detalhes no item 3).")
        else:
            bullets.append("Etapa de saída: nenhum erro de conformidade; estrutura do GTFS "
                           "final compatível com o especificado.")
        if n_aviso_sai:
            bullets.append(f"{n_aviso_sai} aviso(s) para acompanhamento — não bloqueiam a "
                           "aprovação, mas devem ser monitorados pela equipe técnica.")
    else:
        bullets.append("Etapa de saída sem dados neste consolidado — executar a validação "
                       "pós-pipeline para o parecer completo.")
    bullets.append("Os relatórios CSV anexos (detalhado da entrada e resumo da saída) contêm "
                   "a lista completa de ocorrências para cada correção.")

    for b in bullets:
        quebra(12)
        pdf.set_font(FAM, "", 9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(LARG, 5, _sanitizar_pdf("-  " + b),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

    caminho = SCRIPT_DIR / ARQUIVO_PDF_CONSOLIDADO
    pdf.output(str(caminho))
    log_msg(f"Relatório PDF consolidado salvo em: {caminho}")
    return str(caminho)

# ============================================================================
# MAIN
# ============================================================================

def main() -> int:
    cfg = parse_config()

    if cfg["apenas_pdf"]:
        log_msg("Gerando apenas o PDF consolidado a partir dos resultados salvos...")
        return 0 if gerar_relatorio_pdf() else 1

    etapa = cfg["etapa"]
    gtfs_path, auto_detectado = resolver_gtfs(cfg)

    print("\n" + "=" * 80)
    print(f"VALIDACAO GTFS — ETAPA: {etapa.upper()}")
    print("=" * 80)
    print(f"GTFS: {gtfs_path}")
    print("=" * 80)

    if not gtfs_path.exists():
        log_msg(f"ERRO: GTFS não encontrado em {gtfs_path}")
        return 1

    gtfs = load_gtfs(gtfs_path)

    falhas = 0
    if etapa in ("entrada", "todas"):
        falhas += executar_entrada(cfg, gtfs, gtfs_path, auto_detectado)
    if etapa in ("saida", "todas"):
        falhas += executar_saida(cfg, gtfs, gtfs_path)

    if not cfg["sem_pdf"]:
        gerar_relatorio_pdf()

    return 1 if falhas > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
