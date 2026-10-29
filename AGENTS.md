# Diretrizes para Agentes de IA — Projeto de Validação GTFS (`validacao_gtfs`)

Este documento orienta os agentes que atuam neste repositório sobre convenções, fluxo de trabalho e regras operacionais.

---

## 1. Visão Geral do Projeto

Repositório com os scripts de validação e comparação de dados GTFS e Ordem de Serviço (OS) do transporte público:
- [0_validar_gtfs_entrada.py](file:///c:/github_repositories/validacao_gtfs/0_validar_gtfs_entrada.py): Wrapper de compatibilidade para etapa de entrada (`--etapa entrada`).
- [1_validar_gtfs.py](file:///c:/github_repositories/validacao_gtfs/1_validar_gtfs.py): Validador estrutural principal (etapas de entrada, saída ou todas).
- [2_comparar_os_gtfs.py](file:///c:/github_repositories/validacao_gtfs/2_comparar_os_gtfs.py): Comparador de conteúdo das partidas (OS × GTFS reexpandido por frequencies/timetable para todos os tipos de dia).

---

## 2. Regras e Boas Práticas

1. **Alterações Mínimas e Precisas**: Faça modificações pontuais, mantendo a consistência estilística e arquitetural pré-existente.
2. **Preservação de Comentários**: Não remova comentários explicativos ou docstrings existentes.
3. **Links Clicáveis**: Utilize links markdown com esquema `file://` para arquivos e símbolos referenciados.
4. **Sincronização de Documentação**: Sempre após cada alteração de código validada, atualizar:
   - [CHANGELOG.md](file:///c:/github_repositories/validacao_gtfs/CHANGELOG.md)
   - [memory.md](file:///c:/github_repositories/validacao_gtfs/memory.md)
   - [AGENTS.md](file:///c:/github_repositories/validacao_gtfs/AGENTS.md)
   - [.gitignore](file:///c:/github_repositories/validacao_gtfs/.gitignore) (se novas extensões ou arquivos gerados forem criados).

---

## 3. Comandos Úteis de Execução e Teste

```powershell
# Execução da comparação OS x GTFS
python 2_comparar_os_gtfs.py

# Validação estrutural do GTFS
python 1_validar_gtfs.py --etapa saida
```
