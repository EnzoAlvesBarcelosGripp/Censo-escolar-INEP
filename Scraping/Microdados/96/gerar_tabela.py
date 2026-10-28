"""
Gera os CSVs do modelo relacional (DDL de 1996) a partir de CENSOESC_1996.CSV.

Entrada : CENSOESC_1996.CSV (delimitador '|', uma linha por escola, ~800 colunas)
Saída   : na mesma pasta do arquivo de entrada (…/1996/), um CSV por tabela, com o nome da tabela:
            estado.csv, municipio.csv, etapa.csv,
            escola.csv, infraestrutura.csv, escola_etapa.csv,
            pne_modalidade.csv, pne_deficiencia.csv
          As colunas tec_* e supl_prof_* de escola.csv vêm de EM8_1996, EM11_1996 e ES6_1996
          (mesma pasta do CENSOESC_1996), somadas por escola.

Como funciona (resumo):
  1. lê o arquivo em blocos (chunks) e só as colunas necessárias -> memória baixa;
  2. cada bloco vira as tabelas "largas" (escola, infraestrutura) e "longas" (escola_etapa, PNE);
  3. as dimensões (estado, município) são acumuladas durante a leitura e gravadas no fim;
  4. os CSVs são gravados em arquivo temporário e só viram definitivos no final
     (se der erro no meio, não sobra CSV pela metade).

Exemplos:
  python gerar_tabelas_1996.py
  python gerar_tabelas_1996.py --entrada "D:/outro/lugar/CENSOESC_1996.CSV"
  python gerar_tabelas_1996.py --so-dimensoes
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger("tabelas_1996")

ANO = 1996
ENTRADA_PADRAO = Path("C:/dados_inep/data_cen/Microdados da Educação Básica/1996/CENSOESC_1996")
NOME_BASE = "censoesc_1996"
SEP_ENTRADA = "|"

# ---------------------------------------------------------------------------
# 1. Constantes do modelo
# ---------------------------------------------------------------------------
# Código IBGE da UF (usado só para preencher estado.co_uf)
CO_UF_IBGE = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28, "BA": 29,
    "MG": 31, "ES": 32, "RJ": 33, "SP": 35, "PR": 41, "SC": 42, "RS": 43,
    "MS": 50, "MT": 51, "GO": 52, "DF": 53,
}

DIM_ETAPA = [
    ("pre", "Pré-escolar (períodos 1 a 3)", "infantil"),
    ("fund_ini", "Fundamental anos iniciais (CA + 1ª a 4ª série)", "fundamental"),
    ("fund_fin", "Fundamental anos finais (5ª a 8ª série)", "fundamental"),
    ("fund_total", "Fundamental sem separar iniciais/finais", "fundamental"),
    ("medio", "Ensino médio (1ª a 4ª série + não seriada)", "medio"),
    ("especial", "Educação especial", "especial"),
    ("supletivo_s1", "Supletivo - 1º semestre", "supletivo"),
    ("supletivo_s2", "Supletivo - 2º semestre", "supletivo"),
]

COLS_ESTADO = ["sigla_uf", "nome_uf", "co_uf"]
COLS_MUNICIPIO = ["cod_municipio", "nome_municipio", "sigla_uf"]
# agregados dos arquivos EM8/EM11/ES6 (grão escola x curso), somados por escola
COLS_EXTRA_ESCOLA = ["tec_cursos", "tec_matriculas", "tec_concluintes_ant",
                     "supl_prof_matriculas_s1_ant", "supl_prof_concluintes_s1_ant",
                     "supl_prof_matriculas_s2_ant", "supl_prof_concluintes_s2_ant"]
COLS_IE = ["ano", "id_ie", "id_estavel", "cod_municipio", "sigla_uf", "dependencia", "localizacao",
           "cond_funcionamento", "reg_cnas", "niveis", "modalidades", "funcionarios"] + COLS_EXTRA_ESCOLA
COLS_INFRA = ["ano", "id_ie", "salas_existentes_perm", "salas_existentes_prov",
              "salas_utilizadas_no_estab", "salas_utilizadas_fora"]
MEDIDAS_ETAPA = ["turmas", "turmas_multisseriadas", "matriculas", "matriculas_masc", "matriculas_fem",
                 "abandono", "aprovados", "reprovados", "ano_referencia_rendimento",
                 "prof_total", "prof_ate_fundamental", "prof_medio_magisterio", "prof_medio_outro",
                 "prof_superior_licenciatura", "prof_superior_sem_lic",
                 "prof_com_capacitacao", "prof_sem_capacitacao"]
COLS_ETAPA = ["ano", "id_ie", "etapa", "turno"] + MEDIDAS_ETAPA
COLS_PNE_MOD = ["ano", "id_ie", "modalidade", "turmas", "alunos"]
COLS_PNE_DEF = ["ano", "id_ie", "etapa", "deficiencia", "alunos"]

# ---------------------------------------------------------------------------
# 2. Mapa "variável do INEP -> coluna do modelo" (a gramática dos códigos de 1996)
#    D/N + PE/EF/EM + 11 + índice  = turmas/matrículas por turno
#    VPE/VEF/VEM + 4 + sexo        = matrículas por sexo
#    VEF84-86 / VEM94-96           = abandono/aprovados/reprovados
#    VDG1 + contexto + formação    = professores
# ---------------------------------------------------------------------------
def _seq(prefixo: str, sufixos: Iterable[str]) -> list[str]:
    return [f"{prefixo}{s}" for s in sufixos]


def mapa_turno(t: str) -> dict[tuple[str, str], list[str]]:
    """(etapa, medida) -> variáveis a SOMAR, para o turno t ('D' ou 'N')."""
    return {
        ("pre", "turmas"): _seq(f"{t}PE11", "456"),
        ("pre", "matriculas"): _seq(f"{t}PE11", "ABC"),
        # CA entra nos anos iniciais (CA = 1º ano do fundamental de 9 anos)
        ("fund_ini", "turmas"): [f"{t}PE118"] + _seq(f"{t}EF11", "3456"),
        ("fund_ini", "matriculas"): [f"{t}PE11E"] + _seq(f"{t}EF11", "CDEF"),
        ("fund_fin", "turmas"): _seq(f"{t}EF11", "789A"),
        ("fund_fin", "matriculas"): _seq(f"{t}EF11", "GHIJ"),
        ("fund_total", "turmas_multisseriadas"): [f"{t}EF11B"],
        ("medio", "turmas"): _seq(f"{t}EM11", "34567"),         # 1ª-4ª + não seriada
        ("medio", "matriculas"): _seq(f"{t}EM11", "89ABC"),
    }


# matrículas por sexo (turno 'todos')
SEXO = {
    "pre": (_seq("VPE41", "123"), _seq("VPE42", "123")),
    "fund_ini": (["VPE414"] + _seq("VEF41", "1234"), ["VPE424"] + _seq("VEF42", "1234")),
    "fund_fin": (_seq("VEF41", "5678"), _seq("VEF42", "5678")),
    "medio": (_seq("VEM41", "12345"), _seq("VEM42", "12345")),
}

# rendimento do fundamental (sem turno) e do médio (por turno)
REND_FUND = {
    "fund_ini": {"abandono": _seq("VEF84", "1234"), "aprovados": _seq("VEF85", "1234"),
                 "reprovados": _seq("VEF86", "1234")},
    "fund_fin": {"abandono": _seq("VEF84", "5678"), "aprovados": _seq("VEF85", "5678"),
                 "reprovados": _seq("VEF86", "5678")},
}
REND_MEDIO = {
    "diurno": {"abandono": _seq("VEM94", "1234"), "aprovados": _seq("VEM95", "1234"),
               "reprovados": _seq("VEM96", "1234")},
    "noturno": {"abandono": _seq("VEM94", "5678"), "aprovados": _seq("VEM95", "5678"),
                "reprovados": _seq("VEM96", "5678")},
}

# professores: etapa -> prefixos (o 1º prefixo é "com capacitação/curso específico" no pré e no especial)
PROF_PREFIXOS = {
    "pre": ["VDG12", "VDG13"],
    "fund_ini": ["VDG14", "VDG15"],        # CA + 1ª a 4ª (atenção: pode contar 2x o mesmo professor)
    "fund_fin": ["VDG16"],
    "medio": ["VDG17"],
    "especial": ["VDG18", "VDG19"],
    "supletivo_s1": ["VDG1A"],             # 1º e 2º semestre ficam separados (não somam)
    "supletivo_s2": ["VDG1B"],
}
PROF_COM_SEM = {"pre", "especial"}
# dígito final = formação: 1 fund. incompleto, 2 fund. completo, 3 magistério, 4 outra (médio),
# 5 licenciatura, 6 e 7 superior sem licenciatura
PROF_FORMACAO = {
    "prof_ate_fundamental": "12", "prof_medio_magisterio": "3", "prof_medio_outro": "4",
    "prof_superior_licenciatura": "5", "prof_superior_sem_lic": "67",
}

# PNE por modalidade de atendimento (índice final da VEE11x/VEE12x) e por deficiência
PNE_MODALIDADES = {
    "1": "todas", "2": "sala_recursos", "3": "oficina_pedagogica", "4": "oficina_protegida",
    "5": "classe_comum_apoio", "6": "classe_especial", "7": "apoio_psicopedagogico",
    "8": "educacao_precoce", "9": "escola_empresa", "A": "atend_hospitalar",
    "B": "atend_domiciliar", "C": "outras",
}
PNE_DEFICIENCIAS = {"1": "visao", "2": "audicao", "3": "fisica", "4": "mental", "5": "multipla"}
PNE_ETAPAS = {"1": "pre", "2": "fund_total", "3": "medio"}

COLS_TEXTO = ["MASCARA", "CODMUNIC", "UF", "SIGLA", "MUNIC", "DEP", "LOC", "CODFUNC", "REG_CNAS",
              "NIVELPRE", "NIVELCA", "NIV_F1A4_8", "NIV_F5A8_8", "NIVELMEDIO",
              "ENSREGULAR", "ENSESPECIAL", "ENSSUPLET"]
COLS_INFRA_ORIGEM = {"PERMANEN": "salas_existentes_perm", "PROVISOR": "salas_existentes_prov",
                     "NOESTAB": "salas_utilizadas_no_estab", "FORAESTA": "salas_utilizadas_fora"}


def variaveis_numericas() -> list[str]:
    """Todas as variáveis numéricas que o script consome (para ler só elas)."""
    v: set[str] = {"FUNCION", *COLS_INFRA_ORIGEM}
    for t in "DN":
        for cols in mapa_turno(t).values():
            v.update(cols)
    for m, f in SEXO.values():
        v.update(m)
        v.update(f)
    for d in REND_FUND.values():
        for cols in d.values():
            v.update(cols)
    for turno in REND_MEDIO.values():
        for cols in turno.values():
            v.update(cols)
    for prefixos in PROF_PREFIXOS.values():
        for p in prefixos:
            v.update(f"{p}{d}" for d in "1234567")
    for s in PNE_MODALIDADES:
        v.update([f"VEE11{s}", f"VEE12{s}"])
    for e in PNE_ETAPAS:
        for d in PNE_DEFICIENCIAS:
            v.add(f"VEE6{e}{d}")
    return sorted(v)


# ---------------------------------------------------------------------------
# 3. Leitura e limpeza
# ---------------------------------------------------------------------------
def localizar_entrada(caminho: Path) -> Path:
    """Aceita o arquivo (com ou sem extensão) ou uma pasta onde procurá-lo."""
    if caminho.is_file():
        return caminho
    for ext in (".CSV", ".csv", ".Csv", ".txt", ".TXT"):          # 'CENSOESC_1996' sem extensão
        if caminho.with_name(caminho.name + ext).is_file():
            return caminho.with_name(caminho.name + ext)
    pasta = caminho if caminho.is_dir() else caminho.parent
    if pasta.is_dir():
        achados = sorted(p for p in pasta.rglob("*") if p.is_file() and p.stem.lower() == NOME_BASE)
        if achados:
            return achados[0]
    raise SystemExit(f"CENSOESC_1996 não encontrado em {caminho}. Passe o caminho em --entrada.")


def _limpar_nome(c: str) -> str:
    return c.strip().lstrip("\ufeff").replace("ï»¿", "")


def validar_cabecalho(caminho: Path, encoding: str, precisa: set[str]) -> None:
    cab = {_limpar_nome(c) for c in pd.read_csv(caminho, sep=SEP_ENTRADA, encoding=encoding,
                                                encoding_errors="replace", nrows=0).columns}
    faltam = sorted(precisa - cab)
    if faltam:
        raise SystemExit(f"Colunas ausentes no CSV de 1996 ({len(faltam)}): {faltam[:15]}...")


def texto(s: pd.Series) -> pd.Series:
    """strip + vazio -> NA."""
    s = s.astype("string").str.strip()
    return s.mask(s == "")


def numero(df: pd.DataFrame, colunas: Iterable[str], rejeitados: dict[str, int]) -> pd.DataFrame:
    """Converte colunas para float (vazio -> NaN); conta valores não vazios que não são número."""
    out = {}
    for c in colunas:
        bruto = texto(df[c])
        num = pd.to_numeric(bruto, errors="coerce")
        ruim = int((bruto.notna() & num.isna()).sum())
        if ruim:
            rejeitados[c] += ruim
        out[c] = num.astype("float64")
    return pd.DataFrame(out, index=df.index)


def flag(s: pd.Series) -> pd.Series:
    """'s'/'S' -> True (o arquivo usa s/n)."""
    return s.astype("string").str.strip().str.lower().eq("s").fillna(False).astype(bool)


def soma(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Soma por linha; se TODAS as colunas são vazias o resultado fica vazio (≠ zero)."""
    return df[cols].sum(axis=1, min_count=1)


# ---------------------------------------------------------------------------
# 4. Construção das tabelas de um bloco (chunk)
# ---------------------------------------------------------------------------
def montar_ie(df: pd.DataFrame, num: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    letras = pd.DataFrame({
        "P": flag(df["NIVELPRE"]),
        "I": flag(df["NIVELCA"]) | flag(df["NIV_F1A4_8"]),
        "F": flag(df["NIV_F5A8_8"]),
        "M": flag(df["NIVELMEDIO"]),
    })
    niveis = letras.apply(lambda r: "".join(k for k, v in r.items() if v), axis=1)
    mod = pd.DataFrame({"R": flag(df["ENSREGULAR"]), "E": flag(df["ENSESPECIAL"]), "S": flag(df["ENSSUPLET"])})
    modalidades = mod.apply(lambda r: "".join(k for k, v in r.items() if v), axis=1)
    out = pd.DataFrame({
        "ano": ANO,
        "id_ie": df["id_ie"],
        "id_estavel": 0,
        "cod_municipio": texto(df["CODMUNIC"]),
        "sigla_uf": texto(df["SIGLA"]).str.upper(),
        "dependencia": texto(df["DEP"]),
        "localizacao": texto(df["LOC"]),
        "cond_funcionamento": texto(df["CODFUNC"]),
        "reg_cnas": texto(df["REG_CNAS"]),
        "niveis": niveis.replace("", pd.NA),
        "modalidades": modalidades.replace("", pd.NA),
        "funcionarios": num["FUNCION"],
    })
    out = out.merge(extra, left_on="id_ie", right_index=True, how="left")
    return out[COLS_IE]


def montar_infra(df: pd.DataFrame, num: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"ano": ANO, "id_ie": df["id_ie"]})
    for origem, destino in COLS_INFRA_ORIGEM.items():
        out[destino] = num[origem]
    return out[COLS_INFRA]


def montar_etapa(df: pd.DataFrame, num: pd.DataFrame, conf: dict) -> pd.DataFrame:
    """Acumula as medidas por (etapa, turno) e devolve a tabela longa do bloco."""
    acc: dict[tuple[str, str], dict[str, pd.Series]] = defaultdict(dict)

    # turmas e matrículas por turno
    for t, nome in (("D", "diurno"), ("N", "noturno")):
        for (etapa, medida), cols in mapa_turno(t).items():
            acc[(etapa, nome)][medida] = soma(num, cols)

    # matrículas por sexo (turno 'todos')
    for etapa, (masc, fem) in SEXO.items():
        acc[(etapa, "todos")]["matriculas_masc"] = soma(num, masc)
        acc[(etapa, "todos")]["matriculas_fem"] = soma(num, fem)

    # rendimento (a referência é o próprio ano de 1996)
    for etapa, d in REND_FUND.items():
        for medida, cols in d.items():
            acc[(etapa, "todos")][medida] = soma(num, cols)
    for turno, d in REND_MEDIO.items():
        for medida, cols in d.items():
            acc[("medio", turno)][medida] = soma(num, cols)

    # professores (turno 'todos')
    for etapa, prefixos in PROF_PREFIXOS.items():
        todas = [f"{p}{d}" for p in prefixos for d in "1234567"]
        acc[(etapa, "todos")]["prof_total"] = soma(num, todas)
        for medida, digitos in PROF_FORMACAO.items():
            acc[(etapa, "todos")][medida] = soma(num, [f"{p}{d}" for p in prefixos for d in digitos])
        if etapa in PROF_COM_SEM:
            acc[(etapa, "todos")]["prof_com_capacitacao"] = soma(num, [f"{prefixos[0]}{d}" for d in "1234567"])
            acc[(etapa, "todos")]["prof_sem_capacitacao"] = soma(num, [f"{prefixos[1]}{d}" for d in "1234567"])

    # validação: matrículas D+N deveria ser igual a M+F
    for etapa in SEXO:
        d = acc[(etapa, "diurno")]["matriculas"]
        n = acc[(etapa, "noturno")]["matriculas"]
        por_turno = pd.concat([d, n], axis=1).sum(axis=1, min_count=1)
        por_sexo = pd.concat([acc[(etapa, "todos")]["matriculas_masc"],
                              acc[(etapa, "todos")]["matriculas_fem"]], axis=1).sum(axis=1, min_count=1)
        ambos = por_turno.notna() & por_sexo.notna()
        conf[etapa]["comparaveis"] += int(ambos.sum())
        conf[etapa]["iguais"] += int((por_turno[ambos] == por_sexo[ambos]).sum())

    partes = []
    for (etapa, turno), medidas in acc.items():
        bloco = pd.DataFrame(medidas)
        bloco = bloco.dropna(how="all")
        if bloco.empty:
            continue
        bloco.insert(0, "id_ie", df.loc[bloco.index, "id_ie"])
        bloco.insert(0, "turno", turno)
        bloco.insert(0, "etapa", etapa)
        bloco.insert(0, "ano", ANO)
        if {"abandono", "aprovados", "reprovados"} & set(medidas):
            tem = bloco[[c for c in ("abandono", "aprovados", "reprovados") if c in bloco]].notna().any(axis=1)
            bloco["ano_referencia_rendimento"] = pd.Series(ANO, index=bloco.index).where(tem)
        partes.append(bloco)
    if not partes:
        return pd.DataFrame(columns=COLS_ETAPA)
    out = pd.concat(partes, ignore_index=True)
    for c in MEDIDAS_ETAPA:
        if c not in out:
            out[c] = pd.NA
    return out[COLS_ETAPA]


def montar_pne(df: pd.DataFrame, num: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    mods = []
    for s, nome in PNE_MODALIDADES.items():
        b = pd.DataFrame({"turmas": num[f"VEE11{s}"], "alunos": num[f"VEE12{s}"]}).dropna(how="all")
        b.insert(0, "modalidade", nome)
        b.insert(0, "id_ie", df.loc[b.index, "id_ie"])
        b.insert(0, "ano", ANO)
        mods.append(b)
    defs = []
    for e, etapa in PNE_ETAPAS.items():
        for d, nome in PNE_DEFICIENCIAS.items():
            b = pd.DataFrame({"alunos": num[f"VEE6{e}{d}"]}).dropna(how="all")
            b.insert(0, "deficiencia", nome)
            b.insert(0, "etapa", etapa)
            b.insert(0, "id_ie", df.loc[b.index, "id_ie"])
            b.insert(0, "ano", ANO)
            defs.append(b)
    mod = pd.concat(mods, ignore_index=True)[COLS_PNE_MOD]
    de = pd.concat(defs, ignore_index=True)[COLS_PNE_DEF]
    return mod, de


# ---------------------------------------------------------------------------
# 4b. EM8, EM11, ES6 (grão escola x curso) agregados por escola
# ---------------------------------------------------------------------------
ARQUIVO_EM8, ARQUIVO_EM11, ARQUIVO_ES6 = "em8_1996", "em11_1996", "es6_1996"
EM8_MATRICULAS = [f"EM81{i}" for i in "45678"]        # 1ª-4ª série + não seriado (matrícula de 1996)
EM11_CONCLUINTES = [f"EM111{i}" for i in "12345"]     # 5 coortes somadas (concluintes de 1995)
ES6_SEMESTRES = {1: ("ES611", "ES612"), 2: ("ES613", "ES614")}   # (matriculados, concluintes) - tudo de 1995


def localizar_arquivo_curso(pasta: Path, stem: str) -> Path | None:
    achados = sorted(p for p in pasta.rglob("*") if p.is_file() and p.stem.lower() == stem)
    return achados[0] if achados else None


def ler_curso(pasta: Path, stem: str, variaveis: list[str], encoding: str,
              rejeitados: dict[str, int]) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Devolve (chaves, números) alinhados; chaves = id_ie, cod_curso. None se o arquivo não existe."""
    caminho = localizar_arquivo_curso(pasta, stem)
    if caminho is None:
        logger.warning("%s não encontrado em %s -> arquivo ignorado", stem.upper(), pasta)
        return None
    precisa = {"MASCARA", "CODCURSO", *variaveis}
    cab = {_limpar_nome(c) for c in pd.read_csv(caminho, sep=SEP_ENTRADA, encoding=encoding,
                                                encoding_errors="replace", nrows=0).columns}
    if precisa - cab:
        logger.warning("%s: colunas ausentes %s -> arquivo ignorado", caminho.name, sorted(precisa - cab))
        return None
    df = pd.read_csv(caminho, sep=SEP_ENTRADA, encoding=encoding, encoding_errors="replace", dtype=str,
                     usecols=lambda c: _limpar_nome(c) in precisa)
    df.columns = [_limpar_nome(c) for c in df.columns]
    masc = texto(df["MASCARA"])
    df = df[masc.notna()].reset_index(drop=True)
    chaves = pd.DataFrame({"id_ie": f"MASC{ANO}_" + texto(df["MASCARA"]), "cod_curso": texto(df["CODCURSO"])})
    logger.info("%s: %d linhas", caminho.name, len(df))
    return chaves, numero(df, variaveis, rejeitados)


def por_escola(chaves: pd.DataFrame, valores: pd.Series, nome: str) -> pd.Series:
    """Soma o valor de todos os cursos da escola (vazio se nenhum curso informou)."""
    return valores.groupby(chaves["id_ie"]).sum(min_count=1).rename(nome)


def montar_extra_escola(pasta: Path, encoding: str, rejeitados: dict[str, int]) -> pd.DataFrame:
    """Colunas extras de `escola` (índice = id_ie), vindas dos arquivos de curso."""
    series: list[pd.Series] = []
    cursos: list[pd.DataFrame] = []

    em8 = ler_curso(pasta, ARQUIVO_EM8, EM8_MATRICULAS, encoding, rejeitados)
    if em8:
        ch, nu = em8
        series.append(por_escola(ch, soma(nu, EM8_MATRICULAS), "tec_matriculas"))
        cursos.append(ch.dropna(subset=["cod_curso"]))
    em11 = ler_curso(pasta, ARQUIVO_EM11, EM11_CONCLUINTES, encoding, rejeitados)
    if em11:
        ch, nu = em11
        series.append(por_escola(ch, soma(nu, EM11_CONCLUINTES), "tec_concluintes_ant"))
        cursos.append(ch.dropna(subset=["cod_curso"]))
    if cursos:                                     # nº de cursos técnicos distintos da escola (EM8 ∪ EM11)
        todos = pd.concat(cursos, ignore_index=True).drop_duplicates()
        series.append(todos.groupby("id_ie").size().rename("tec_cursos"))

    es6 = ler_curso(pasta, ARQUIVO_ES6, [v for par in ES6_SEMESTRES.values() for v in par], encoding, rejeitados)
    if es6:
        ch, nu = es6
        for sem, (vm, vc) in ES6_SEMESTRES.items():
            series.append(por_escola(ch, nu[vm], f"supl_prof_matriculas_s{sem}_ant"))
            series.append(por_escola(ch, nu[vc], f"supl_prof_concluintes_s{sem}_ant"))

    extra = pd.concat(series, axis=1) if series else pd.DataFrame(index=pd.Index([], name="id_ie"))
    extra.index.name = "id_ie"
    return extra.reindex(columns=COLS_EXTRA_ESCOLA)


# ---------------------------------------------------------------------------
# 5. Escrita segura (arquivo temporário -> rename no final)
# ---------------------------------------------------------------------------
class Escritor:
    def __init__(self, pasta: Path, nome: str, colunas: list[str], sep: str):
        self.final = pasta / f"{nome}.csv"
        self.tmp = pasta / f"{nome}.csv.tmp"
        self.colunas = colunas
        self.sep = sep
        self.linhas = 0
        self._cabecalho = True
        if self.tmp.exists():
            self.tmp.unlink()

    def escrever(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        df = df[self.colunas].copy()
        for c in df.columns:
            if c not in ("ano",) and pd.api.types.is_float_dtype(df[c]):
                df[c] = df[c].round().astype("Int64")
        df.to_csv(self.tmp, mode="a", header=self._cabecalho, index=False, sep=self.sep, encoding="utf-8")
        self._cabecalho = False
        self.linhas += len(df)

    def fechar(self) -> None:
        if self._cabecalho:                       # nada foi escrito: grava só o cabeçalho
            pd.DataFrame(columns=self.colunas).to_csv(self.tmp, index=False, sep=self.sep, encoding="utf-8")
        os.replace(self.tmp, self.final)


# ---------------------------------------------------------------------------
# 6. Dimensões
# ---------------------------------------------------------------------------
def moda(s: pd.Series) -> str | None:
    s = s.dropna()
    return None if s.empty else str(s.value_counts().index[0])


def gravar_dimensoes(pares_mun: list[pd.DataFrame], pasta: Path, sep: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """pares_mun: contagens (cod_municipio, nome_municipio, sigla_uf, uf_nome, n) acumuladas por chunk."""
    todos = pd.concat(pares_mun, ignore_index=True)
    cont = todos.groupby(["cod_municipio", "nome_municipio", "sigla_uf", "nome_uf"], dropna=False, as_index=False)["n"].sum()

    # município: um registro por cod_municipio (o mais frequente); avisa se há conflito
    por_cod = cont.sort_values("n", ascending=False)
    conflito_uf = cont.groupby("cod_municipio")["sigla_uf"].nunique()
    conflito_nome = cont.groupby("cod_municipio")["nome_municipio"].nunique()
    if (conflito_uf > 1).any():
        logger.warning("%d CODMUNIC aparecem em mais de uma UF -> CODMUNIC é código LOCAL; a chave de "
                       "municipio precisa ser (sigla_uf, cod_municipio). Exemplos: %s",
                       int((conflito_uf > 1).sum()), list(conflito_uf[conflito_uf > 1].index[:5]))
    if (conflito_nome > 1).any():
        logger.warning("%d CODMUNIC com mais de um nome de município (mantido o mais frequente). Exemplos: %s",
                       int((conflito_nome > 1).sum()), list(conflito_nome[conflito_nome > 1].index[:5]))
    mun = (por_cod.drop_duplicates("cod_municipio")[["cod_municipio", "nome_municipio", "sigla_uf"]]
           .sort_values("cod_municipio").reset_index(drop=True))

    # estado: um registro por sigla; nome mais frequente
    est = (cont.groupby("sigla_uf")["nome_uf"].apply(moda).reset_index())
    est["co_uf"] = est["sigla_uf"].map(CO_UF_IBGE).astype("Int64")
    desconhecidas = est.loc[est["co_uf"].isna(), "sigla_uf"].tolist()
    if desconhecidas:
        logger.warning("Siglas sem código IBGE conhecido: %s", desconhecidas)
    est = est[COLS_ESTADO].sort_values("sigla_uf").reset_index(drop=True)

    est.to_csv(pasta / "estado.csv", index=False, sep=sep, encoding="utf-8")
    mun.to_csv(pasta / "municipio.csv", index=False, sep=sep, encoding="utf-8")
    pd.DataFrame(DIM_ETAPA, columns=["etapa", "descricao", "etapa_ampla"]).to_csv(
        pasta / "etapa.csv", index=False, sep=sep, encoding="utf-8")
    return est, mun


# ---------------------------------------------------------------------------
# 7. Execução
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entrada", type=Path, default=ENTRADA_PADRAO,
                    help="arquivo CENSOESC_1996 (com ou sem extensão) ou pasta (padrão: %(default)s)")
    ap.add_argument("--saida", type=Path, default=None,
                    help="pasta de saída (padrão: a mesma pasta do arquivo de entrada, ex.: ...\\1996)")
    ap.add_argument("--encoding", default="utf-8-sig")
    ap.add_argument("--sep-saida", default=",")
    ap.add_argument("--chunk", type=int, default=50_000, help="linhas por bloco de leitura")
    ap.add_argument("--so-dimensoes", action="store_true", help="gera apenas estado, municipio e etapa")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    entrada = localizar_entrada(args.entrada)
    pasta = args.saida or entrada.parent
    pasta.mkdir(parents=True, exist_ok=True)
    logger.info("Entrada: %s", entrada)
    logger.info("Saída  : %s", pasta)

    numericas = variaveis_numericas()
    precisa = set(COLS_TEXTO) | set(numericas)
    validar_cabecalho(entrada, args.encoding, precisa)

    escritores = {} if args.so_dimensoes else {
        "escola": Escritor(pasta, "escola", COLS_IE, args.sep_saida),
        "infraestrutura": Escritor(pasta, "infraestrutura", COLS_INFRA, args.sep_saida),
        "escola_etapa": Escritor(pasta, "escola_etapa", COLS_ETAPA, args.sep_saida),
        "pne_modalidade": Escritor(pasta, "pne_modalidade", COLS_PNE_MOD, args.sep_saida),
        "pne_deficiencia": Escritor(pasta, "pne_deficiencia", COLS_PNE_DEF, args.sep_saida),
    }

    vistos: set[str] = set()
    pares_mun: list[pd.DataFrame] = []
    rejeitados: dict[str, int] = defaultdict(int)
    conf = {e: {"comparaveis": 0, "iguais": 0} for e in SEXO}
    lidas = sem_chave = duplicadas = sem_municipio = 0
    t0 = time.perf_counter()
    extra = pd.DataFrame(index=pd.Index([], name="id_ie"), columns=COLS_EXTRA_ESCOLA)
    if not args.so_dimensoes:
        extra = montar_extra_escola(pasta, args.encoding, rejeitados)

    leitor = pd.read_csv(entrada, sep=SEP_ENTRADA, encoding=args.encoding, encoding_errors="replace",
                         dtype=str, usecols=lambda c: _limpar_nome(c) in precisa, chunksize=args.chunk,
                         low_memory=False)
    for chunk in leitor:
        chunk.columns = [_limpar_nome(c) for c in chunk.columns]
        lidas += len(chunk)

        masc = texto(chunk["MASCARA"])
        sem_chave += int(masc.isna().sum())
        chunk = chunk[masc.notna()].copy()
        chunk["id_ie"] = f"MASC{ANO}_" + masc[masc.notna()]

        repetida = chunk["id_ie"].duplicated() | chunk["id_ie"].isin(vistos)
        duplicadas += int(repetida.sum())
        chunk = chunk[~repetida]
        vistos.update(chunk["id_ie"])

        cod = texto(chunk["CODMUNIC"])
        sem_municipio += int(cod.isna().sum())
        chunk = chunk[cod.notna()].reset_index(drop=True)
        if chunk.empty:
            continue

        # dimensões (contagem por combinação, para escolher o valor mais frequente no fim)
        pares = pd.DataFrame({
            "cod_municipio": texto(chunk["CODMUNIC"]), "nome_municipio": texto(chunk["MUNIC"]),
            "sigla_uf": texto(chunk["SIGLA"]).str.upper(), "nome_uf": texto(chunk["UF"]),
        })
        pares_mun.append(pares.groupby(list(pares.columns), dropna=False, as_index=False).size()
                         .rename(columns={"size": "n"}))

        if not args.so_dimensoes:
            num = numero(chunk, numericas, rejeitados)
            escritores["escola"].escrever(montar_ie(chunk, num, extra))
            escritores["infraestrutura"].escrever(montar_infra(chunk, num))
            escritores["escola_etapa"].escrever(montar_etapa(chunk, num, conf))
            mod, de = montar_pne(chunk, num)
            escritores["pne_modalidade"].escrever(mod)
            escritores["pne_deficiencia"].escrever(de)
        logger.info("%d linhas lidas (%.0f s)", lidas, time.perf_counter() - t0)

    if not pares_mun:
        raise SystemExit("Nenhuma escola válida encontrada.")
    est, mun = gravar_dimensoes(pares_mun, pasta, args.sep_saida)
    sem_escola = int((~extra.index.isin(vistos)).sum())
    if sem_escola:
        logger.warning("%d escolas dos arquivos EM8/EM11/ES6 não existem no CENSOESC e foram ignoradas", sem_escola)
    for e in escritores.values():
        e.fechar()

    # ------------------------------ relatório ------------------------------
    print("\n" + "=" * 70)
    print(f"Linhas lidas: {lidas} | sem MASCARA: {sem_chave} | MASCARA repetida: {duplicadas} | "
          f"sem CODMUNIC: {sem_municipio}")
    print(f"estado: {len(est)} | municipio: {len(mun)} | etapa: {len(DIM_ETAPA)}")
    for nome, e in escritores.items():
        print(f"{nome}: {e.linhas} linhas")
    if rejeitados:
        top = sorted(rejeitados.items(), key=lambda kv: -kv[1])[:8]
        print(f"Valores numéricos inválidos (viraram vazio): {sum(rejeitados.values())} | maiores: {top}")
    if not args.so_dimensoes:
        print("\nValidação: matrículas D+N == M+F (por etapa, entre escolas com os dois valores)")
        for etapa, c in conf.items():
            pct = 100 * c["iguais"] / c["comparaveis"] if c["comparaveis"] else float("nan")
            print(f"  {etapa:9s} {c['iguais']:>7}/{c['comparaveis']:<7} iguais ({pct:.1f}%)")
    print(f"\nArquivos em: {pasta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())