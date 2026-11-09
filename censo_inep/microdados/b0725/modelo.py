"""
Módulo de modelagem do bloco 2007-2025 (schema b0725), usado por gerar_tabelas_0725.py.

A partir SÓ dos arquivos do INEP (cabeçalhos dos CSVs + dicionários .xlsx das pastas 2007..2025):
  - descobre os arquivos de cada ano (microdados_ed_basica_<ano>, suplemento_cursos_tecnicos_<ano>,
    Tabela_<nome>_2025_V2) e lê os cabeçalhos -> em que anos e arquivos cada variável existe;
  - classifica cada variável numa tabela por TEMA e define o tipo SQL pelo dicionário;
  - aplica as renomeações aprovadas (RENOMEACOES) e gera o DDL.

Tabelas: escola, oferta, infraestrutura, equipamento, profissionais, matricula, turma, docente,
         curso_tecnico (escola x curso) + curso (nome e área do curso, 3FN) + variavel e dominio (metadados).
Fora (3FN): nomes de região/UF/município/meso/microrregião (dependem do município: dim) e CO_REDE
         (repete TP_DEPENDENCIA).

Rodado sozinho é só AUDITORIA: grava a classificação, os candidatos a renomeação e o DDL em
<dados_processados>/Microdados_0725/auditoria (nunca na pasta do código).
  python modelar_b0725.py
"""
from __future__ import annotations

import argparse
import difflib
import logging
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd

logger = logging.getLogger("modelar_b0725")

RAIZ_PADRAO = Path("C:/dados_inep/data_cen/Microdados da Educação Básica")
AUDITORIA_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/Microdados_0725/auditoria")
ANOS = [str(a) for a in range(2007, 2026)]
CHAVE = ["NU_ANO_CENSO", "CO_ENTIDADE"]

# Renomeações APROVADAS (A4): nome antigo -> nome novo. Viram uma coluna só, com o nome novo.
# Para aprovar outra: rode este módulo (auditoria), veja b0725_candidatos_renomeacao.csv e acrescente aqui.
RENOMEACOES = {"QT_MAT_TEC_EJA": "QT_MAT_CURSO_TEC_EJA", "QT_MAT_TEC_SUBS": "QT_MAT_CURSO_TEC_SUBS"}
# Fora do modelo por redundância (3FN): variável -> motivo
FORA = {"CO_REDE": "repete TP_DEPENDENCIA (1-3 = pública, 4 = privada)"}

# arquivo base (identificação da escola) de cada layout e arquivo de cada tema em 2025
BASE = {"microdados", "escola"}
ARQUIVO_DO_TEMA = {"matricula": "matricula", "turma": "turma", "docente": "docente",
                   "profissionais": "gestor_escolar", "curso_tecnico": "curso_tecnico", "curso": "curso_tecnico"}

# ---------------------------------------------------------------------------
# 1. Regras de tema (a primeira que casar vale). Regex no NOME da variável.
# ---------------------------------------------------------------------------
# dependem do município, não da escola (3FN) -> dim.municipio / dim.estado. CO_MUNICIPIO fica (é a FK).
NA_DIMENSAO = r"(NO|CO|SG)_(REGIAO|UF|MESORREGIAO|MICRORREGIAO|REGIAO_GEOG_(INTERM|IMED))|NO_MUNICIPIO"
# dependem do curso, não da escola (3FN) -> b0725.curso (um registro por curso técnico)
NO_CURSO = r"NO_CURSO_EDUC_PROFISSIONAL|ID_AREA_CURSO_PROFISSIONAL|NO_AREA_CURSO_PROFISSIONAL"
REGRAS_ESCOLA = [   # (tabela, regex) para as variáveis da Tabela de Escola (e as descontinuadas)
    ("escola", r"NO_ENTIDADE|CO_MUNICIPIO|CO_DISTRITO|NO_DISTRITO|CO_SUBDISTRITO|NO_SUBDISTRITO|"
               r"TP_DEPENDENCIA|TP_LOCALIZACAO.*|TP_SITUACAO_FUNCIONAMENTO|TP_CATEGORIA_ESCOLA_PRIVADA|"
               r"DS_.*|NU_ENDERECO|NO_BAIRRO|CO_CEP|NU_DDD|NU_TELEFONE.*|NU_FAX|DS_EMAIL|LATITUDE|LONGITUDE|"
               r"DT_ANO_LETIVO_.*|CO_ORGAO_REGIONAL|TP_CONVENIO.*|TP_PODER_PUBLICO.*|IN_CONVENIADA_PP|"
               r"IN_MANT_.*|TP_REGULAMENTACAO|TP_RESPONSAVEL_REGULAMENTACAO|IN_VINCULO_.*|"
               r"IN_ESCOLA_SEDE.*|CO_ESCOLA_SEDE.*|CO_IES_OFERTANTE|IN_PODER_PUBLICO.*|IN_.*CNPJ.*|NU_CNPJ.*|"
               r"TP_ESFERA.*|IN_LOCAL_DIFERENCIADA.*"),
    ("equipamento", r"(IN|QT)_(EQUIP|COMP|DESKTOP|TABLET|INTERNET|BANDA_LARGA|REDE_LOCAL|ACESSO_INTERNET|"
                    r"ACES_INTERNET|LOUSA|NOTEBOOK|PROJETOR|TV|DVD|IMPRESSORA|COPIADORA|FAX|SOM|MULTIMIDIA|"
                    r"FOTO|RETRO|VIDEO|PARABOLICA|SCANNER)\w*|IN_COMPUTADOR|QT_COMPUTADOR|TP_REDE_LOCAL"),
    ("profissionais", r"(IN|QT)_PROF_\w*|QT_FUNCIONARIOS|IN_GESTOR\w*|QT_GESTOR\w*|TP_GESTOR\w*|QT_GEST_\w*"),
    ("infraestrutura", r"(IN|QT|TP)_(AGUA|ENERGIA|ESGOTO|LIXO|TRATAMENTO|SALA|SALAS|BANHEIRO|BIBLIOTECA|"
                       r"LABORATORIO|QUADRA|COZINHA|REFEITORIO|PARQUE|BERCARIO|DEPENDENCIAS|AUDITORIO|"
                       r"DESPENSA|ALMOXARIFADO|AREA|PATIO|PISCINA|DORMITORIO|ACESSIBILIDADE|VESTIARIO|"
                       r"LOCAL_FUNC|PREDIO|ALOJAM|ESTUDIO|HORTA|TERREIRAO|VIVEIRO|SECRETARIA|ARQUIVO|"
                       r"OCUPACAO|BRINQUEDOTECA|LAVANDERIA|GARAGEM|ALMOX|ENERG|INSUMOS|RECURSOS|PARQUE_INFANTIL|"
                       r"ESPACO|MANT_ESCOLA|CANTINA|ALIMENTO)\w*"),
]
ABA_TABELA = {"matr": "matricula", "turma": "turma", "docente": "docente", "gestor": "profissionais",
              "curso": "curso_tecnico"}
PREFIXO_TABELA = [(r"QT_MAT\w*|QT_TRANSP\w*", "matricula"), (r"QT_TUR\w*", "turma"), (r"QT_DOC\w*", "docente")]

ORDEM_TABELAS = ["escola", "oferta", "infraestrutura", "equipamento", "profissionais", "matricula", "turma",
                 "docente", "curso_tecnico"]


def comp(s: object) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s)


def tema(v: str, aba: str) -> tuple[str, str]:
    """(tabela, regra) de uma variável."""
    if v in CHAVE:
        return "chave", "chave de todas as tabelas"
    if v in FORA:
        return "fora", FORA[v]
    if re.fullmatch(NA_DIMENSAO, v):
        return "dimensao", "depende do município (3FN): fica em dim.municipio / dim.estado"
    if re.fullmatch(NO_CURSO, v):
        return "curso", "depende do curso (3FN): fica em b0725.curso"
    a = comp(aba)
    for k, t in ABA_TABELA.items():
        if k in a:
            if t == "curso_tecnico" or t == "profissionais":
                return t, f"aba '{aba}'"
            if any(re.fullmatch(rx, v) for rx, _ in PREFIXO_TABELA):
                return t, f"aba '{aba}'"
    for rx, t in PREFIXO_TABELA:
        if re.fullmatch(rx, v):
            return t, f"prefixo {rx}"
    for t, rx in REGRAS_ESCOLA:
        if re.fullmatch(rx, v):
            return t, f"regra de nome ({t})"
    return "oferta", "demais IN_/TP_/QT_ da Tabela de Escola (oferta e organização)"


# ---------------------------------------------------------------------------
# 1b. Arquivos e cabeçalhos (mesma regra do verificar_07_25.py)
# ---------------------------------------------------------------------------
def limpar(c: object) -> str:
    return str(c).strip().lstrip("\ufeff").replace("ï»¿", "")


def detectar_formato(caminho: Path) -> tuple[str, str]:
    """(encoding, separador): utf-8 se os primeiros 500 KB decodificam, senão cp1252; o separador que mais
    aparece no cabeçalho."""
    with caminho.open("rb") as f:
        bruto = f.read(500_000)
    try:
        bruto.decode("utf-8")
        enc = "utf-8-sig"
    except UnicodeDecodeError:
        enc = "cp1252"
    cab = bruto.split(b"\n", 1)[0].decode(enc.replace("-sig", ""), errors="replace")
    return enc, max([";", "|", ",", "\t"], key=cab.count)


def cabecalho(caminho: Path) -> list[str]:
    enc, sep = detectar_formato(caminho)
    return [limpar(c) for c in pd.read_csv(caminho, sep=sep, encoding=enc, nrows=0, encoding_errors="replace").columns]


def arquivos_do_ano(raiz: Path, ano: int) -> dict[str, Path]:
    """{'microdados' | 'suplemento_cursos_tecnicos' | 'escola' | 'matricula' | ...: caminho}. Em 2025 o nome
    lógico sai de Tabela_<nome>_2025_V2 (sem diferenciar maiúsculas). Vale o maior arquivo de cada nome."""
    pasta = raiz / str(ano)
    out: dict[str, Path] = {}
    if not pasta.is_dir():
        return out
    for p in sorted(pasta.rglob("*"), key=lambda p: -p.stat().st_size if p.is_file() else 0):
        if not p.is_file() or p.suffix.lower() not in (".csv", ".txt") or p.name.startswith("~$"):
            continue
        s = p.stem.lower()
        if s == f"microdados_ed_basica_{ano}":
            nome = "microdados"
        elif s.startswith("suplemento_cursos_tecnicos") and str(ano) in s:
            nome = "suplemento_cursos_tecnicos"
        elif (m := re.fullmatch(rf"tabela_(.+?)_{ano}(_v\d+)?", s)):
            nome = m.group(1)
        else:
            continue
        out.setdefault(nome, p)
    return out


def ler_cabecalhos(raiz: Path, anos: list[int] | None = None) -> dict[int, dict[str, tuple[Path, list[str]]]]:
    """{ano: {nome_logico: (caminho, colunas)}} de 2007 a 2025. Ano sem arquivo base PARA (o modelo precisa de
    todos os anos para saber o período de cada variável)."""
    out = {}
    for ano in anos or [int(a) for a in ANOS]:
        arqs = arquivos_do_ano(raiz, ano)
        if not BASE & set(arqs):
            raise SystemExit(f"{ano}: arquivo base não encontrado em {raiz / str(ano)} "
                             f"(microdados_ed_basica_{ano}.csv ou Tabela_Escola_{ano}_V2.csv)")
        out[ano] = {n: (p, cabecalho(p)) for n, p in arqs.items()}
    return out


def presenca(cabs: dict[int, dict[str, tuple[Path, list[str]]]]) -> pd.DataFrame:
    """variavel -> anos em que está em algum arquivo."""
    anos = defaultdict(set)
    for ano, arqs in cabs.items():
        for _, cols in arqs.values():
            for c in cols:
                anos[c].add(ano)
    p = pd.DataFrame({"variavel": list(anos), "anos_lista": [sorted(a) for a in anos.values()]})
    p["ano_inicio"] = p.anos_lista.map(min)
    p["ano_fim"] = p.anos_lista.map(max)
    p["anos"] = p.anos_lista.map(lambda xs: ",".join(map(str, xs)))
    return p


# ---------------------------------------------------------------------------
# 2. Dicionários: tipo, tamanho, categorias (definição mais recente de cada variável)
# ---------------------------------------------------------------------------
def ler_dicionarios(raiz: Path, todos: bool = False) -> pd.DataFrame:
    """Dicionários .xlsx de 2007..2025. Padrão: uma definição por variável (a do ano mais recente).
    todos=True: todas as definições de todos os anos, em ordem do mais antigo ao mais recente (para o domínio)."""
    import openpyxl
    linhas = []
    for ano in sorted(ANOS):
        pasta = raiz / ano
        if not pasta.is_dir():
            continue
        for p in sorted(pasta.rglob("*.xlsx")):
            if p.name.startswith("~$") or "dicion" not in comp(p.name).replace(" ", ""):
                continue
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            for ws in wb.worksheets:
                rows = list(ws.iter_rows(values_only=True))
                hi = next((i for i, r in enumerate(rows) if r and r[0] is not None and str(r[0]).strip() == "N"), None)
                if hi is None:
                    continue
                for r in rows[hi + 2:]:
                    if not r or len(r) < 6 or r[1] is None or not str(r[0] or "").strip().isdigit():
                        continue
                    linhas.append({"ano_dic": int(ano), "aba": ws.title.strip(), "variavel": str(r[1]).strip(),
                                   "descricao": re.sub(r"\s+", " ", str(r[2] or "")).strip(),
                                   "tipo_dic": str(r[3] or "").strip(), "tam": str(r[4] or "").strip(),
                                   "categoria": str(r[5]) if r[5] is not None else ""})
            logger.info("dicionário %s: %s", ano, p.name)
    d = pd.DataFrame(linhas)
    if d.empty:
        raise SystemExit(f"Nenhum dicionário .xlsx encontrado em {raiz}/2007..2025")
    # definição mais recente; dentro do mesmo ano, a da aba temática (não a Tabela de Escola, que repete chaves)
    d["_escola"] = d.aba.map(lambda a: "escola" in comp(a))
    # colunas de identificação se repetem em todas as abas de 2025: vale a da Tabela de Escola
    d = d.sort_values(["ano_dic", "_escola"], ascending=[True, True])
    return d if todos else d.drop_duplicates("variavel", keep="last")


def tipo_sql(v: str, tipo: str, tam: str) -> str:
    t = comp(tipo)
    n = int(re.sub(r"\D", "", tam) or 0)
    if v == "NU_ANO_CENSO":
        return "SMALLINT"
    if v == "CO_MUNICIPIO":
        return "CHAR(7)"                         # como dim.municipio.cod_municipio
    if v in ("LATITUDE", "LONGITUDE"):
        return "DECIMAL(12,8)"
    if v.startswith(("NU_TELEFONE", "NU_FAX")):
        return "VARCHAR(20)"                     # o dicionário diz 8 dígitos, mas celular tem 9 desde 2016
    if v in ("CO_CEP", "NU_DDD") or v.startswith("NU_CNPJ"):
        return f"VARCHAR({max(n, 8)})"            # código, não quantidade: preserva zeros à esquerda
    if "data" in t or v.startswith("DT_"):
        return "VARCHAR(20)"                     # formato da origem varia; fica como texto
    if "char" in t:
        return f"NVARCHAR({min(max(n, 1), 4000)})" if n else "NVARCHAR(255)"
    if v.startswith("IN_"):
        return "TINYINT"
    if v.startswith("TP_"):
        return "TINYINT" if n <= 2 else "SMALLINT"
    if v.startswith(("QT_", "NU_")):
        return "INT"
    if v.startswith("CO_") or v.startswith("ID_"):
        return "SMALLINT" if n <= 4 else ("INT" if n <= 9 else "BIGINT")
    return "INT" if n else "NVARCHAR(255)"


def dominios(dic: pd.DataFrame) -> pd.DataFrame:
    """(variavel, codigo, rotulo). dic pode ter vários anos (ordem do mais antigo ao mais recente):
    um código documentado em qualquer ano entra, com o rótulo do ano mais recente em que aparece."""
    linhas = []
    for v, cat in zip(dic.variavel, dic.categoria):
        for linha in str(cat).splitlines():
            m = re.match(r"^\s*(-?\d+)\s*-\s*(.+?)\s*$", linha)
            if m:
                linhas.append({"variavel": v, "codigo": int(m.group(1)), "rotulo": m.group(2)})
    return pd.DataFrame(linhas).drop_duplicates(["variavel", "codigo"], keep="last")


# ---------------------------------------------------------------------------
# 3. Candidatos a renomeação (A4): mesmo tema, anos sem sobreposição e em sequência, descrição parecida
# ---------------------------------------------------------------------------
def parte_distinta(a: str, b: str) -> tuple[str, str]:
    """Tira o começo comum das duas descrições (ex.: 'Dependências físicas existentes ... - '):
    a comparação fica só no que de fato distingue as variáveis."""
    wa, wb = comp(a).split(), comp(b).split()
    i = 0
    while i < min(len(wa), len(wb)) and wa[i] == wb[i]:
        i += 1
    return " ".join(wa[i:]), " ".join(wb[i:])


def candidatos(v: pd.DataFrame, limite: float) -> pd.DataFrame:
    """Pares (antiga -> nova) no mesmo tema, anos sem sobreposição e em sequência (no máximo 1 ano de buraco).
    Nota = média de: similaridade da PARTE DISTINTA das descrições, similaridade dos nomes e fração das
    partes do nome antigo (QT, MAT, CT, 1...) que aparecem no novo.
    Fica só o melhor par de cada variável (um para um)."""
    linhas = []
    for tab, g in v[v.tabela.isin(ORDEM_TABELAS + ["curso"])].groupby("tabela"):
        g = g.reset_index(drop=True)
        for i, a in g.iterrows():
            for _, b in g.iloc[i + 1:].iterrows():
                if a.ano_fim < b.ano_inicio:
                    velho, novo = a, b
                elif b.ano_fim < a.ano_inicio:
                    velho, novo = b, a
                else:
                    continue                                     # anos se sobrepõem: são variáveis diferentes
                if novo.ano_inicio - velho.ano_fim > 2:
                    continue
                da, db = parte_distinta(velho.descricao, novo.descricao)
                sd = difflib.SequenceMatcher(None, da, db).ratio() if (da or db) else 1.0
                sn = difflib.SequenceMatcher(None, velho.variavel, novo.variavel).ratio()
                ta, tb = set(velho.variavel.split("_")), set(novo.variavel.split("_"))
                st = len(ta & tb) / len(ta)                      # partes do nome antigo presentes no novo
                nota = (sd + sn + st) / 3
                if nota >= limite:
                    linhas.append({"tabela": tab, "antiga": velho.variavel,
                                   "anos_antiga": f"{velho.ano_inicio}-{velho.ano_fim}",
                                   "nova": novo.variavel, "anos_nova": f"{novo.ano_inicio}-{novo.ano_fim}",
                                   "nota": round(nota, 2), "sim_descricao": round(sd, 2), "sim_nome": round(sn, 2),
                                   "partes_do_nome": round(st, 2),
                                   "descricao_antiga": velho.descricao[:150], "descricao_nova": novo.descricao[:150],
                                   "aprovar": ""})
    c = pd.DataFrame(linhas)
    if c.empty:
        return c
    c = c.sort_values("nota", ascending=False)
    c = c.drop_duplicates("antiga").drop_duplicates("nova")      # um para um: o melhor par de cada lado
    return c.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4. DDL
# ---------------------------------------------------------------------------
FK_DIM = {"CO_MUNICIPIO": "dim.municipio (cod_municipio)", "TP_DEPENDENCIA": "dim.dependencia (codigo)",
          "TP_SITUACAO_FUNCIONAMENTO": "dim.situacao_funcionamento (codigo)",
          "TP_LOCALIZACAO": "dim.localizacao (codigo)"}


def tipo_mysql(t: str) -> str:
    """Tipo neutro do modelo -> MySQL 8. (O modelo guarda o tipo neutro: é ele que a validação do gerador usa.)"""
    if t == "TINYINT":
        return "TINYINT UNSIGNED"                # no MySQL TINYINT vai de -128 a 127; os códigos do INEP vão até 99
    return re.sub(r"^NVARCHAR", "VARCHAR", t)     # o database é utf8mb4: VARCHAR já guarda qualquer acento


def ddl(v: pd.DataFrame) -> str:
    """DDL do b0725 em MySQL 8 (idempotente: CREATE ... IF NOT EXISTS)."""
    def coluna(r, nota: str) -> str:
        return f"    {r.variavel.lower():40s} {tipo_mysql(r.tipo_sql):18s} NULL,  -- {nota}"

    out = ["-- =============================================================================",
           "-- Censo Escolar INEP - ddl 07 25.sql (MySQL 8.0+). GERADO por gerar_tabelas_0725.py: não editar à mão.",
           "-- Bloco por escola 2007-2025 (database b0725). Chave estável: (ano, co_entidade). Requer ddl Completo.sql.",
           "-- Colunas com o nome do INEP em minúsculas; NULL fora do período da variável (ver b0725.variavel).",
           "-- Valores como na origem (flags 0/1, códigos TP_ com rótulo em b0725.dominio).",
           "-- Idempotente: cria só o que não existe. Para recomeçar: ddl reset.sql.",
           "-- =============================================================================", "",
           "CREATE DATABASE IF NOT EXISTS b0725 CHARACTER SET utf8mb4;", ""]
    for tab in ORDEM_TABELAS:
        g = v[v.tabela == tab].sort_values("variavel")
        if g.empty:
            continue
        chave = ["ano SMALLINT NOT NULL", "co_entidade INT NOT NULL"]
        pk = ["ano", "co_entidade"]
        if tab == "curso_tecnico":
            dc = v[v.tabela == "curso"].sort_values("variavel")
            out += ["-- ---------- curso (dimensão dos cursos técnicos: nome e área dependem do curso) ----------",
                    "CREATE TABLE IF NOT EXISTS b0725.curso (",
                    "    co_curso_educ_profissional INT NOT NULL,",
                    *[coluna(r, "valor mais recente") for r in dc.itertuples()],
                    "    CONSTRAINT pk_b0725_curso PRIMARY KEY (co_curso_educ_profissional)", ") ENGINE = InnoDB;", ""]
            chave.append("co_curso_educ_profissional INT NOT NULL")
            pk.append("co_curso_educ_profissional")
            g = g[g.variavel != "CO_CURSO_EDUC_PROFISSIONAL"]
        cols = [f"    {c}," for c in chave] + [coluna(r, f"{r.ano_inicio}-{r.ano_fim}") for r in g.itertuples()]
        cons = [f"    CONSTRAINT pk_b0725_{tab} PRIMARY KEY ({', '.join(pk)})"]
        if tab == "escola":
            for var, ref in FK_DIM.items():
                if var in set(g.variavel):
                    cons.append(f"    CONSTRAINT fk_b0725_escola_{var.lower()} FOREIGN KEY ({var.lower()}) REFERENCES {ref}")
            cons.append("    CONSTRAINT ck_b0725_escola_ano CHECK (ano BETWEEN 2007 AND 2025)")
            cons.append("    INDEX ix_b0725_escola_nucleo (ano, co_municipio, tp_dependencia, tp_situacao_funcionamento)")
        else:
            cons.append(f"    CONSTRAINT fk_b0725_{tab}_escola FOREIGN KEY (ano, co_entidade) "
                        f"REFERENCES b0725.escola (ano, co_entidade)")
        if tab == "curso_tecnico":
            cons.append("    CONSTRAINT fk_b0725_curso_tecnico_curso FOREIGN KEY (co_curso_educ_profissional) "
                        "REFERENCES b0725.curso (co_curso_educ_profissional)")
        out += [f"-- ---------- {tab} ({len(g)} variáveis) ----------",
                f"CREATE TABLE IF NOT EXISTS b0725.{tab} (", *cols, ",\n".join(cons), ") ENGINE = InnoDB;", ""]
    out += ["-- ---------- metadados ----------",
            "CREATE TABLE IF NOT EXISTS b0725.variavel (",
            "    variavel       VARCHAR(40)   NOT NULL,   -- nome do INEP (a coluna é o nome em minúsculas)",
            "    tabela         VARCHAR(20)   NOT NULL,",
            "    descricao      TEXT          NULL,       -- descrições do dicionário passam de 700 caracteres",
            "    ano_inicio     SMALLINT      NOT NULL,   -- primeiro ano em que está no arquivo",
            "    ano_fim        SMALLINT      NOT NULL,",
            "    anos           VARCHAR(120)  NOT NULL,   -- anos presentes (pode ter buracos)",
            "    nome_anterior  VARCHAR(40)   NULL,       -- renomeação aprovada (A4)",
            "    CONSTRAINT pk_b0725_variavel PRIMARY KEY (variavel)", ") ENGINE = InnoDB;", "",
            "CREATE TABLE IF NOT EXISTS b0725.dominio (",
            "    variavel  VARCHAR(40)   NOT NULL,",
            "    codigo    BIGINT        NOT NULL,   -- há códigos como 88888 e 99999999999999 (CNPJ sem declaração)",
            "    rotulo    VARCHAR(500)  NOT NULL,",
            "    CONSTRAINT pk_b0725_dominio PRIMARY KEY (variavel, codigo),",
            "    CONSTRAINT fk_b0725_dominio_variavel FOREIGN KEY (variavel) REFERENCES b0725.variavel (variavel)",
            ") ENGINE = InnoDB;", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 5. Modelo completo (o que o gerador usa)
# ---------------------------------------------------------------------------
COLS_MODELO = ["variavel", "tabela", "tipo_sql", "tipo_dic", "tam", "ano_inicio", "ano_fim", "anos", "nome_anterior",
               "aba", "regra", "descricao"]


def montar_modelo(raiz: Path, cabs: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(modelo, dominios). modelo: uma linha por coluna do b0725 (já com as RENOMEACOES aplicadas)."""
    p = presenca(cabs)
    todos = ler_dicionarios(raiz, todos=True)
    dic = todos.drop_duplicates("variavel", keep="last").set_index("variavel")   # tipo/descrição: ano mais recente
    p["tipo_dic"] = p.variavel.map(dic.tipo_dic).fillna("")
    p["tam"] = p.variavel.map(dic.tam).fillna("")
    p["aba"] = p.variavel.map(dic.aba).fillna("")
    p["descricao"] = p.variavel.map(dic.descricao).fillna("")
    sem_dic = p[p.tipo_dic.eq("")].variavel.tolist()
    if sem_dic:
        logger.warning("%d variáveis sem tipo em nenhum dicionário (tipo pelo prefixo): %s", len(sem_dic), sem_dic[:10])
    p[["tabela", "regra"]] = p.apply(lambda r: pd.Series(tema(r.variavel, r.aba)), axis=1)
    p["tipo_sql"] = [tipo_sql(v, t, n) for v, t, n in zip(p.variavel, p.tipo_dic, p.tam)]
    p["nome_anterior"] = ""
    for antiga, nova in RENOMEACOES.items():
        ia, inv = p.index[p.variavel == antiga], p.index[p.variavel == nova]
        if ia.empty or inv.empty:
            raise SystemExit(f"renomeação {antiga} -> {nova}: variável não encontrada nos arquivos")
        anos = sorted(set(p.at[ia[0], "anos_lista"]) | set(p.at[inv[0], "anos_lista"]))
        p.at[inv[0], "anos_lista"] = anos
        p.loc[inv[0], ["ano_inicio", "ano_fim", "anos", "nome_anterior"]] = \
            [min(anos), max(anos), ",".join(map(str, anos)), antiga]
        p = p.drop(index=ia)
    dom = dominios(todos)                                  # domínio: união dos códigos de todos os anos
    dom = dom[dom.variavel.isin(set(p.variavel))]
    return p.sort_values(["tabela", "variavel"]).reset_index(drop=True), dom.reset_index(drop=True)


def resumo(p: pd.DataFrame) -> str:
    per = p.apply(lambda r: "2025" if r.ano_inicio == 2025 else ("2023+" if r.ano_inicio >= 2023 else "2007-2022"),
                  axis=1).rename("periodo")
    return pd.crosstab(p.tabela, per, margins=True, margins_name="total").to_string()


# ---------------------------------------------------------------------------
def main() -> int:
    """Auditoria: grava a classificação, os candidatos a renomeação, os domínios e o DDL na pasta de auditoria."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO)
    ap.add_argument("--saida", type=Path, default=AUDITORIA_PADRAO)
    ap.add_argument("--limite", type=float, default=0.60, help="nota mínima dos candidatos a renomeação (A4)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p, dom = montar_modelo(args.raiz, ler_cabecalhos(args.raiz))
    args.saida.mkdir(parents=True, exist_ok=True)
    p[COLS_MODELO].to_csv(args.saida / "b0725_variaveis.csv", index=False, encoding="utf-8-sig")
    cand = candidatos(p, args.limite)
    cand.to_csv(args.saida / "b0725_candidatos_renomeacao.csv", index=False, encoding="utf-8-sig")
    dom.to_csv(args.saida / "b0725_dominios.csv", index=False, encoding="utf-8-sig")
    (args.saida / "ddl 07 25.sql").write_text(ddl(p), encoding="utf-8")
    print("\n=== variáveis por tabela e período ===\n" + resumo(p))
    print(f"\ncandidatos a renomeação: {len(cand)}  |  domínios: {dom.variavel.nunique()} variáveis, {len(dom)} códigos")
    print(f"auditoria em {args.saida.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())