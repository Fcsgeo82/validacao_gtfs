# Validação GTFS

Itens numerados do pipeline de validação do GTFS do transporte público do Rio:

| Script | Etapa | O que faz |
|--------|-------|-----------|
| `0_validar_gtfs_entrada.py` | **antes** do pipeline | Wrapper de compatibilidade: valida a integridade do GTFS de entrada |
| `1_validar_gtfs.py` | **depois** do pipeline | Valida conformidade/integridade do GTFS final e cruza linhas com a OS |
| `2_comparar_os_gtfs.py` | **depois** do pipeline | Compara o **conteúdo de partidas** da OS × GTFS (todos os tipos de dia) |

Os scripts aceitam `--config '<json>'` e mantêm fallback com variáveis hardcoded no topo do arquivo.

---

## Instalacao

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 0️⃣ `0_validar_gtfs_entrada.py`
**Objetivo:** Validar a integridade do GTFS de entrada **antes** de iniciar o pipeline de processamento, detectando problemas antecipadamente.

| Item | Descrição |
|------|-----------|
| **Entrada** | Arquivo GTFS ZIP original (SPPO, BRT ou Rio) — tabelas `routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt`, `calendar_dates.txt`, `shapes.txt` |
| **Parâmetros** | `BASE_DADOS` (default `C:/R_SMTR/dados`), `PASTA_RESULTADOS` (default `C:/R_SMTR/resultados/validacoes_snapshot`), `ano_gtfs`, `mes_gtfs`, `estudo_gtfs`, `gtfs_processar` (`sppo`, `brt` ou `rio`), `endereco_gtfs` |
| **Validações** | 1. Todas as routes possuem pelo menos uma trip associada |
|  | 2. Trips de excepcionalidade (com `[...]` no `trip_headsign`) seguem o padrão: sem acentos, espaços substituídos por `_`, apenas minúsculas e caracteres `[a-z0-9_]` |
|  | 3. (Extra) Trips sem `route_id` preenchido |
|  | 4. (Extra) Inventário de `service_id` utilizados |
| **Saída** | Relatório CSV com timestamp em `PASTA_RESULTADOS/validacao_gtfs_entrada_<tipo>_<sufixo>_<timestamp>.csv` |
| **Retorno** | `sys.exit(0)` se todas as validações críticas passaram; `sys.exit(1)` se houver falhas |
| **Dependências** | `pandas`, `zipfile`, `re`, `unicodedata`, `pathlib` |

```bash
python 0_validar_gtfs_entrada.py --config '{"BASE_DADOS": "C:/R_SMTR/dados", "gtfs_processar": "sppo", "ano_gtfs": "2027", "mes_gtfs": "08", "estudo_gtfs": "04"}'
```

> [!TIP]
> Execute este script antes do `2_ajustar_stop_times.py` do projeto `processar_gtfs`.
> Ele gera sugestões automáticas de correção para os `trip_headsign` fora do padrão.

---

## 📊 `1_validar_gtfs.py`
**Objetivo:** Validação de conformidade e integridade pós-processamento, cruzando o **GTFS final (.zip)** com a **tabela de Ordem de Serviço (OS .csv)** gerada no planejamento.

### Localização

- Script: `1_validar_gtfs.py` (antes `validar_gtfs.py`)
- Pasta de execução: raiz deste projeto

### O que é verificado

1. **Padrão dos Arquivos GTFS**:
Presença de todos os arquivos obrigatórios (agency, feed_info, calendar, calendar_dates, routes, trips, stop_times, stops, shapes, frequencies, fare_attributes, fare_rules), colunas obrigatórias, arquivos vazios, coordenadas de paradas e consistência de fusos horários.

   - Presença de todos os arquivos obrigatórios (`agency`, `feed_info`, `calendar`, `calendar_dates`, `routes`, `trips`, `stop_times`, `stops`, `shapes`, `frequencies`, `fare_attributes`, `fare_rules`).
   - Verificação de colunas obrigatórias em cada arquivo.
   - Arquivos não vazios e coordenadas geográficas válidas (lat/lon de stops não zeradas/nulas).
   - Consistência de fuso horário em `agency_timezone`.

2. **Integridade Interna do GTFS**:
Garante que toda trip possui registros em stop_times.txt, frequencies.txt, routes.txt e shapes.txt; detecta órfãos em stop_times e rotas sem viagens.

   - Toda trip tem registros em `stop_times.txt`.
   - Inexistência de registros órfãos em `stop_times.txt`.
   - Toda trip regular (exceto `service_id == 'EXCEP'`) possui programação horária definida via `frequencies.txt` ou diretamente como grade horária fixa (*timetable / schedule*) em `stop_times.txt`.
   - Toda trip referencia um `route_id` válido em `routes.txt`.
   - Toda trip referencia um `shape_id` válido em `shapes.txt`.
   - Identificação de rotas sem trips associadas.

3. **Calendário e Itinerários Alternativos (EXCEP)**:
Valida se todos os service_id constam no calendário e assegura que viagens com serviço EXCEP estejam vinculadas a itinerários de evento/desvio (com [...] no letreiro) ou a linhas de serviço especial no padrão TP (ex: 2308TP, 4 dígitos seguidos de TP).

   - Todo `service_id` em trips deve estar definido em `calendar.txt` ou `calendar_dates.txt`.
   - `service_id == 'EXCEP'` deve estar associado a trips com colchetes `[...]` no letreiro ou linhas especiais no padrão `\d{4}TP` (ex: `2308TP`).
   - Trips com `[...]` no headsign devem possuir `service_id == 'EXCEP'`.

4. **Extensões de Trajeto**:
Compara a extensão máxima percorrida em stop_times com shapes (alerta para divergência > 5%) e identifica trips sem shape_dist_traveled.

   - Coerência de extensão: comparação de `shape_dist_traveled` máximo em `stop_times` vs `shapes` (tolerância de até 5%).
   - Identificação de trips sem `shape_dist_traveled` preenchido em `stop_times`.

5. **Validação de Shape IDs**:
Detecta anomalias de formatação nos identificadores (proíbe caractere +, prevenindo exportações acidentais em notação científica) e verifica se todos os shapes das viagens existem em shapes.txt.

   - Shape IDs não podem conter `+` (ex: formatação acidental de número científico como `1.04E+05`).
   - Shape IDs referenciados em `trips.txt` devem existir em `shapes.txt`.

6. **Padrão de Cabeçalho da OS**:
Valida o padrão do cabeçalho da OS, suportando tanto o formato por sentido (Serviço, Vista, Consórcio, Sentido, Extensão...) quanto o formato resumo por dia (Extensão de Ida, Extensão de Volta...).

   - Suporte aos padrões da OS:
     - Formato por Sentido: `Serviço`, `Vista`, `Consórcio`, `Sentido`, `Extensão`, faixas...
     - Formato Resumo por Dia: `Serviço`, `Vista`, `Consórcio`, `Extensão de Ida`, `Extensão de Volta`, faixas...
   - Verificação de linhas sem código de serviço.

7. **Cruzamento OS × GTFS**:
Identifica linhas da OS ausentes no GTFS, linhas regulares do GTFS ausentes na OS, cobertura das linhas de itinerários alternativos (EXCEP) e compara as extensões por sentido (OS vs Shapes).

   - Linhas da OS ausentes no GTFS (`ERRO`).
   - Linhas regulares do GTFS ausentes na OS (`AVISO`).
   - Linhas de itinerários alternativos (EXCEP) no GTFS sem correspondência na OS (`AVISO`).
   - Comparação da extensão entre a OS e o GTFS (Shapes) por sentido (alerta para diferenças > 5%).

### Como Executar

#### 1. Execução direta (Detecção automática):
Se você colocar o GTFS (`.zip`) e a OS (`tabela_programacao_OS.csv` ou outro `.csv`) na raiz deste projeto, basta rodar:

```bash
python 1_validar_gtfs.py
```
O script detecta os arquivos automaticamente.

#### 2. Parâmetros explícitos via linha de comando:

```bash
python 1_validar_gtfs.py --gtfs "0169_gtfs_out_26_1E_ret.zip" --os "tabela_programacao_OS.csv" --output-dir "." --estudo "2026-10-01Q"
```

#### 3. Via argumento `--config` (JSON inline):

```bash
python 1_validar_gtfs.py --config '{"gtfs_path": "gtfs.zip", "os_path": "os.csv", "output_dir": ".", "estudo": "2026-10-01Q"}'
```

#### 4. Via arquivo de configuração JSON:

```bash
python 1_validar_gtfs.py --config "config.json"
```

### Formato de Saída

O script gera um arquivo CSV formatado para importação direta no **Google Sheets** (separador `;`, decimais com vírgula `,`, codificação `UTF-8 com BOM`):

- **Nome do arquivo**: `relatorio_validacao_{estudo}.csv`
- **Colunas**:
  - `categoria`: área avaliada (ex: `1. Padrão GTFS`, `2. Integridade GTFS`, ...)
  - `check`: descrição do item testado
  - `status`: `OK`, `AVISO` ou `ERRO`
  - `n_afetados`: quantidade de registros/linhas impactadas
  - `detalhes`: relação de IDs ou exemplos dos registros divergentes

---

## 🔍 `2_comparar_os_gtfs.py`
**Objetivo:** Verificar se o **conteúdo de partidas** da OS bate com o GTFS — para **todos os tipos de dia**, não só a presença da linha:

| Tipo de dia na OS | `service_id` no GTFS |
|-------------------|----------------------|
| Dia Útil | `U_REG` |
| Sábado | `S_REG` |
| Domingo | `D_REG` |
| Ponto Facultativo | `F_REG` |

O mapeamento é configurável via `mapa_dia_servico` no `--config`.

### Como funciona

1. Reexpande as partidas do GTFS por `service_id`: `frequencies.txt` com a convenção `ceil` alinhada a `while t < end` do `7_lista_partidas.py` do `processar_gtfs` (partidas em `[start, end)`, exclui end alinhado) + viagens timetable (sem frequencies) pela primeira saída em `stop_times.txt`.
2. Classifica cada serviço da OS: `EM_<ID>` (presente no quadro do dia), `SO_EXCEP_EVENTO` (só em `EXCEP`), `SEM_<ID>_NO_GTFS` (com partidas na OS, ausente no GTFS) e `SO_GTFS_NA_OS` (no GTFS, fora da OS).
3. Compara célula a célula (serviço × sentido × faixa horária): `n_os`, `n_gtfs`, `dif`.

### Entradas (sem hardcode)

- **Padrão**: `OS.csv` e `GTFS.zip` na raiz do projeto.
- **Fallback de auto-detecção**: `tabela_programacao_OS.csv` > `tabela_*.csv` > outros `.csv` (ignora relatórios); zips válidos (`routes/trips/stops`), mais recente primeiro.
- Separação (`;`/`,`), encoding (`utf-8-sig`/`latin-1`) e formato de coluna da OS são auto-detectados: `partidas 00:00-01:00 - Dia Útil`, `Partidas 00h à 01h - Dia Útil` e `Partidas Ida - Dia Útil` (Resumo por Dia, comparado por total).

### Como Executar

```bash
# Auto-detecção (OS.csv/GTFS.zip ou fallback na raiz)
python 2_comparar_os_gtfs.py

# Arquivos explícitos
python 2_comparar_os_gtfs.py --os "os.csv" --gtfs "gtfs.zip" --output-dir "." --prefixo "meu_estudo"

# Config (JSON inline ou arquivo)
python 2_comparar_os_gtfs.py --config '{"os_path": "os.csv", "gtfs_path": "gtfs.zip", "formula": "floor", "tolerancia": 0}'
```

Chaves do `--config`: `os_path`, `gtfs_path`, `output_dir`, `prefixo`, `formula` (`floor`|`floor_mais_1`|`ceil`), `tolerancia` (partidas por célula), `encoding_os`, `fail_se_ausente` (padrão `true`), `mapa_dia_servico`.

### Formato de Saída

- `relatorio_<prefixo>_servicos.csv` — `tipo_dia, servico, consorcio, classificacao, partidas_os, partidas_gtfs_reexp, vista_os, services_gtfs`
- `relatorio_<prefixo>_celulas.csv` — `tipo_dia, servico, sentido, faixa, n_os, n_gtfs, dif`
- `resumo_<prefixo>.txt` — resumo legível por tipo de dia
- **Exit code**: `0` = OK; `1` = entradas ausentes ou serviços da OS sem cobertura no GTFS (informativo com `fail_se_ausente=false`). Divergências de célula são AVISO e não falham.
