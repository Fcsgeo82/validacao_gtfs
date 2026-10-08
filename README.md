# Validação GTFS

Dois validadores independentes do pipeline de GTFS do transporte público do Rio:

| Script | Etapa | O que faz |
|--------|-------|-----------|
| `0_validar_gtfs_entrada.py` | **antes** do pipeline | Valida a integridade do GTFS de entrada |
| `validar_gtfs.py` | **depois** do pipeline | Valida conformidade/integridade do GTFS final e cruza com a tabela de OS |

Os dois scripts aceitam `--config '<json>'` e mantêm fallback com variáveis hardcoded no topo do arquivo.

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

## 📊 `validar_gtfs.py`
**Objetivo:** Validação de conformidade e integridade pós-processamento, cruzando o **GTFS final (.zip)** com a **tabela de Ordem de Serviço (OS .csv)** gerada no planejamento.

### Localização

- Script: `validar_gtfs.py`
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
python validar_gtfs.py
```
O script detecta os arquivos automaticamente.

#### 2. Parâmetros explícitos via linha de comando:

```bash
python validar_gtfs.py --gtfs "0169_gtfs_out_26_1E_ret.zip" --os "tabela_programacao_OS.csv" --output-dir "." --estudo "2026-10-01Q"
```

#### 3. Via argumento `--config` (JSON inline):

```bash
python validar_gtfs.py --config '{"gtfs_path": "gtfs.zip", "os_path": "os.csv", "output_dir": ".", "estudo": "2026-10-01Q"}'
```

#### 4. Via arquivo de configuração JSON:

```bash
python validar_gtfs.py --config "config.json"
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
