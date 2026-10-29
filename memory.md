# memory.md — Memória técnica do projeto `validacao`

> Visão completa do repositório para orientar qualquer pessoa ou agente que for trabalhar aqui.
> Atualizado em: **2026-10-27** (versão 4.0.1).

---

## 1. O que é este projeto

Repositório independente (antes era `processar_gtfs/validacao/`) com os **validadores de GTFS** do transporte público do Rio de Janeiro. Cobre três itens do pipeline:

1. **Entrada (pré-pipeline)** — valida o GTFS original *antes* de `processar_gtfs` rodar (evita retrabalho).
2. **Saída (pós-pipeline)** — valida o GTFS final e **cruza com a tabela de OS** (Ordem de Serviço) gerada no planejamento.
3. **Conteúdo (pós-pipeline)** — compara o **conteúdo de partidas** da OS × GTFS para **todos os tipos de dia** (2_comparar_os_gtfs.py).

Desde a versão **3.0.0** as validações entrada/saída vivem no script `1_validar_gtfs.py` (antes `validar_gtfs.py`), selecionadas por `--etapa`. Desde a **3.1.0** toda execução também gera o **PDF executivo consolidado** `relatorio_consolidado_gtfs.pdf` para leitura da chefia. A **4.0.0** renomeou o script para a convenção numerada (`0_`, `1_`, `2_`) e adicionou o comparador de conteúdo.

---

## 2. Estrutura do repositório

| Arquivo | Papel |
|---------|-------|
| `1_validar_gtfs.py` | **Script único** com todas as validações estruturais (etapas entrada e saída); antes chamava-se `validar_gtfs.py` |
| `0_validar_gtfs_entrada.py` | **Wrapper de compatibilidade** — injeta `--etapa entrada` e chama o `main()` do `1_validar_gtfs.py` (via `importlib`, pois o nome começa com dígito); mantido porque o pipeline externo `processar_gtfs` o chama pelo nome |
| `2_comparar_os_gtfs.py` | **Comparador de conteúdo** OS × GTFS (partidas por faixa, todos os tipos de dia) — ver §7 |
| `requirements.txt` | `pandas>=2.0` e `fpdf2>=2.8` |
| `venv/` | Ambiente virtual Python 3.13 local (não versionar) |
| `README.md` | Documentação de uso (comandos, chaves de config, formatos de saída) |
| `CHANGELOG.md` | Histórico Keep a Changelog (1.0.0 → 4.0.0) |
| `memory.md` | Este arquivo |
| `gtfs_combi_2026-10-02Q_COMPLETO.zip` | GTFS de trabalho (auto-detectado; `GTFS.zip` tem prioridade se existir) |
| `tabela_programacao_OS.csv` | OS de exemplo (`sep=';'`, `decimal=','`, `utf-8-sig`; `OS.csv` tem prioridade se existir) |
| `relatorio_validacao_validacao_gtfs.csv` | Relatório gerado pela etapa de saída (gerável/regenerável) |
| `relatorio_comparar_os_gtfs_*.csv` / `resumo_comparar_os_gtfs.txt` | Saídas do script 2 (regeneráveis) |
| `resultados_entrada.json` / `resultados_saida.json` | Resultados por etapa salvos a cada execução (base do PDF) |
| `relatorio_consolidado_gtfs.pdf` | Relatório executivo consolidado (regenerado a cada execução) |
| `validacao_gtfs_entrada_*.csv` | CSVs detalhados da entrada (com timestamp; acumulam; 32 históricos vindos de `C:\R_SMTR` movidos para cá) |

---

## 3. Como executar

```bash
# Ativação do ambiente
.\venv\Scripts\Activate.ps1

# Padrão: etapa de SAÍDA com auto-detecção de GTFS e OS na pasta
python 1_validar_gtfs.py

# Etapas
python 1_validar_gtfs.py --etapa entrada
python 1_validar_gtfs.py --etapa saida
python 1_validar_gtfs.py --etapa todas

# Wrapper legado (idêntico a --etapa entrada)
python 0_validar_gtfs_entrada.py

# Arquivos explícitos / config
python 1_validar_gtfs.py --gtfs "x.zip" --os "y.csv" --output-dir "." --estudo "2026-10-01Q"
python 1_validar_gtfs.py --config '{"gtfs_path": "x.zip", "estudo": "rotulo"}'
python 0_validar_gtfs_entrada.py --config "config.json"   # Streamlit: arquivo JSON

# PDF consolidado
python 1_validar_gtfs.py --apenas-pdf   # só regenera o PDF a partir dos JSONs salvos
python 1_validar_gtfs.py --sem-pdf      # roda a etapa sem gerar o PDF

# Comparador de conteúdo OS x GTFS (todos os tipos de dia)
python 2_comparar_os_gtfs.py                                   # OS.csv/GTFS.zip + fallback
python 2_comparar_os_gtfs.py --os "os.csv" --gtfs "gtfs.zip" --prefixo "estudo"
python 2_comparar_os_gtfs.py --config '{"formula": "floor", "fail_se_ausente": false}'
```

**Exit codes:** `1_validar_gtfs.py`: `0` = OK; `1` = GTFS não encontrado, ou FALHA (entrada) / ERRO (saída). `AVISO` nunca falha. `2_comparar_os_gtfs.py`: `0` = OK; `1` = entradas ausentes ou serviços da OS sem cobertura no GTFS (`fail_se_ausente`).

---

## 4. Anatomia do `1_validar_gtfs.py`

Ordem das seções no arquivo:

1. **Constantes de fallback** — entrada (`BASE_DADOS`, `PASTA_RESULTADOS`, `ANO/MES/ESTUDO_GTFS`, `GTFS_PROCESSAR`) e saída (`GTFS_PATH`, `OS_PATH`, `OUTPUT_DIR`, `ESTUDO`).
2. **`Resultado` (dataclass)** — usado pelo modelo de checks da saída (`categoria`, `check`, `status`, `n_afetados`, `detalhes`).
3. **`COLUNAS_ESPERADAS`** — colunas obrigatórias por arquivo GTFS; `COLUNAS_OS_OBRIGATORIAS` / `FAIXAS_OS` (referência da OS).
4. **Infra compartilhada**:
   - `log_msg()` — log com timestamp `HH:MM:SS`.
   - `detectar_gtfs_na_pasta(pasta)` — acha `*.zip` que tenham `routes.txt`/`trips.txt`/`stops.txt`; ignora ZIP inválido; ordena do mais recente ao mais antigo.
   - `extrair_metadata_gtfs(nome)` — regex `^(operador)_(AAAA)-(MM)-(DD)Q` → `{gtfs_processar, ano_gtfs, mes_gtfs, estudo_gtfs}` (ex.: `rio_2026-10-01Q.zip`).
   - `parse_config()` — argparse: `--config` (JSON inline **ou** arquivo), `--etapa`, `--gtfs`, `--os`, `--output-dir`, `--estudo`, `--apenas-pdf`, `--sem-pdf`; monta o `cfg` completo e registra quais chaves foram explícitas em `cfg["_explicitos"]`.
   - `resolver_gtfs(cfg)` → `(Path, auto_detectado)`: explícito → auto-detecção (`pasta_gtfs` + `output_dir`) → fallback.
   - `resolver_os(cfg)` → explícito → auto-descoberta (`tabela_programacao_OS.csv` > `tabela_*.csv` > `*.csv` que não começa com `relatorio_` nem `validacao_gtfs_`).
   - `salvar_resultados_json()` / `carregar_resultados_json()` — persistência das etapas em `resultados_entrada.json` / `resultados_saida.json` (inclui `_texto`/`_json_default` para `Path`/`Timestamp`/`numpy`).
   - `load_gtfs(path)` — **todas** as `*.txt` do ZIP, chaves **com** sufixo (`.txt`), `dtype=str`, trim de colunas, `EmptyDataError` → DataFrame vazio.
   - `load_os(path)` — `sep=';'`, tenta `utf-8-sig` e cai para `latin-1`.
5. **Etapa entrada** — validações detalhadas (ver §5).
6. **Etapa saída** — categorias 1–7 (ver §6).
7. **Relatório PDF** — `_sanitizar_pdf`, `_truncar_pdf`, `_parecer`, `_cor_status`, `gerar_relatorio_pdf()` (ver §7).
8. **`main()`** → resolve config/GTFS, carrega uma vez, roda as etapas pedidas, gera o PDF (salvo `--sem-pdf`), soma falhas → exit code.

### Regras de precedência (vale para tudo)

**flag de CLI > chave no `--config` > auto-detecção > constante de fallback.**

A meta-extração do nome do arquivo (§ `extrair_metadata_gtfs`) só é aplicada na etapa de entrada e **apenas quando o GTFS foi auto-detectado**; chaves explícitas na config sempre vencem.

---

## 5. Etapa `entrada` — o que valida

| # | Check | Status |
|---|-------|--------|
| 1 | Toda `route` tem ≥ 1 trip (`route_id` em `trips.txt`) | `FALHA` por route |
| 2 | Texto entre `[...]` no `trip_headsign`: sem acentos, espaço → `_`, sem maiúsculas, só `[a-z0-9_]` | `FALHA` por trip + **sugestão de correção** |
| extra | Trips sem `route_id` | aviso no console |
| extra | Inventário de `service_id` (contagem por ID) | informativo |

- **Modelo do CSV** (11 colunas, vírgula, `utf-8-sig`): `validacao, status, route_id, route_short_name, route_long_name, route_type, trip_id, service_id, trip_headsign, problema, sugestao`.
- **Saída**: `PASTA_RESULTADOS/validacao_gtfs_entrada_<tipo>_<AAAAMMDD>Q_<timestamp>.csv` — snapshot detalhado **linha a linha** (serve de lista de trabalho para correção). `PASTA_RESULTADOS` default = **pasta do script** (3.1.0; antes era `C:\R_SMTR\resultados\validacoes_snapshot`).
- **Também salva** `resultados_entrada.json` (resumo, `por_validacao`, `pendencias`) para o PDF consolidado.
- Funções-chave: `validar_routes_com_trips`, `validar_trips_excepcionalidade`, `validar_padrao_excepcionalidade`, `gerar_sugestao_correcao`, `imprimir_resumo_consolidado`.

---

## 6. Etapa `saída` — o que valida

| Cat. | Tema | Observação |
|------|------|-----------|
| 1 | Padrão GTFS | arquivos/colunas obrigatórias, arquivos vazios, lat/lon de stops, `agency_timezone` |
| 2 | Integridade | trips sem stop_times, órfãos, programação (frequencies **ou** timetable em `departure_time`), route/shape inválidos, routes sem trips (`AVISO`) |
| 3 | Calendário e EXCEP | `service_id` no calendário; EXCEP precisa de `[evento]` ou padrão `\d{4}TP`; inverso: `[evento]` precisa ser EXCEP |
| 4 | Extensões | `max(shape_dist_traveled)` em stop_times vs shapes (tolerância 5%); trips sem `shape_dist_traveled` |
| 5 | Shape IDs | proíbe `+` (notação científica); referência trips → shapes |
| 6 | Cabeçalho OS | detecta formato "por Sentido" ou "Resumo por Dia"; linhas sem Serviço |
| 7 | OS × GTFS | linhas da OS ausentes no GTFS (`ERRO`), regulares ausentes na OS (`AVISO`), EXCEP sem OS (`AVISO`), extensões por sentido > 5% (`AVISO`) |

- **Modelo do CSV** (5 colunas, `;` e decimal `,`, `utf-8-sig` — Google Sheets): `categoria, check, status, n_afetados, detalhes`; `status ∈ {OK, AVISO, ERRO}`.
- **Saída**: `output_dir/relatorio_validacao_<estudo>.csv` (sobrescreve — sem timestamp, é o artefato versionado). **Também salva** `resultados_saida.json` (resumo + `checks`) para o PDF consolidado.
- Funções: `check_arquivos_gtfs`, `check_integridade`, `check_calendario`, `check_extensoes`, `check_shape_ids`, `check_cabecalho_os`, `check_os_vs_gtfs` → `consolidar` → `exportar_csv`.

---

## 7. Comparador de conteúdo OS × GTFS (4.0.0, `2_comparar_os_gtfs.py`)

- **Objetivo**: verificar se o **conteúdo de partidas** da OS bate com o GTFS, para **todos os tipos de dia** (Dia Útil→`U_REG`, Sábado→`S_REG`, Domingo→`D_REG`, Ponto Facultativo→`F_REG`; mapa em `mapa_dia_servico`). Diferente do check 7 do script 1 (presença de linha), aqui compara **quantidade de partidas por faixa horária**.
- **Entradas sem hardcode**: default `OS.csv`/`GTFS.zip` na raiz; fallback de auto-detecção idêntico ao do script 1 (`tabela_programacao_OS.csv` > `tabela_*.csv` > outros `.csv` que não sejam relatórios; zips válidos). Sep (`;`/`,`), encoding (`utf-8-sig`/`latin-1`) e formato de coluna auto-detectados — os três formatos de OS: `partidas 00:00-01:00 - Dia Útil`, `Partidas 00h à 01h - Dia Útil` e `Partidas Ida - Dia Útil` (Resumo por Dia → comparação por total, faixa `total`).
- **Reexpansão**: `frequencies.txt` com convenção `floor` (`n = (end-start)//headway`, partidas em `[start, end)`, `% 86400`), alinhada ao `7_lista_partidas.py` do `processar_gtfs`; alternativas `ceil`/`floor_mais_1` via `formula`. Viagens **timetable** (sem frequencies) entram pela primeira saída em `stop_times.txt` (`stop_sequence` mínimo). Importante: no 0174 as trips sem frequencies são só `EXCEP`(740), `U_REG`(69) e `F_REG`(7) — o timetable cobre as últimas duas.
- **Classificação por serviço×dia**: `EM_<ID>` / `SO_EXCEP_EVENTO` / `SEM_<ID>_NO_GTFS` (falta — causa `exit 1`, desligável com `fail_se_ausente=false`) / `SO_GTFS_NA_OS` (informativo).
- **Células**: (tipo_dia, serviço, sentido, faixa) com `n_os`, `n_gtfs`, `dif`; horas fora das faixas da OS são contadas como "fora de faixa" no resumo. `tolerancia` define divergência (padrão 0); divergência é AVISO — não falha.
- **Saídas**: `relatorio_<prefixo>_servicos.csv`, `relatorio_<prefixo>_celulas.csv` (ambos `utf-8-sig`), `resumo_<prefixo>.txt`; prefixo padrão `comparar_os_gtfs`.
- **Validação**: reproduziu a investigação OS 191 × GTFS 0174 (Domingo: 364 serviços, todos `EM_D_REG`; 269 células OS>GTFS +270; 5.304 exatas; 25 sem banda — idêntico ao `codigos_py/investigar_os190_vs_gtfs0174.py` do `processar_gtfs`).

---

## 8. Relatório PDF consolidado (3.1.0)

- **Arquivo**: `relatorio_consolidado_gtfs.pdf` (A4, pasta do script, gerado com `fpdf2` — fonte Arial do Windows com fallback Helvetica; texto sanitizado p/ Latin-1; largura útil 180mm).
- **Fonte de dados**: `resultados_entrada.json` + `resultados_saida.json` (cada etapa salva o próprio JSON ao rodar; se um dos dois não existe, a seção correspondente vira placeholder "não executada").
- **Estrutura**: (1) identificação (GTFS, horários das etapas, emissão); (2) sumário executivo com caixa de **PARECER GERAL** — `APROVADO` (sem falhas/erros/avisos), `APROVADO COM RESSALVAS` (só avisos), `NÃO APROVADO` (falha/erro); (3) entrada (tabela `por_validacao` + pendências); (4) saída (tabela de todos os checks + principais achados); (5) conclusões/recomendações.
- **Flags**: padrão gera ao final de `main()`; `--apenas-pdf` pula validação e só regenera o PDF; `--sem-pdf` valida e não gera.
- Páginas têm cabeçalho com título, rodapé `Página N/M` e numeração; cores por status (verde/âmbar/vermelho).

---

## 9. Sobreposições propositalmente mantidas

- **"Routes sem trips"** existe nas duas etapas: entrada gera **linha detalhada por route** (snapshot acionável); saída gera **um check resumido** com contagem. Granularidades diferentes, consumidores diferentes — não unificar.
- **EXCEP/`[evento]`**: entrada valida o *formato do texto* entre colchetes; saída valida o *vínculo* EXCEP ↔ `[evento]`/padrão TP. Complementares.
- Os dois formatos de CSV **não devem ser fundidos** (decisão registrada na 3.0.0).

---

## 10. Decisões e histórico (resumo)

| Versão | Mudança |
|--------|---------|
| 1.0.0 | Criação do `validar_gtfs.py` (pós-pipeline, 7 categorias, CSV Google Sheets) |
| 1.1.0 | Timetable em stop_times no check 2.3; auto-descoberta de arquivos; encoding UTF-8 no Windows |
| 1.2.0 | Regra de exceção `\d{4}TP` no check 3.2 |
| 2.0.0 | `0_validar_gtfs_entrada.py` migrado de `processar_gtfs/`; repo independente |
| 3.0.0 | **Fusão em script único** com `--etapa`; wrapper fino; detecção de GTFS com validação de conteúdo; exit 1 na saída com `ERRO`; `memory.md` |
| 3.1.0 | **PDF executivo consolidado** + JSONs intermediários; flags `--apenas-pdf`/`--sem-pdf`; `PASTA_RESULTADOS` default = pasta do script |
| 4.0.0 | **Renomeio** `validar_gtfs.py` → `1_validar_gtfs.py` (convenção `0_`/`1_`/`2_`); wrapper corrigido via `importlib`; **novo `2_comparar_os_gtfs.py`** (conteúdo OS × GTFS, todos os tipos de dia, `OS.csv`/`GTFS.zip`) |
| 4.0.1 | Correção no `2_comparar_os_gtfs.py` (`NameError` na linha 553 ao testar `tem_total`), inclusão do campo `consorcio` no relatório de serviços e correção da fórmula padrão de reexpansão de `frequencies` para `"ceil"` (equivalente exato a `while t < end`) |

Decisões-chave da 3.0.0 (usuário aprovou explicitamente):
1. Script único com `--etapa` (não um módulo compartilhado + 2 scripts).
2. `0_validar_gtfs_entrada.py` mantido como wrapper (compatibilidade com o pipeline `processar_gtfs`).
3. Dois CSVs com formatos atuais preservados.
4. Padrão de `--etapa` = `saida` (mantém compat com `python validar_gtfs.py` sem args).

---

## 11. Contexto externo

- **Pipeline `processar_gtfs`** (repositório separado): chama `0_validar_gtfs_entrada.py` **antes** de `2_ajustar_stop_times.py`. Não renomear/remover o wrapper sem atualizar o pipeline.
- **Streamlit**: injeta config via `--config <arquivo.json>` — por isso `--config` precisa continuar aceitando caminho de arquivo (e também JSON inline).
- **Base de dados**: `C:/R_SMTR/dados` (`gtfs/<ano>/<tipo>_<AAAA>-<MM>-<DD>Q.zip`). Relatórios agora ficam na **pasta do repositório** (os 32 CSVs históricos de `C:/R_SMTR/resultados/validacoes_snapshot` foram movidos para cá em 2026-10-08).
- **OS**: gerada pelo planejamento; aqui `tabela_programacao_OS.csv` (e `OS.csv` como nome-padrão esperado pelo script 2). Formatos suportados: "por Sentido" (com faixas `00:00-01:00` ou `00h à 01h`) e "Resumo por Dia".
- **Google Sheets**: a etapa de saída é importada manualmente (`;` + decimal `,` + BOM) — não mudar o formato.

---

## 12. Convenções do código

- PT-BR nos textos/README; mensagens de log da etapa de entrada sem acento (herança); checks da saída com acento.
- `pandas` com `dtype=str` em tudo (nenhuma conversão implícita); conversões numéricas explícitas (`pd.to_numeric(..., errors="coerce")`).
- Chaves do dict do GTFS **sempre com `.txt`** (`gtfs["trips.txt"]`).
- Sem testes automatizados no repo — a verificação é executar os comandos do §3 com o GTFS da pasta e conferir CSV + exit code.
- `sys.stdout`/`sys.stderr` reconfigurados para UTF-8 no import (Windows).
- Encoding dos CSVs: `utf-8-sig` (BOM) sempre.

---

## 13. Ambiente e comandos de verificação

```bash
# Criar/atualizar venv + dependências
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# Smoke test das duas etapas (usa o GTFS/OS da pasta)
.\venv\Scripts\python.exe 1_validar_gtfs.py --etapa entrada   # exit 0/1 conforme dados
.\venv\Scripts\python.exe 1_validar_gtfs.py --etapa saida     # relatorio_validacao_*.csv atualizado
.\venv\Scripts\python.exe 0_validar_gtfs_entrada.py           # wrapper ≡ --etapa entrada

# PDF consolidado
.\venv\Scripts\python.exe 1_validar_gtfs.py --apenas-pdf      # regenera só o PDF
.\venv\Scripts\python.exe 1_validar_gtfs.py --etapa entrada --sem-pdf   # não gera PDF

# Comparador de conteúdo (2_comparar_os_gtfs.py)
.\venv\Scripts\python.exe 2_comparar_os_gtfs.py               # exit 0; resumo_comparar_os_gtfs.txt

# Checagem rápida de sintaxe
.\venv\Scripts\python.exe -m py_compile 1_validar_gtfs.py 0_validar_gtfs_entrada.py 2_comparar_os_gtfs.py
```

Python usado: 3.13.4 (`C:\Program Files\Python313\python.exe`); pandas 3.x instalado no venv.

---

## 14. Armadilhas conhecidas

- **Auto-detecção pega o ZIP mais recente** — se houver vários `.zip` na pasta, o mais novo vence (a lista de candidatos é impressa). Use `endereco_gtfs`/`--gtfs` para tirar a dúvida.
- **Auto-detecção exige conteúdo de GTFS** (`routes.txt`/`trips.txt`/`stops.txt`) — ZIPs comum não é considerado GTFS.
- **`relatorio_validacao_<estudo>.csv` é sobrescrito** a cada execução da saída (sem timestamp); os CSVs da entrada têm timestamp e acumulam.
- **Exit 1 na saída é novo (3.0.0)** — scripts que tratassem o exit code do `1_validar_gtfs.py` antes vão passar a vê-lo falhar quando houver `ERRO`.
- **Nome de módulo com dígito**: `1_validar_gtfs.py` não pode ser importado com `from ... import` normal — o wrapper `0_` usa `importlib.import_module("1_validar_gtfs")`.
- A etapa de entrada **não falha** por `AVISO` (ex.: trips sem `route_id`); só `FALHA`.
- `--etapa todas` roda entrada e saída sobre o **mesmo** GTFS — normalmente entrada roda no ZIP de entrada e saída no ZIP de saída, em momentos diferentes do pipeline.
- **`relatorio_consolidado_gtfs.pdf` é sobrescrito** a cada geração; se só uma etapa rodou, a outra aparece como "não executada (sem dados salvos)" no PDF — rode as duas (ou `--etapa todas`) antes de distribuir o relatório.
- **CSVs da entrada acumulam** (timestamp no nome) na pasta do script; podem ser movidos/limpos livremente — o PDF não depende deles, só dos JSONs.
- **`2_comparar_os_gtfs.py` falha (`exit 1`) com serviços da OS ausentes no GTFS** por design — para rodada exploratória use `--config '{"fail_se_ausente": false}'`. O default `OS.csv`/`GTFS.zip` é só o *nome esperado na raiz*; se não existirem, cai na auto-detecção (uma OS antiga na pasta pode ser escolhida — confira o cabeçalho impresso).
- **`--config` JSON inline pelo PowerShell** perde as aspas duplas na passagem de argumento; use arquivo `.json` ou aspas simples com escapes `\"` nesse caso.
