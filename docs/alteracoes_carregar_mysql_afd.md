# Alterações no `carregar_mysql.py` para a família "Adequação da formação docente"

Status: **pendente** (a carga foi adiada de propósito). Este documento descreve o que mudar quando chegar a hora, sem alterar nada agora.

## 1. Pré-requisitos

1. Os 3 geradores rodaram e deixaram estes CSVs em `C:\dados_inep\data_ind\processados\adequacao-da-formacao-docente\`:

   | Gerador | CSVs |
   |---|---|
   | `gerar_afd_uf.py` | `cadastro_uf.csv` (588), `etapa.csv` (7), `percentual_docentes.csv` (20.566) |
   | `gerar_afd_escola.py` | `escola.csv` (878.665), `percentual_docentes_escola.csv` (6.150.655) |
   | `gerar_afd_municipio.py` | `cadastro_municipio.csv` (67.934), `percentual_docentes_municipio.csv` (2.349.578) |

2. O DDL `ddl adequacao_formacao_docente.sql` (7 tabelas) rodou no Workbench. O carregador não cria tabelas: se faltarem, a conferência para sem alterar o banco.
3. `SET GLOBAL local_infile = 1;` ativo (já exigido pelo carregador).

## 2. Mudanças no código

### 2.1 `Bloco`: raiz própria (opcional)

Os CSVs de indicadores ficam em `data_ind\processados`, fora da raiz padrão (`data_cen\dados_processados`).

```python
@dataclass(frozen=True)
class Bloco:
    nome: str
    banco: str
    pasta: str
    tabelas: tuple[str, ...]
    opcionais: frozenset[str] = frozenset()
    upsert: bool = False
    raiz: Path | None = None        # NOVO: raiz própria do bloco; None = usa --raiz
```

### 2.2 `BLOCOS`: bloco novo (depois do `0725`)

A ordem de `tabelas` é a ordem de carga: pais antes dos filhos.

```python
    Bloco("afd", "adequacao_formacao_docente", "adequacao-da-formacao-docente",
          ("etapa",
           "cadastro_uf", "percentual_docentes",
           "escola", "percentual_docentes_escola",
           "cadastro_municipio", "percentual_docentes_municipio"),
          raiz=Path("C:/dados_inep/data_ind/processados")),
```

O nome do bloco é `afd` (a família), e não `indicadores`: cada família terá o seu bloco e o seu database.

### 2.3 `conferir()`: usar a raiz do bloco

```python
        p = Plano(bloco, tabela, (bloco.raiz or raiz) / bloco.pasta / f"{tabela}.csv")
```

### 2.4 Mensagem final e docstring

- Acrescentar `'adequacao_formacao_docente'` na lista `table_schema IN (...)` da mensagem final.
- Acrescentar no docstring do topo a linha de entrada do bloco `afd` e um exemplo `--blocos afd`.

## 3. O que não precisa mudar (conferido no código atual)

- **Ordem de esvaziar:** `esvaziar` trunca de trás para frente, então os filhos saem antes dos pais, com `FOREIGN_KEY_CHECKS=0` só durante o TRUNCATE.
- **NULL:** campo vazio no CSV vira NULL via `NULLIF(@cN, '')`. Os geradores escrevem vazio onde o INEP tem `--`.
- **Fim de linha e encoding:** os CSVs saem do módulo `csv` (UTF-8 sem BOM, `\r\n`). `fim_de_linha()` detecta o `\r\n`, e o cabeçalho é lido com `utf-8-sig`.
- **Nomes de coluna:** a conferência compara em minúsculas (`CAT_1` no CSV = `CAT_1` no DDL; `co_uf` vazio para Brasil vira NULL).
- **Verificação antes do COMMIT:** avisos do MySQL e contagem de linhas arquivo × tabela já derrubam a carga em qualquer diferença.

## 4. Decisões a tomar

| # | Decisão | Sugestão |
|---|---|---|
| 1 | O bloco `afd` entra em `--blocos todos`? Se o DDL não rodou, a conferência para sem alterar nada. | Entrar. O comportamento já é seguro. |
| 2 | FK entre databases (`escola` → `b0725.escola`, `cadastro_municipio` → `dim.municipio`)? | Não criar FK. Validar por consulta (seção 6) e decidir depois. |
| 3 | `5101837` (município criado em 2025) pode não existir em `dim.municipio`. | Conferir antes (seção 5). Se faltar, atualizar a dimensão. |
| 4 | Volume: `percentual_docentes_escola` tem 6,1 milhões de linhas numa única transação. | Aceitar. Se o undo log pesar, dividir o LOAD por ano. |

## 5. Antes de carregar

```sql
-- o município novo existe na dimensão?
SELECT * FROM dim.municipio WHERE co_municipio = 5101837;
```

Conferir também os nomes reais das colunas de ano e chave em `b0725.escola` e `dim.estado` (as consultas abaixo assumem `ano`, `co_entidade`, `co_municipio` e `co_uf`; ajustar se forem outros).

## 6. Como rodar

```powershell
python carregar_mysql.py --blocos afd --conferir --faixas
python carregar_mysql.py --blocos afd
```

`--faixas` confirma que os percentuais cabem em `DECIMAL(5,1)` e que os textos cabem nos `VARCHAR`. A leitura dos 6,1 milhões de linhas leva alguns minutos.

## 7. Validação depois da carga (Workbench)

```sql
-- contagens esperadas
SELECT 'etapa', COUNT(*) FROM adequacao_formacao_docente.etapa                                  -- 7
UNION ALL SELECT 'cadastro_uf', COUNT(*) FROM adequacao_formacao_docente.cadastro_uf            -- 588
UNION ALL SELECT 'percentual_docentes', COUNT(*) FROM adequacao_formacao_docente.percentual_docentes  -- 20566
UNION ALL SELECT 'escola', COUNT(*) FROM adequacao_formacao_docente.escola                      -- 878665
UNION ALL SELECT 'percentual_docentes_escola', COUNT(*) FROM adequacao_formacao_docente.percentual_docentes_escola  -- 6150655
UNION ALL SELECT 'cadastro_municipio', COUNT(*) FROM adequacao_formacao_docente.cadastro_municipio  -- 67934
UNION ALL SELECT 'percentual_docentes_municipio', COUNT(*) FROM adequacao_formacao_docente.percentual_docentes_municipio;  -- 2349578

-- escolas da família que não existem em b0725.escola (esperado: 0)
SELECT e.NU_ANO_CENSO, COUNT(*) AS orfas
FROM adequacao_formacao_docente.escola e
LEFT JOIN b0725.escola b ON b.ano = e.NU_ANO_CENSO AND b.co_entidade = e.CO_ENTIDADE
WHERE b.co_entidade IS NULL
GROUP BY e.NU_ANO_CENSO;

-- municípios que não existem em dim.municipio (esperado: 0, ou só 5101837 se a dimensão estiver antiga)
SELECT DISTINCT c.CO_MUNICIPIO
FROM adequacao_formacao_docente.cadastro_municipio c
LEFT JOIN dim.municipio d ON d.co_municipio = c.CO_MUNICIPIO
WHERE d.co_municipio IS NULL;

-- códigos de UF do cadastro_uf que não existem em dim.estado (esperado: 0 linhas)
SELECT DISTINCT c.co_uf
FROM adequacao_formacao_docente.cadastro_uf c
LEFT JOIN dim.estado s ON s.co_uf = c.co_uf
WHERE c.nivel = 'UF' AND s.co_uf IS NULL;

-- distribuições completas que não fecham em 100 ± 1 (esperado: 0 linhas), repetir para as 3 tabelas de fato
SELECT * FROM adequacao_formacao_docente.percentual_docentes
WHERE CAT_1 IS NOT NULL AND CAT_2 IS NOT NULL AND CAT_3 IS NOT NULL
  AND CAT_4 IS NOT NULL AND CAT_5 IS NOT NULL
  AND ABS(CAT_1 + CAT_2 + CAT_3 + CAT_4 + CAT_5 - 100) > 1;
```

## 8. Para as próximas famílias

Cada família repete o padrão: um database próprio, um DDL, um bloco em `BLOCOS` com a sua `raiz` e uma seção de validação. Se a `etapa` (ou outra dimensão pequena) se repetir entre famílias, ela fica duplicada em cada database. É um custo baixo, e mantém cada família independente (TRUNCATE e recarga sem afetar as outras).
