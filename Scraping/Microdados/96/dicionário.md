# Dicionário de dados — Censo Escolar INEP 1996

Banco: SQL Server (DDL em `ddl_censo_1996_sqlserver.sql`). Fonte: `CENSOESC_1996` (uma linha por escola).
Os CSVs de cada tabela têm o mesmo nome da tabela e ficam em `C:\dados_inep\data_cen\Microdados da Educação Básica\1996\`.

## Convenções

- **Vazio (`NULL`) ≠ zero.** Vazio significa que a escola não informou a variável; zero significa que informou zero. Nas somas, se todas as variáveis somadas estão vazias, o resultado fica vazio.
- **`ano`** está em todas as tabelas "por ano" para permitir empilhar os anos seguintes sem mudar o modelo.
- **`id_ie`** em 1996 é `MASC1996_<MASCARA>`. A máscara só vale dentro do ano, então não rastreia a mesma escola entre anos (`escola.id_estavel = 0`). A partir de 2007 o identificador será o `CO_ENTIDADE`.
- **CA (classe de alfabetização)** é tratada como 1º ano do fundamental de 9 anos: entra em `fund_ini` junto com a 1ª–4ª série.
- **Tipos:** `PK` = chave primária, `FK → tabela.coluna` = chave estrangeira, `NN` = `NOT NULL`, `CK` = `CHECK`.

## Relacionamentos

```
estado 1───N municipio 1───N escola 1───N infraestrutura        (1:1 por escola/ano)
   │                          │   1───N escola_etapa ───N:1─── etapa
   └──────────────N───────────┘   1───N pne_modalidade
         (escola.sigla_uf)         1───N pne_deficiencia ──N:1── etapa
```

---

## 1. `estado`

Unidades da Federação. Grão: uma linha por UF.

| Coluna | Tipo | Descrição | Constraint |
|---|---|---|---|
| `sigla_uf` | `CHAR(2)` | Sigla da UF (ex.: `RO`). Origem: `SIGLA`. | PK |
| `nome_uf` | `NVARCHAR(40)` | Nome da UF como aparece no arquivo (em 1996, sem acento). Origem: `UF`. | NN |
| `co_uf` | `SMALLINT` | Código IBGE da UF (ex.: 11 = RO). Preenchido a partir de uma tabela fixa por sigla. | Índice único filtrado (`co_uf IS NOT NULL`) |

## 2. `municipio`

Municípios que aparecem no censo. Grão: uma linha por `CODMUNIC`.

| Coluna | Tipo | Descrição | Constraint |
|---|---|---|---|
| `cod_municipio` | `VARCHAR(12)` | Código do município do INEP (12 dígitos: região, UF, mesorregião, microrregião, município e dígito verificador). Origem: `CODMUNIC`. | PK |
| `nome_municipio` | `NVARCHAR(80)` | Nome do município. Origem: `MUNIC`. | NN |
| `sigla_uf` | `CHAR(2)` | UF do município. | NN, FK → `estado.sigla_uf` |

## 3. `etapa`

Etapas de ensino harmonizadas. Grão: uma linha por etapa. Valores fixos (inseridos pelo DDL).

| Coluna | Tipo | Descrição | Constraint |
|---|---|---|---|
| `etapa` | `VARCHAR(16)` | Código da etapa (valores abaixo). | PK |
| `descricao` | `NVARCHAR(80)` | Descrição legível. | NN |
| `etapa_ampla` | `VARCHAR(16)` | Agrupamento grosso: `infantil`, `fundamental`, `medio`, `especial`, `supletivo`. | NN |

| `etapa` | Significado |
|---|---|
| `pre` | Pré-escolar (períodos 1 a 3) |
| `fund_ini` | Fundamental anos iniciais (CA + 1ª a 4ª série) |
| `fund_fin` | Fundamental anos finais (5ª a 8ª série) |
| `fund_total` | Fundamental sem separar iniciais e finais (multisseriadas, PNE) |
| `medio` | Ensino médio (1ª a 4ª série + não seriada) |
| `especial` | Educação especial |
| `supletivo_s1` | Supletivo — 1º semestre |
| `supletivo_s2` | Supletivo — 2º semestre |

## 4. `escola`

Dados gerais da escola. Grão: escola × ano (`ano`, `id_ie`).

| Coluna | Tipo | Descrição | Constraint |
|---|---|---|---|
| `ano` | `SMALLINT` | Ano do censo (1996). Não vem de `ANO` do arquivo; é constante da carga. | PK, NN |
| `id_ie` | `VARCHAR(30)` | Identificador da escola: `MASC1996_<MASCARA>`. | PK, NN |
| `id_estavel` | `BIT` | `1` se o `id_ie` identifica a mesma escola entre anos; `0` se vale só dentro do ano (sempre `0` em 1996–2006). | NN, default `0` |
| `cod_municipio` | `VARCHAR(12)` | Município da escola. Origem: `CODMUNIC`. | NN, FK → `municipio.cod_municipio` |
| `sigla_uf` | `CHAR(2)` | UF da escola. Origem: `SIGLA`. | NN, FK → `estado.sigla_uf` |
| `dependencia` | `VARCHAR(10)` | Dependência administrativa: `Federal`, `Estadual`, `Municipal`, `Privada`. Origem: `DEP`. | — |
| `localizacao` | `VARCHAR(10)` | Zona da escola: `Urbana` ou `Rural`. Origem: `LOC`. | — |
| `cond_funcionamento` | `VARCHAR(11)` | Condição de funcionamento: `Ativo` (em funcionamento), `Paralizado` (atividades temporariamente suspensas), `Extinto` (encerradas definitivamente). Origem: `CODFUNC`. | — |
| `reg_cnas` | `VARCHAR(15)` | Número de registro no CNAS (Conselho Nacional de Assistência Social). Origem: `REG_CNAS`. Em geral vazio. | — |
| `niveis` | `VARCHAR(8)` | Níveis de ensino ministrados, em letras na ordem `P`, `I`, `F`, `M`: `P` pré-escolar, `I` CA + 1ª–4ª série, `F` 5ª–8ª série, `M` médio (ex.: `PIF`). Origem: `NIVELPRE`, `NIVELCA`, `NIV_F1A4_8`, `NIV_F5A8_8`, `NIVELMEDIO`. Vazio se nenhuma flag. | — |
| `modalidades` | `VARCHAR(4)` | Modalidades ministradas, em letras `R`, `E`, `S`: regular, especial, supletivo (ex.: `RS`). Origem: `ENSREGULAR`, `ENSESPECIAL`, `ENSSUPLET`. | — |
| `funcionarios` | `INT` | Total de funcionários da escola. Origem: `FUNCION`. | CK `>= 0` |

## 5. `infraestrutura`

Salas de aula. Grão: escola × ano. Relação 1:1 com `escola` (1996 só traz salas).

| Coluna | Tipo | Descrição | Constraint |
|---|---|---|---|
| `ano` | `SMALLINT` | Ano do censo. | PK, FK → `escola.ano` |
| `id_ie` | `VARCHAR(30)` | Escola. | PK, FK → `escola.id_ie` (FK composta com `ano`) |
| `salas_existentes_perm` | `INT` | Salas de aula existentes — permanentes. Origem: `PERMANEN`. | CK `>= 0` |
| `salas_existentes_prov` | `INT` | Salas de aula existentes — provisórias. Origem: `PROVISOR`. | CK `>= 0` |
| `salas_utilizadas_no_estab` | `INT` | Salas de aula utilizadas — no estabelecimento. Origem: `NOESTAB`. | CK `>= 0` |
| `salas_utilizadas_fora` | `INT` | Salas de aula utilizadas — fora do estabelecimento. Origem: `FORAESTA`. | CK `>= 0` |

> "Existentes" e "utilizadas" são conceitos diferentes; não some um par com o outro.

## 6. `escola_etapa`

Turmas, matrículas, rendimento e professores por etapa e turno. Grão: escola × ano × etapa × turno.
Só existem as linhas em que alguma medida foi informada.

| Coluna | Tipo | Descrição | Constraint |
|---|---|---|---|
| `ano` | `SMALLINT` | Ano do censo. | PK, FK → `escola.ano` |
| `id_ie` | `VARCHAR(30)` | Escola. | PK, FK → `escola.id_ie` (FK composta com `ano`) |
| `etapa` | `VARCHAR(16)` | Etapa de ensino. | PK, FK → `etapa.etapa` |
| `turno` | `VARCHAR(8)` | `diurno`, `noturno` ou `todos` (ver quadro abaixo). | PK, CK `IN ('diurno','noturno','todos')` |
| `turmas` | `INT` | Número de turmas na etapa e turno (soma das séries/períodos; em `fund_ini` inclui a CA; em `medio` inclui não seriada). Origem: `D/N + PE/EF/EM + 11 + 3..8`. | CK `>= 0` |
| `turmas_multisseriadas` | `INT` | Turmas multisseriadas do ensino fundamental (só em `fund_total`). Origem: `DEF11B`, `NEF11B`. | CK `>= 0` |
| `matriculas` | `INT` | Matrículas na etapa e turno (soma das séries/períodos). Origem: `D/N + PE/EF/EM + 11 + 8..J`. | CK `>= 0` |
| `matriculas_masc` | `INT` | Matrículas do sexo masculino na etapa (só em `turno = todos`). Origem: `VPE41x`, `VEF41x`, `VEM41x`. | CK `>= 0` |
| `matriculas_fem` | `INT` | Matrículas do sexo feminino na etapa (só em `turno = todos`). Origem: `VPE42x`, `VEF42x`, `VEM42x`. | CK `>= 0` |
| `abandono` | `INT` | Alunos afastados por abandono. Fundamental: só `fund_ini`/`fund_fin` (sem CA), `turno = todos`. Médio: por turno. Origem: `VEF84x`, `VEM94x`. | CK `>= 0` |
| `aprovados` | `INT` | Alunos aprovados (mesma estrutura de `abandono`). Origem: `VEF85x`, `VEM95x`. | CK `>= 0` |
| `reprovados` | `INT` | Alunos reprovados (mesma estrutura de `abandono`). Origem: `VEF86x`, `VEM96x`. | CK `>= 0` |
| `ano_referencia_rendimento` | `SMALLINT` | Ano a que se refere o rendimento (1996 — confirmado como o próprio ano). Preenchido só quando há abandono, aprovados ou reprovados. | — |
| `prof_total` | `INT` | Total de professores da etapa (soma das 7 formações). Origem: `VDG1<contexto><formação>`. **Não somar entre etapas**: o mesmo professor pode atuar em mais de uma. | CK `>= 0` |
| `prof_ate_fundamental` | `INT` | Professores com 1º grau incompleto ou completo (formações 1 e 2). | CK `>= 0` |
| `prof_medio_magisterio` | `INT` | Professores com magistério completo, 2º grau (formação 3). | CK `>= 0` |
| `prof_medio_outro` | `INT` | Professores com outra formação completa de 2º grau (formação 4). | CK `>= 0` |
| `prof_superior_licenciatura` | `INT` | Professores com licenciatura completa, 3º grau (formação 5). | CK `>= 0` |
| `prof_superior_sem_lic` | `INT` | Professores de nível superior sem licenciatura, com ou sem magistério (formações 6 e 7). | CK `>= 0` |
| `prof_com_capacitacao` | `INT` | Só `pre` e `especial`: professores com capacitação (pré) ou com curso específico (especial). | CK `>= 0` |
| `prof_sem_capacitacao` | `INT` | Só `pre` e `especial`: professores sem capacitação ou sem curso específico. | CK `>= 0` |

**Quais medidas existem em cada linha**

| `turno` | Medidas preenchidas |
|---|---|
| `diurno` / `noturno` | `turmas`, `matriculas`; `turmas_multisseriadas` (só `fund_total`); rendimento do `medio` |
| `todos` | `matriculas_masc`, `matriculas_fem`; rendimento de `fund_ini` e `fund_fin`; todas as colunas `prof_*` (inclusive `especial` e `supletivo_s1`/`s2`, que só têm `turno = todos`) |

> Validação feita na carga: para cada etapa, `matriculas` diurno + noturno = `matriculas_masc` + `matriculas_fem` em 100% das escolas comparáveis.
> Ressalvas: os professores de CA entram em `fund_ini` (pode contar duas vezes quem leciona na CA e na 1ª–4ª série); o rendimento dos anos iniciais cobre só a 1ª–4ª série, sem CA.

## 7. `pne_modalidade`

Alunos com necessidades especiais (PNE) por modalidade de atendimento. Grão: escola × ano × modalidade.

| Coluna | Tipo | Descrição | Constraint |
|---|---|---|---|
| `ano` | `SMALLINT` | Ano do censo. | PK, FK → `escola.ano` |
| `id_ie` | `VARCHAR(30)` | Escola. | PK, FK → `escola.id_ie` (FK composta com `ano`) |
| `modalidade` | `VARCHAR(24)` | Modalidade de atendimento (valores abaixo). | PK, CK (lista abaixo) |
| `turmas` | `INT` | Número de turmas para alunos PNE. Origem: `VEE11x`. | CK `>= 0` |
| `alunos` | `INT` | Alunos PNE com atendimento educativo. Origem: `VEE12x`. | CK `>= 0` |

Valores de `modalidade`: `todas` (total declarado; não é necessariamente a soma das demais, um aluno pode estar em mais de uma), `sala_recursos`, `oficina_pedagogica`, `oficina_protegida`, `classe_comum_apoio`, `classe_especial`, `apoio_psicopedagogico`, `educacao_precoce`, `escola_empresa`, `atend_hospitalar`, `atend_domiciliar`, `outras`.

## 8. `pne_deficiencia`

Alunos PNE por tipo de deficiência e etapa. Grão: escola × ano × etapa × deficiência.
É outro recorte da mesma população de `pne_modalidade`; os dois **não devem ser somados entre si**.

| Coluna | Tipo | Descrição | Constraint |
|---|---|---|---|
| `ano` | `SMALLINT` | Ano do censo. | PK, FK → `escola.ano` |
| `id_ie` | `VARCHAR(30)` | Escola. | PK, FK → `escola.id_ie` (FK composta com `ano`) |
| `etapa` | `VARCHAR(16)` | Etapa do aluno: só `pre`, `fund_total` ou `medio` (o arquivo não separa anos iniciais e finais). | PK, FK → `etapa.etapa`, CK `IN ('pre','fund_total','medio')` |
| `deficiencia` | `VARCHAR(12)` | `visao`, `audicao`, `fisica`, `mental` ou `multipla`. | PK, CK (lista anterior) |
| `alunos` | `INT` | Número de alunos PNE. Origem: `VEE61x` (pré), `VEE62x` (fundamental), `VEE63x` (médio). | CK `>= 0` |

---

## Fora do modelo de 1996

Descartados de propósito: coortes de nascimento (`VEF3`, `VPE3`, `VEM3`, `VEE2`–`VEE5`), repetentes e origem do aluno (`VEF5`–`VEF7`, `VEM5`–`VEM7`, `VPE5`), movimento (`VEM1`, `VEE7`), concluintes (`VEF9`), supletivo detalhado (`VES*`), professores totais da escola (`VDG11x`) e os campos `ANO`, `UF`/`SIGLA`/`MUNIC` (estes três já estão em `estado` e `municipio`).
Os arquivos `EM8`, `EM11` e `ES6` (grão escola × curso) ainda não entraram no modelo.