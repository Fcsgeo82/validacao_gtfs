# Changelog — Projeto de Validação GTFS (`validacao/`)

Todas as alterações notáveis neste projeto de validação estão documentadas neste arquivo.

O formato é baseado no [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

---

## [3.1.0] - 2026-10-08

### Adicionado
- **Relatório PDF consolidado** `relatorio_consolidado_gtfs.pdf` (A4, `fpdf2`), gerado ao final de toda execução na pasta do script: identificação, sumário executivo com caixa de **PARECER GERAL** (`APROVADO` / `APROVADO COM RESSALVAS` / `NÃO APROVADO`), detalhamento das duas etapas, principais achados e conclusões — formato executivo para leitura da chefia direta.
- Persistência dos resultados de cada etapa em JSON (`resultados_entrada.json`, `resultados_saida.json`), permitindo regerar o PDF sem revalidar.
- Flags `--apenas-pdf` (gera somente o PDF a partir dos JSONs) e `--sem-pdf` (executa sem gerar o PDF).

### Modificado
- **`PASTA_RESULTADOS`** (etapa de entrada) agora tem como default a **pasta do script** — os CSVs detalhados deixaram de ser gravados em `C:\R_SMTR\resultados\validacoes_snapshot` (32 CSVs históricos movidos para a pasta do repositório).
- Auto-detecção da OS ignora também os `validacao_gtfs_*.csv` da pasta (não confundi-los com a tabela de OS).
- Detalhes numéricos dos checks de extensão (divergências stop_times × shapes e OS × GTFS) exibem `float` puro em CSV e PDF (antes apareciam como `np.float64(...)`).

---

## [3.0.0] - 2026-10-08

### Adicionado
- Fusão dos dois validadores em `validar_gtfs.py` com o argumento `--etapa entrada|saida|todas` (padrão: `saida`, preservando o comportamento de `python validar_gtfs.py` sem argumentos).
- CLI unificada: `--config` agora aceita **JSON inline ou caminho de arquivo** (estilo Streamlit do script 0 + estilo do `validar_gtfs.py`), além das flags `--gtfs`, `--os`, `--output-dir` e `--estudo`.
- Detecção automática de GTFS compartilhada pelas duas etapas: valida o conteúdo do ZIP (`routes.txt`/`trips.txt`/`stops.txt`), ignora ZIPs inválidos, ordena por data de modificação e lista os candidatos descartados. Busca em `pasta_gtfs` (novo, padrão: pasta do script) e `output_dir`.
- Extração de metadados do nome do GTFS (`rio_2026-10-01Q.zip` → operador/ano/mês/estudo) para nomear o relatório da etapa de entrada, respeitando valores explícitos da config.
- Novo arquivo `memory.md` com a memória técnica completa do projeto.

### Modificado
- `0_validar_gtfs_entrada.py` virou um wrapper fino (~20 linhas) que injeta `--etapa entrada` e chama o `main()` do `validar_gtfs.py`, mantendo o nome de invocação usado pelo pipeline `processar_gtfs`.
- Loader único do GTFS (`load_gtfs`): carrega todos os `.txt` do ZIP com chaves **com** sufixo `.txt`, preservando o tratamento de arquivos vazios (`EmptyDataError`) do script 0 e o trim de colunas do `validar_gtfs.py`.
- **Mudança de comportamento:** a etapa de saída agora retorna `exit 1` quando existem checks com `ERRO` (antes retornava sempre `0`). Avisos (`AVISO`) continuam não falhando.
- Resolução de caminhos padronizada: flag de CLI > chave no `--config` > auto-detecção > constante de fallback.
- README reescrito para o modelo de script único com etapas.

---

## [2.0.0] - 2026-10-06

### Adicionado
- `0_validar_gtfs_entrada.py` — validação do GTFS de entrada (pré-pipeline), migrado de `processar_gtfs/codigos_py/`.
- Seção do script 0 no `README.md`, junto com a documentação de `validar_gtfs.py`.

### Modificado
- Projeto promovido a repositório independente: os arquivos que viviam em `processar_gtfs/validacao/` foram movidos para a raiz deste projeto (`validar_gtfs.py`, `README.md`, dados de exemplo e relatórios).
- `validar_gtfs.py`: constantes de fallback `GTFS_PATH`, `OS_PATH` e `OUTPUT_DIR` agora apontam para `C:\github_repositories\validacao`.

---

## [1.2.0] - 2026-10-06

### Adicionado
- Regra de exceção para linhas com sufixo `TP` no check 3.2 (`EXCEP vinculado a trips com [evento] ou padrão TP`): viagens com `service_id == EXCEP` cujo `trip_short_name` siga o padrão de 4 dígitos seguidos de `TP` (regex `^\d{4}TP$`, ex: `2308TP`, `2110TP`, `2804TP`) agora são aceitas como válidas mesmo sem colchetes no `trip_headsign`.

### Modificado
- O check 3.2 agora discrimina no campo de detalhes quantas viagens pertencem a desvios/eventos com `[...]` e quantas correspondem ao padrão de linhas especiais `\d{4}TP`.

---

## [1.1.0] - 2026-10-06

### Adicionado
- Suporte a viagens regulares modeladas via grade horária fixa (*timetable / schedule-based*) no check 2.3: viagens com `service_id != EXCEP` que possuem horários programados diretamente em `stop_times.txt` (com `departure_time` válido) não são mais marcadas como erro quando ausentes em `frequencies.txt` (ex: linhas 899, SV899, 2110, 2383, 2613, 2614).
- Detalhamento informativo no check 2.3 indicando a contagem de viagens por frequências e por grade horária (*timetable*).
- Auto-descoberta inteligente de arquivos no diretório `validacao/`: ao executar `python validacao/validar_gtfs.py` sem argumentos, o script localiza automaticamente qualquer arquivo `.zip` (GTFS) e arquivos de OS (`tabela_programacao_OS.csv` ou outro `.csv` de dados).

### Modificado
- Check 2.3 renomeado para `Trips regulares com programação (frequencies ou timetable)`.
- Suporte a múltiplos formatos de tabela de OS no check 6 (Cabeçalho da OS) e check 7.4 (Extensão OS × Shapes): tanto o formato por sentido (`Serviço`, `Vista`, `Consórcio`, `Sentido`, `Extensão`) quanto o formato resumo por dia (`Extensão de Ida`, `Extensão de Volta`).
- Configuração de codificação segura no terminal Windows (`sys.stdout` em UTF-8 com substituição de caracteres incompatíveis e tags textuais `[OK]`, `[AVISO]`, `[ERRO]`).

---

## [1.0.0] - 2026-10-06

### Adicionado
- Criação inicial do script `validacao/validar_gtfs.py`:
  - **Categoria 1 (Padrão GTFS):** verificação de presença e colunas de todos os arquivos obrigatórios (`agency`, `feed_info`, `calendar`, `calendar_dates`, `routes`, `trips`, `stop_times`, `stops`, `shapes`, `frequencies`, `fare_attributes`, `fare_rules`), arquivos não vazios, coordenadas válidas de paradas e fusos horários consistentes.
  - **Categoria 2 (Integridade GTFS):** garantia de que toda viagem possui `stop_times`, rotas válidas e shapes válidos; verificação de registros órfãos em `stop_times` e rotas sem viagens.
  - **Categoria 3 (Calendário & EXCEP):** consistência de `service_id` com o calendário; regra inicial de correspondência biunívoca entre `service_id == EXCEP` e `trip_headsign` contendo `[...]`.
  - **Categoria 4 (Extensões):** coerência de distância percorrida entre `stop_times` e `shapes` (alerta para divergências > 5%) e detecção de viagens sem `shape_dist_traveled`.
  - **Categoria 5 (Shape IDs):** verificação estrita contra formatações em notação científica (proibição de caractere `+` em `shape_id`) e integridade referencial com `shapes.txt`.
  - **Categoria 6 (Cabeçalho OS):** conformidade das colunas obrigatórias da OS e ausência de linhas sem serviço.
  - **Categoria 7 (Cruzamento OS × GTFS):** identificação de linhas da OS ausentes no GTFS, linhas GTFS regulares ausentes na OS, cobertura de linhas excepcionais e comparação de extensão por sentido.
  - **Exportação:** relatório consolidado em formato CSV (`relatorio_validacao_{estudo}.csv`) formatado com separador `;` e decimais `,` (UTF-8 com BOM) para importação direta no Google Sheets ou Excel.
- Documentação inicial em `validacao/README.md`.
