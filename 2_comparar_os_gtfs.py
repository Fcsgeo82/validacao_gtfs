"""
2_comparar_os_gtfs.py — Conteúdo da OS x GTFS por tipo de dia
=============================================================
Compara as partidas da OS (Ordem de Serviço) com as partidas do GTFS
reexpandidas a partir de frequencies.txt (+ viagens timetable em
stop_times.txt), para TODOS os tipos de dia presentes na OS:

    Dia Útil          -> U_REG
    Sábado            -> S_REG
    Domingo           -> D_REG
    Ponto Facultativo -> F_REG

Mapeamento configurável via `mapa_dia_servico` no --config.

Formatos de OS aceitos (sep e encoding auto-detectados):
    - Por Sentido com faixas: tabela_programacao_OS.csv
      ('partidas 00:00-01:00 - Dia Útil') e OS 190/191
      ('Partidas 00h à 01h - Dia Útil');
    - Resumo por Dia ('Partidas Ida - Dia Útil') — comparação por total.

Entradas (auto-detectadas na pasta do script, sobrescrevíveis):
    OS:   OS.csv > tabela_programacao_OS.csv > tabela_*.csv > outros .csv
    GTFS: GTFS.zip > zips válidos (routes/trips/stops), mais recente primeiro

Saídas (output_dir, padrão = pasta do script):
    relatorio_<prefixo>_servicos.csv — classificação por (tipo_dia, serviço, consórcio)
    relatorio_<prefixo>_celulas.csv  — partidas OS x GTFS por
                                       (tipo_dia, serviço, sentido, faixa)
    resumo_<prefixo>.txt             — resumo legível

Uso:
    python 2_comparar_os_gtfs.py
    python 2_comparar_os_gtfs.py --os OS.csv --gtfs GTFS.zip
    python 2_comparar_os_gtfs.py --config '{"prefixo": "os191_vs_0174"}'

Convenção de reexpansão (alinhada a 7_lista_partidas.py do processar_gtfs):
partidas em [start, end) com passo headway_secs, n = ceil((end-start)/headway)
— equivale exatamente a while t < end (exclui end alinhado, gerando todas
as partidas estritamente dentro da janela).
Alternativas via config: "floor" ou "floor_mais_1".

Exit code: 0 = OK; 1 = entradas não encontradas ou serviços da OS com
partidas ausentes no GTFS correspondente (fail_se_ausente, padrão True).
Divergências de célula são AVISO e não falham.
"""

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent

# Entradas padrão na raiz do projeto (sobrescrevíveis via --config/--os/--gtfs)
OS_PATH = str(SCRIPT_DIR / "OS.csv")
GTFS_PATH = str(SCRIPT_DIR / "GTFS.zip")
OUTPUT_DIR = str(SCRIPT_DIR)

# Convenção de contagem da grade de frequencies (script 7 do processar_gtfs: while t < end)
FORMULA_PADRAO = "ceil"

# Mapeamento nome do tipo de dia (normalizado, sem acento, minúsculo) -> service_id
MAPA_DIA_SERVICO_PADRAO = {
    "dia util": "U_REG",
    "sabado": "S_REG",
    "domingo": "D_REG",
    "ponto facultativo": "F_REG",
}

SENTIDO_PAR_DIR = {"ida": "0", "volta": "1", "circular": "0", "0": "0", "1": "1"}
DIR_PAR_SENTIDO = {"0": "Ida", "1": "Volta"}

# 'partidas 00:00-01:00 - Dia Útil'  ou  'Partidas 00h à 01h - Dia Útil'
RE_FAIXA = re.compile(
    r"(?i)^partidas\s+(\d{1,2})(?::\d{2}|h)?\s*(?:à|-|–)?\s*(\d{1,2})(?::\d{2}|h)?\s*-\s*(.+)$"
)
# 'Partidas Ida - Dia Útil' (formato Resumo por Dia)
RE_TOTAL = re.compile(r"(?i)^partidas\s+(ida|volta)\s*-\s*(.+)$")


def log_msg(msg):
    print(f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] {msg}")


# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

def parse_config() -> dict:
    parser = argparse.ArgumentParser(
        description="Compara o conteúdo de partidas da OS com o GTFS por tipo de dia")
    parser.add_argument("--config", type=str, help="JSON inline ou caminho para arquivo .json")
    parser.add_argument("--os", dest="os_path", type=str, help="Caminho do CSV da OS")
    parser.add_argument("--gtfs", dest="gtfs_path", type=str, help="Caminho do .zip do GTFS")
    parser.add_argument("--output-dir", type=str, help="Pasta dos relatórios")
    parser.add_argument("--prefixo", type=str, help="Prefixo dos arquivos de saída")
    args, _ = parser.parse_known_args()

    cfg = {
        "os_path": OS_PATH,
        "gtfs_path": GTFS_PATH,
        "output_dir": OUTPUT_DIR,
        "prefixo": "comparar_os_gtfs",
        "formula": FORMULA_PADRAO,
        "tolerancia": 0,
        "encoding_os": "",
        "fail_se_ausente": True,
        "mapa_dia_servico": dict(MAPA_DIA_SERVICO_PADRAO),
    }

    if args.config:
        config_str = args.config.strip()
        if os.path.isfile(config_str):
            with open(config_str, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
        else:
            user_cfg = json.loads(config_str)
        cfg.update(user_cfg)

    if args.os_path:
        cfg["os_path"] = args.os_path
    if args.gtfs_path:
        cfg["gtfs_path"] = args.gtfs_path
    if args.output_dir:
        cfg["output_dir"] = args.output_dir
    if args.prefixo:
        cfg["prefixo"] = args.prefixo

    if cfg["formula"] not in ("floor", "floor_mais_1", "ceil"):
        raise SystemExit(f"formula inválida: {cfg['formula']!r} (use floor, floor_mais_1 ou ceil)")
    return cfg


# ============================================================================
# RESOLUÇÃO DE ENTRADAS
# ============================================================================

def resolver_os(cfg) -> str:
    """Resolve o CSV da OS: explícito > OS.csv > tabela_programacao_OS.csv >
    tabela_*.csv > outros .csv (ignora relatórios gerados)."""
    explicito = cfg.get("os_path") or ""
    if explicito and os.path.isfile(explicito) and \
            os.path.normpath(explicito) != os.path.normpath(OS_PATH):
        return explicito

    pasta = Path(cfg.get("output_dir") or SCRIPT_DIR)
    raiz = SCRIPT_DIR
    cands = []
    for base in dict.fromkeys([pasta, raiz]):
        cands += [
            str(base / "OS.csv"),
            str(base / "tabela_programacao_OS.csv"),
        ] + sorted(glob.glob(os.path.join(str(base), "tabela_*.csv")))
        cands += [
            f for f in sorted(glob.glob(os.path.join(str(base), "*.csv")))
            if not os.path.basename(f).startswith(("relatorio_", "validacao_gtfs_", "2_"))
        ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return explicito


def resolver_gtfs(cfg) -> str:
    """Resolve o GTFS: explícito > GTFS.zip > zips válidos (mais recente)."""
    explicito = cfg.get("gtfs_path") or ""
    if explicito and os.path.isfile(explicito) and \
            os.path.normpath(explicito) != os.path.normpath(GTFS_PATH):
        return explicito

    pasta = Path(cfg.get("output_dir") or SCRIPT_DIR)
    raiz = SCRIPT_DIR
    vistos = set()
    for base in dict.fromkeys([pasta, raiz]):
        padrao = base / "GTFS.zip"
        if padrao.is_file():
            return str(padrao)
        if not base.exists():
            continue
        for zp in base.glob("*.zip"):
            chave = str(zp.resolve())
            if chave in vistos:
                continue
            vistos.add(chave)
            try:
                with zipfile.ZipFile(zp, "r") as z:
                    nomes = z.namelist()
                    if any(n in nomes for n in ("routes.txt", "trips.txt", "stops.txt")):
                        return str(zp)
            except zipfile.BadZipFile:
                log_msg(f"Aviso: ignorando ZIP inválido: {zp.name}")
    return explicito


# ============================================================================
# LEITURA DOS DADOS
# ============================================================================

def carregar_gtfs(caminho: str) -> dict:
    """Carrega só os arquivos necessários: trips, frequencies e stop_times
    (colunas mínimas) — evita ler shapes/stops de feeds grandes."""
    log_msg(f"Lendo GTFS: {caminho}")
    gtfs = {}
    with zipfile.ZipFile(caminho, "r") as z:
        nomes = set(z.namelist())

        def _ler(nome, usecols=None):
            if nome not in nomes:
                return pd.DataFrame()
            try:
                df = pd.read_csv(z.open(nome), dtype=str, usecols=usecols)
            except (pd.errors.EmptyDataError, ValueError):
                return pd.DataFrame()
            df.columns = df.columns.str.strip()
            return df

        gtfs["trips.txt"] = _ler("trips.txt")
        gtfs["frequencies.txt"] = _ler("frequencies.txt")
        gtfs["stop_times.txt"] = _ler(
            "stop_times.txt", usecols=["trip_id", "stop_sequence", "departure_time"])
        gtfs["routes.txt"] = _ler("routes.txt", usecols=["route_id", "agency_id"])
        gtfs["agency.txt"] = _ler("agency.txt", usecols=["agency_id", "agency_name"])
    for nome, df in gtfs.items():
        log_msg(f"  {nome}: {len(df)} linhas")
    return gtfs


def _num(v) -> float:
    """Converte valor de OS ('16,000' / '16.0' / vazio) em float."""
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", ".")
    if not s or s.lower() == "nan":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def normalizar_tipo_dia(texto: str):
    t = unicodedata.normalize("NFKD", str(texto).strip())
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    if "facultativ" in t:
        return "ponto facultativo"
    if "domingo" in t:
        return "domingo"
    if "sabado" in t:
        return "sabado"
    if "util" in t:
        return "dia util"
    return None


def carregar_os(caminho: str, mapa_dia: dict, encoding_os: str = ""):
    """Lê a OS (sep/encoding auto-detectados) e retorna:
    - long: uma linha por (linha_os, svc, sentido, dir, tipo, faixa, n>0);
    - faixas_por_tipo: {(tipo, (a, b))} presentes na OS;
    - totais_por_tipo: {(tipo, 'ida'|'volta')} do formato Resumo por Dia.
    Aceita os dois formatos de coluna de partidas (faixa HH:MM / 'Hh à Hh' e
    total Ida/Volta)."""
    with open(caminho, "rb") as fh:
        primeira = fh.readline().decode("latin-1")
    sep = ";" if primeira.count(";") >= primeira.count(",") else ","

    df = None
    for enc in ([encoding_os] if encoding_os else ["utf-8-sig", "latin-1"]):
        try:
            df = pd.read_csv(caminho, sep=sep, dtype=str, encoding=enc)
            log_msg(f"Lendo OS: {caminho} (sep={sep!r}, encoding={enc})")
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise SystemExit(f"Não foi possível decodificar a OS: {caminho}")
    df.columns = [c.strip() for c in df.columns]

    col_servico = next((c for c in df.columns if "servi" in c.lower()), None)
    col_sentido = next((c for c in df.columns if "sentido" in c.lower()), None)
    col_vista = next((c for c in df.columns if "vista" in c.lower()), None)
    col_consorcio = next((c for c in df.columns if "cons" in c.lower()), None)
    if not col_servico:
        raise SystemExit("Coluna 'Serviço' não encontrada na OS")

    cols_faixa, cols_total = {}, {}
    for c in df.columns:
        m = RE_FAIXA.match(c)
        if m:
            tipo = normalizar_tipo_dia(m.group(3))
            if tipo in mapa_dia:
                cols_faixa[(tipo, (int(m.group(1)), int(m.group(2))))] = c
            continue
        m2 = RE_TOTAL.match(c)
        if m2:
            tipo = normalizar_tipo_dia(m2.group(2))
            if tipo in mapa_dia:
                cols_total[(tipo, m2.group(1).lower())] = c
    if not cols_faixa and not cols_total:
        raise SystemExit("Nenhuma coluna de partidas por tipo de dia reconhecida na OS "
                         "(formatos: 'partidas 00:00-01:00 - Dia Útil', "
                         "'Partidas 00h à 01h - Dia Útil' ou 'Partidas Ida - Dia Útil')")

    faixa_para_rotulo = lambda t: f"{t[0]:02d}h-{t[1]:02d}h" if isinstance(t, tuple) else "total"
    registros = []
    for _, r in df.iterrows():
        svc = str(r.get(col_servico, "")).strip()
        if not svc or svc.lower() == "nan":
            continue
        sentido = str(r.get(col_sentido, "")).strip() if col_sentido else ""
        if sentido.lower() == "nan":
            sentido = ""
        vista = str(r.get(col_vista, "")).strip() if col_vista else ""
        if vista.lower() == "nan":
            vista = ""
        consorcio = str(r.get(col_consorcio, "")).strip() if col_consorcio else ""
        if consorcio.lower() == "nan":
            consorcio = ""
        dir_linha = SENTIDO_PAR_DIR.get(sentido.lower(), "")
        for (tipo, faixa), col in cols_faixa.items():
            n = int(round(_num(r[col])))
            if n > 0:
                registros.append({"svc": svc, "consorcio": consorcio, "sentido": sentido,
                                  "dir": dir_linha, "vista": vista, "tipo": tipo,
                                  "faixa": faixa_para_rotulo(faixa), "n": n})
        for (tipo, lado), col in cols_total.items():
            n = int(round(_num(r[col])))
            if n > 0:
                registros.append({"svc": svc, "consorcio": consorcio,
                                  "sentido": sentido or lado.capitalize(),
                                  "dir": SENTIDO_PAR_DIR.get(lado, ""), "vista": vista,
                                  "tipo": tipo, "faixa": "total", "n": n})

    long = pd.DataFrame(registros, columns=["svc", "consorcio", "sentido", "dir", "vista", "tipo", "faixa", "n"])
    faixas_por_tipo = sorted({(t, f) for (t, f) in cols_faixa})
    totais_por_tipo = sorted({(t, l) for (t, l) in cols_total})
    log_msg(f"  OS: {df.shape[0]} linhas, {long['svc'].nunique()} serviços, "
            f"{len(cols_faixa)} colunas de faixa, {len(cols_total)} de total; "
            f"{len(long)} células com partidas > 0")
    return long, faixas_por_tipo, totais_por_tipo


# ============================================================================
# REEXPANSÃO DO GTFS
# ============================================================================

def reexpandir_service(gtfs: dict, service_id: str, formula: str) -> pd.DataFrame:
    """Reexpande as partidas de um service_id em (trip_short_name, direction_id,
    hora_do_dia): frequencies com a convenção escolhida + viagens timetable
    (sem frequencies) pela primeira saída em stop_times."""
    trips = gtfs.get("trips.txt", pd.DataFrame())
    freq = gtfs.get("frequencies.txt", pd.DataFrame())
    if trips.empty or "service_id" not in trips.columns:
        return pd.DataFrame(columns=["svc", "dir", "hora"])
    alvo = trips[trips["service_id"] == service_id]
    partes = []

    if not freq.empty and not alvo.empty:
        f = freq[freq["trip_id"].isin(set(alvo["trip_id"]))].merge(
            alvo[["trip_id", "trip_short_name", "direction_id"]], on="trip_id")
        if not f.empty:
            start = pd.to_timedelta(f["start_time"], errors="coerce").dt.total_seconds().to_numpy()
            end = pd.to_timedelta(f["end_time"], errors="coerce").dt.total_seconds().to_numpy()
            hw = pd.to_numeric(f["headway_secs"], errors="coerce").to_numpy()
            ok = (~np.isnan(start)) & (~np.isnan(end)) & (hw > 0)
            start, end, hw = start[ok], end[ok], hw[ok]
            if formula == "floor_mais_1":
                n = ((end - start) // hw + 1).astype(int)
            elif formula == "floor":
                n = ((end - start) // hw).astype(int)
            else:  # ceil (padrão) — convenção exata de while t < end do 7_lista_partidas.py
                n = np.ceil((end - start) / hw).astype(int)
            n = np.clip(n, 0, None)
            svc = f.loc[ok, "trip_short_name"].astype(str).str.strip().to_numpy()
            dir_ = f.loc[ok, "direction_id"].astype(str).str.strip().to_numpy()
            dir_ = np.where(np.isin(dir_, ["nan", "None", ""]), "", dir_)
            max_n = int(n.max()) if len(n) else 0
            if max_n > 0:
                idx = np.repeat(np.arange(len(n)), max_n)
                k = np.tile(np.arange(max_n), len(n))
                sel = k < n[idx]
                hora = ((start[idx][sel] + k[sel] * hw[idx][sel]) % 86400) / 3600
                partes.append(pd.DataFrame({
                    "svc": np.repeat(svc, max_n)[sel],
                    "dir": np.repeat(dir_, max_n)[sel],
                    "hora": hora,
                }))

    st = gtfs.get("stop_times.txt", pd.DataFrame())
    trips_sf = alvo[~alvo["trip_id"].isin(set(freq["trip_id"]))] if not freq.empty else alvo
    if not st.empty and not trips_sf.empty and "departure_time" in st.columns:
        stn = st[["trip_id", "stop_sequence", "departure_time"]].copy()
        stn["seq"] = pd.to_numeric(stn["stop_sequence"], errors="coerce")
        stn = stn.dropna(subset=["seq"])
        primeiro = stn.loc[stn.groupby("trip_id")["seq"].idxmin()]
        reg = trips_sf[["trip_id", "trip_short_name", "direction_id"]].merge(
            primeiro[["trip_id", "departure_time"]], on="trip_id")
        if not reg.empty:
            h = pd.to_timedelta(reg["departure_time"], errors="coerce").dt.total_seconds()
            h = (h % 86400) / 3600
            dir_ = reg["direction_id"].astype(str).str.strip()
            dir_ = dir_.where(~dir_.isin(["nan", "None", ""]), "")
            partes.append(pd.DataFrame({
                "svc": reg["trip_short_name"].astype(str).str.strip(),
                "dir": dir_.to_numpy(),
                "hora": h.to_numpy(),
            }))

    if not partes:
        return pd.DataFrame(columns=["svc", "dir", "hora"])
    return pd.concat(partes, ignore_index=True).dropna(subset=["hora"])


def qual_faixa(hora: np.ndarray, bounds, tem_total: bool) -> np.ndarray:
    """Atribui cada hora à faixa da OS (a <= h < b); 'total' se o OS só tem
    comparação por total; '' para horas fora de qualquer faixa."""
    if tem_total and not bounds:
        return np.full(len(hora), "total", dtype=object)
    if not bounds:
        return np.full(len(hora), "", dtype=object)
    rotulos = [f"{a:02d}h-{b:02d}h" for a, b in bounds]
    condicoes = [(hora >= a) & (hora < b) for a, b in bounds]
    return np.select(condicoes, rotulos, default="")


# ============================================================================
# PRINCIPAL
# ============================================================================

def main() -> int:
    cfg = parse_config()
    mapa_dia = cfg["mapa_dia_servico"]
    formula = cfg["formula"]
    tolerancia = int(cfg["tolerancia"])
    prefixo = str(cfg["prefixo"])
    pasta_saida = Path(cfg["output_dir"])
    pasta_saida.mkdir(parents=True, exist_ok=True)

    end_os = resolver_os(cfg)
    end_gtfs = resolver_gtfs(cfg)
    if not end_os or not os.path.isfile(end_os):
        log_msg(f"ERRO: OS não encontrada (esperado OS.csv na raiz ou informe --os): {end_os}")
        return 1
    if not end_gtfs or not os.path.isfile(end_gtfs):
        log_msg(f"ERRO: GTFS não encontrado (esperado GTFS.zip na raiz ou informe --gtfs): {end_gtfs}")
        return 1

    print("\n" + "=" * 80)
    print("COMPARACAO DE CONTEUDO: OS x GTFS (partidas por tipo de dia)")
    print("=" * 80)
    print(f"OS  : {end_os}")
    print(f"GTFS: {end_gtfs}")
    print(f"Formula de reexpansao: {formula}")
    print("=" * 80)

    gtfs = carregar_gtfs(end_gtfs)
    trips = gtfs["trips.txt"]
    if trips.empty:
        log_msg("ERRO: trips.txt ausente ou vazio no GTFS")
        return 1
    trips["tss"] = trips["trip_short_name"].astype(str).str.strip()
    linhas_excep = set(trips.loc[trips["service_id"] == "EXCEP", "tss"])

    # Mapeamento de fallback do GTFS: trip_short_name -> agency_name (consórcio)
    gtfs_consorcio = {}
    routes = gtfs.get("routes.txt", pd.DataFrame())
    agency = gtfs.get("agency.txt", pd.DataFrame())
    if not routes.empty and not agency.empty and \
            "route_id" in trips.columns and "route_id" in routes.columns and \
            "agency_id" in routes.columns and "agency_id" in agency.columns:
        m_ag = (
            trips[["tss", "route_id"]]
            .drop_duplicates()
            .merge(routes[["route_id", "agency_id"]].drop_duplicates(), on="route_id")
            .merge(agency[["agency_id", "agency_name"]].drop_duplicates(), on="agency_id")
        )
        for _, row in m_ag.drop_duplicates(subset=["tss"]).iterrows():
            nome_ag = str(row["agency_name"]).strip()
            if nome_ag and nome_ag.lower() != "nan":
                gtfs_consorcio[str(row["tss"]).strip()] = nome_ag

    os_long, faixas_por_tipo, totais_por_tipo = carregar_os(
        end_os, mapa_dia, str(cfg.get("encoding_os") or ""))

    tipos_desta_os = [t for t in mapa_dia if t in set(os_long["tipo"])]

    registros_serv, registros_cel, linhas_res = [], [], []
    linhas_res.append("COMPARACAO DE CONTEUDO: OS x GTFS (partidas por tipo de dia)")
    linhas_res.append(f"OS  : {Path(end_os).name}")
    linhas_res.append(f"GTFS: {Path(end_gtfs).name}")
    linhas_res.append(f"Formula de reexpansao de frequencies: {formula}")
    linhas_res.append(f"Tolerancia por celula: {tolerancia}")
    linhas_res.append("")

    falhas_ausentes = 0
    for tipo in tipos_desta_os:
        svc_id = mapa_dia[tipo]
        dep = reexpandir_service(gtfs, svc_id, formula)
        linhas_dia = set(trips.loc[trips["service_id"] == svc_id, "tss"])
        os_tipo = os_long[os_long["tipo"] == tipo]
        n_os_total = int(os_tipo["n"].sum()) if not os_tipo.empty else 0

        linhas_res.append("=" * 70)
        titulo = f"{tipo.capitalize()} ({svc_id})"
        linhas_res.append(titulo.upper())
        linhas_res.append("=" * 70)
        if os_tipo.empty and not linhas_dia:
            linhas_res.append("Sem partidas na OS e sem viagens no GTFS para este tipo de dia.")
            linhas_res.append("")
            continue
        linhas_res.append(f"OS: {os_tipo['svc'].nunique()} servicos com partidas ({n_os_total} partidas)")
        linhas_res.append(f"GTFS {svc_id}: {len(linhas_dia)} linhas (trip_short_name), "
                          f"{len(dep)} partidas reexpandidas (freq + timetable)")

        # --- Classificação por serviço --------------------------------------
        os_por_svc = os_tipo.groupby("svc")["n"].sum() if not os_tipo.empty else pd.Series(dtype=float)
        for svc in sorted(set(os_por_svc.index) | linhas_dia):
            n_os = int(os_por_svc.get(svc, 0))
            if n_os > 0:
                if svc in linhas_dia:
                    classe = f"EM_{svc_id}"
                elif svc in linhas_excep:
                    classe = "SO_EXCEP_EVENTO"
                else:
                    classe = f"SEM_{svc_id}_NO_GTFS"
            else:
                classe = "SO_GTFS_NA_OS"
            vistas = sorted(set(os_tipo.loc[os_tipo["svc"] == svc, "vista"]) - {""})
            consorcios_os = sorted(set(os_tipo.loc[os_tipo["svc"] == svc, "consorcio"]) - {""})
            consorcio_final = " / ".join(consorcios_os) if consorcios_os else gtfs_consorcio.get(svc, "")
            registros_serv.append({
                "tipo_dia": tipo, "servico": svc, "consorcio": consorcio_final,
                "classificacao": classe,
                "partidas_os": n_os,
                "partidas_gtfs_reexp": int((dep["svc"] == svc).sum()) if not dep.empty else 0,
                "vista_os": " | ".join(vistas),
                "services_gtfs": ",".join(
                    sorted(trips.loc[trips["tss"] == svc, "service_id"].unique())),
            })

        ausentes = [r["servico"] for r in registros_serv
                    if r["tipo_dia"] == tipo and r["classificacao"] == f"SEM_{svc_id}_NO_GTFS"]
        so_excep = [r["servico"] for r in registros_serv
                    if r["tipo_dia"] == tipo and r["classificacao"] == "SO_EXCEP_EVENTO"]
        so_gtfs = [r["servico"] for r in registros_serv
                   if r["tipo_dia"] == tipo and r["classificacao"] == "SO_GTFS_NA_OS"]
        falhas_ausentes += len(ausentes)

        n_em = len({r["servico"] for r in registros_serv
                    if r["tipo_dia"] == tipo and r["classificacao"] == f"EM_{svc_id}"})
        linhas_res.append(f"Classificacao: EM_{svc_id}: {n_em}"
                          f" | SO_EXCEP_EVENTO: {len(so_excep)}"
                          f" | SEM_{svc_id}_NO_GTFS: {len(ausentes)}"
                          f" | SO_GTFS_NA_OS: {len(so_gtfs)}")
        if ausentes:
            linhas_res.append(f"  AUSENTES em {svc_id} (partidas OS sem cobertura):")
            for r in registros_serv:
                if r["tipo_dia"] == tipo and r["classificacao"] == f"SEM_{svc_id}_NO_GTFS":
                    cons_str = f" [{r['consorcio']}]" if r.get("consorcio") else ""
                    linhas_res.append(f"    {r['servico']:10s}{cons_str:15s} partidas_os={r['partidas_os']:4d}  "
                                      f"vista={r['vista_os']}")
        if so_excep:
            linhas_res.append(f"  So em EXCEP (evento, fora do quadro regular): "
                              f"{', '.join(so_excep[:30])}{' ...' if len(so_excep) > 30 else ''}")
        if so_gtfs:
            linhas_res.append(f"  No GTFS {svc_id} mas fora da OS: "
                              f"{', '.join(so_gtfs[:30])}{' ...' if len(so_gtfs) > 30 else ''}")

        # --- Células (serviço x sentido x faixa) ---------------------------
        bounds = [f for (t, f) in faixas_por_tipo if t == tipo]
        tem_total = any(t == tipo for (t, _) in totais_por_tipo)
        if not dep.empty:
            dep = dep.copy()
            dep["faixa"] = qual_faixa(dep["hora"].to_numpy(), bounds, tem_total)
            dep_f = dep[dep["faixa"] != ""]
            gtfs_f = dep_f.groupby(["svc", "dir", "faixa"]).size().reset_index(name="n_gtfs")
        else:
            gtfs_f = pd.DataFrame(columns=["svc", "dir", "faixa", "n_gtfs"])

        if not os_tipo.empty:
            os_f = (os_tipo.groupby(["svc", "dir", "faixa"])["n"].sum()
                    .reset_index().rename(columns={"n": "n_os"}))
        else:
            os_f = pd.DataFrame(columns=["svc", "dir", "faixa", "n_os"])

        m = os_f.merge(gtfs_f, on=["svc", "dir", "faixa"], how="outer").fillna(0)
        if m.empty:
            linhas_res.append("Sem celulas para comparar.")
            linhas_res.append("")
            continue
        m["n_os"] = m["n_os"].astype(int)
        m["n_gtfs"] = m["n_gtfs"].astype(int)
        m["dif"] = m["n_os"] - m["n_gtfs"]
        m["tipo_dia"] = tipo
        m["sentido"] = m["dir"].map(DIR_PAR_SENTIDO).fillna(m["dir"])
        registros_cel.append(m[["tipo_dia", "svc", "sentido", "faixa",
                                "n_os", "n_gtfs", "dif"]])

        fora = 0
        if not dep.empty and bounds:
            fora = int((dep["faixa"] == "").sum())
        cel_div = m[m["dif"].abs() > tolerancia]
        pos = cel_div[cel_div["dif"] > 0]
        neg = cel_div[cel_div["dif"] < 0]
        cel_sem = m[m["n_gtfs"] == 0]
        linhas_res.append(f"Celulas comparadas: {len(m)}"
                          f" | exatas: {len(m) - len(cel_div)}"
                          f" | divergentes (|dif| > {tolerancia}): {len(cel_div)}"
                          f" (OS>GTFS: {len(pos)} [{int(pos['dif'].sum()) if len(pos) else 0:+d}]"
                          f", GTFS>OS: {len(neg)} [{int(neg['dif'].sum()) if len(neg) else 0:+d}])")
        linhas_res.append(f"Celulas sem nenhuma partida do GTFS na mesma faixa: {len(cel_sem)}")
        if fora:
            linhas_res.append(f"Partidas do GTFS fora das faixas da OS: {fora}")
        linhas_res.append("")

    # --- Exportação ---------------------------------------------------------
    tab_serv = pd.DataFrame(registros_serv, columns=[
        "tipo_dia", "servico", "consorcio", "classificacao", "partidas_os",
        "partidas_gtfs_reexp", "vista_os", "services_gtfs"])
    tab_cel = (pd.concat(registros_cel, ignore_index=True)
               if registros_cel else pd.DataFrame(
                   columns=["tipo_dia", "svc", "sentido", "faixa", "n_os", "n_gtfs", "dif"]))
    tab_cel = tab_cel.rename(columns={"svc": "servico"})

    p_serv = pasta_saida / f"relatorio_{prefixo}_servicos.csv"
    p_cel = pasta_saida / f"relatorio_{prefixo}_celulas.csv"
    p_res = pasta_saida / f"resumo_{prefixo}.txt"
    tab_serv.to_csv(p_serv, index=False, encoding="utf-8-sig")
    tab_cel.to_csv(p_cel, index=False, encoding="utf-8-sig")

    resumo = "\n".join(linhas_res)
    p_res.write_text(resumo, encoding="utf-8")
    print("\n" + resumo)
    print("\nRelatórios gerados:")
    print(f"  {p_serv}")
    print(f"  {p_cel}")
    print(f"  {p_res}")

    if falhas_ausentes and cfg.get("fail_se_ausente", True):
        log_msg(f"ERRO: {falhas_ausentes} servicos da OS sem cobertura no GTFS "
                "(detalhe no resumo). Use fail_se_ausente=false p/ apenas informar.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
