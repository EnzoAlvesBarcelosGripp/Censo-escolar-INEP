"""
Gera os CSVs do bloco 1996 (DDL 03_b1996.sql) a partir de CENSOESC_1996.CSV.

Entrada : CENSOESC_1996.CSV (delimitador '|', uma linha por escola, ~800 colunas)
          + dim/municipio.csv gerado por gerar_dim.py (para trocar CODMUNIC pelo código IBGE)
          + EM8_1996, EM11_1996 e ES6_1996 (mesma pasta do CENSOESC; colunas tec_* e supl_prof_* de escola)
Saída   : na mesma pasta do arquivo de entrada (…/1996/), um CSV por tabela do schema b1996:
            escola.csv, infraestrutura.csv, escola_etapa.csv, pne_modalidade.csv, pne_deficiencia.csv
          As dimensões (estado, município, domínios, etapas) não são geradas aqui: ver gerar_dim.py e 01_dim.sql.

Regras:
  - turnos diurno + noturno somados; a CA é uma etapa própria ('ca') e 'fund_ini' = 1ª a 4ª série;
  - dependência, situação e localização gravadas como código dos domínios (dim.*);
  - nada é descartado em silêncio: escola sem MASCARA, MASCARA repetida, CODMUNIC vazio ou sem código IBGE,
    UF diferente da do município ou valor de domínio desconhecido fazem o script PARAR e informar.

Exemplos:
  python gerar_tabelas_1996.py
  python gerar_tabelas_1996.py --entrada "D:/outro/lugar/CENSOESC_1996.CSV" --municipios "D:/dim/municipio.csv"
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
MUNICIPIOS_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/dim/municipio.csv")
NOME_BASE = "censoesc_1996"
SEP_ENTRADA = "|"

# ---------------------------------------------------------------------------
# 1. Constantes do modelo
# ---------------------------------------------------------------------------
# códigos dos domínios (iguais aos inseridos por 01_dim.sql): texto de 1996 -> código
DOM_DEPENDENCIA = {"Federal": 1, "Estadual": 2, "Municipal": 3, "Particular": 4}
DOM_SITUACAO = {"Ativo": 1, "Paralisado": 2, "Extinto": 3}
DOM_LOCALIZACAO = {"Urbana": 1, "Rural": 2}

# agregados dos arquivos EM8/EM11/ES6 (grão escola x curso), somados por escola
COLS_EXTRA_ESCOLA = ["tec_matriculas", "tec_concluintes_ant",
                     "supl_prof_matriculas_s1_ant", "supl_prof_concluintes_s1_ant",
                     "supl_prof_matriculas_s2_ant", "supl_prof_concluintes_s2_ant"]
COLS_FLAGS_ESCOLA = ["nivel_pre", "nivel_ca", "nivel_fund_ini", "nivel_fund_fin", "nivel_medio",
                     "mod_regular", "mod_especial", "mod_supletivo"]
COLS_IE = ["ano", "id_ie", "cod_municipio", "sigla_uf", "dependencia", "situacao_funcionamento", "localizacao",
           "reg_cnas"] + COLS_FLAGS_ESCOLA + ["funcionarios", "prof_total_escola"] + COLS_EXTRA_ESCOLA
COLS_INFRA = ["ano", "id_ie", "salas_existentes_perm", "salas_existentes_prov",
              "salas_utilizadas_no_estab", "salas_utilizadas_fora"]
MEDIDAS_ETAPA = ["turmas", "turmas_multisseriadas", "matriculas", "matriculas_masc", "matriculas_fem",
                 "abandono", "aprovados", "reprovados",
                 "prof_total", "prof_ate_fundamental", "prof_medio_magisterio", "prof_medio_outro",
                 "prof_superior_licenciatura", "prof_superior_sem_lic",
                 "prof_com_capacitacao", "prof_sem_capacitacao"]
COLS_ETAPA = ["ano", "id_ie", "etapa"] + MEDIDAS_ETAPA
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
        # CA é etapa própria em 1996; a soma CA + 1ª-4ª só acontece no núcleo
        ("ca", "turmas"): [f"{t}PE118"],
        ("ca", "matriculas"): [f"{t}PE11E"],
        ("fund_ini", "turmas"): _seq(f"{t}EF11", "3456"),
        ("fund_ini", "matriculas"): _seq(f"{t}EF11", "CDEF"),
        ("fund_fin", "turmas"): _seq(f"{t}EF11", "789A"),
        ("fund_fin", "matriculas"): _seq(f"{t}EF11", "GHIJ"),
        ("fund_total", "turmas_multisseriadas"): [f"{t}EF11B"],
        ("medio", "turmas"): _seq(f"{t}EM11", "34567"),         # 1ª-4ª + não seriada
        ("medio", "matriculas"): _seq(f"{t}EM11", "89ABC"),
    }


# matrículas por sexo (turno 'todos')
SEXO = {
    "pre": (_seq("VPE41", "123"), _seq("VPE42", "123")),
    "ca": (["VPE414"], ["VPE424"]),
    "fund_ini": (_seq("VEF41", "1234"), _seq("VEF42", "1234")),
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
    "ca": ["VDG14"],                       # Classe de Alfabetização
    "fund_ini": ["VDG15"],                 # 1ª a 4ª série
    "fund_fin": ["VDG16"],
    "medio": ["VDG17"],
    "especial": ["VDG18", "VDG19"],
    "supletivo_s1": ["VDG1A"],             # 1º e 2º semestre ficam separados (não somam)
    "supletivo_s2": ["VDG1B"],
}
PROF_COM_SEM = {"pre", "especial"}
PROF_ESCOLA = _seq("VDG11", "1234567")     # professores da escola (todas as formações) -> prof_total_escola
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
    v: set[str] = {"FUNCION", *COLS_INFRA_ORIGEM, *PROF_ESCOLA}
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


def numero(df: pd.DataFrame, colunas: Iterable[str]) -> pd.DataFrame:
    """Converte colunas para float (vazio -> NaN). Valor não numérico NÃO é convertido: o script para e informa."""
    out = {}
    for c in colunas:
        bruto = texto(df[c])
        num = pd.to_numeric(bruto, errors="coerce")
        ruim = bruto.notna() & num.isna()
        if ruim.any():
            raise SystemExit(f"Valor não numérico na coluna {c}: {bruto[ruim].unique()[:5].tolist()} "
                             f"({int(ruim.sum())} linhas). Nada foi alterado; corrija a origem ou decida o tratamento.")
        out[c] = num.astype("float64")
    return pd.DataFrame(out, index=df.index)


def sn(s: pd.Series, nome: str) -> pd.Series:
    """Flag do INEP: mantém 's' / 'n' como estão; vazio fica vazio. Outro valor é avisado e vira vazio."""
    low = texto(s).str.lower()
    ruim = low.notna() & ~low.isin(["s", "n"])
    if ruim.any():
        logger.warning("%s: %d valores diferentes de s/n (ex.: %s) -> ficaram vazios", nome, int(ruim.sum()),
                       low[ruim].unique()[:5].tolist())
    return low.where(~ruim)


def ou(nome: str, *series: pd.Series) -> pd.Series:
    """OR de flags s/n: 's' se alguma é 's'; senão 'n' se alguma é 'n'; senão vazio."""
    d = pd.concat([sn(x, nome) for x in series], axis=1)
    resultado = pd.Series(pd.NA, index=d.index, dtype="string")
    resultado = resultado.mask(d.eq("n").any(axis=1), "n")
    return resultado.mask(d.eq("s").any(axis=1), "s")


def codificar(s: pd.Series, dominio: dict[str, int], nome: str) -> pd.Series:
    """Texto de 1996 -> código do domínio. Vazio fica vazio; valor desconhecido faz o script parar."""
    t = texto(s)
    desconhecido = t.notna() & ~t.isin(dominio.keys())
    if desconhecido.any():
        raise SystemExit(f"{nome}: valores fora do domínio {t[desconhecido].unique()[:5].tolist()} "
                         f"({int(desconhecido.sum())} linhas). Atualize o domínio em 01_dim.sql e neste script.")
    return t.map(dominio).astype("Int64")


def soma(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Soma por linha; se TODAS as colunas são vazias o resultado fica vazio (≠ zero)."""
    return df[cols].sum(axis=1, min_count=1)


# ---------------------------------------------------------------------------
# 4. Construção das tabelas de um bloco (chunk)
# ---------------------------------------------------------------------------
def montar_ie(df: pd.DataFrame, num: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """df já traz cod_municipio (IBGE) e sigla_uf conferidos em ligar_municipio()."""
    out = pd.DataFrame({
        "ano": ANO,
        "id_ie": df["id_ie"],
        "cod_municipio": df["cod_municipio"],
        "sigla_uf": df["sigla_uf"],
        "dependencia": codificar(df["DEP"], DOM_DEPENDENCIA, "DEP"),
        "situacao_funcionamento": codificar(df["CODFUNC"], DOM_SITUACAO, "CODFUNC"),
        "localizacao": codificar(df["LOC"], DOM_LOCALIZACAO, "LOC"),
        "reg_cnas": texto(df["REG_CNAS"]),
        "nivel_pre": sn(df["NIVELPRE"], "NIVELPRE"),
        "nivel_ca": sn(df["NIVELCA"], "NIVELCA"),
        "nivel_fund_ini": sn(df["NIV_F1A4_8"], "NIV_F1A4_8"),
        "nivel_fund_fin": sn(df["NIV_F5A8_8"], "NIV_F5A8_8"),
        "nivel_medio": sn(df["NIVELMEDIO"], "NIVELMEDIO"),
        "mod_regular": sn(df["ENSREGULAR"], "ENSREGULAR"),
        "mod_especial": sn(df["ENSESPECIAL"], "ENSESPECIAL"),
        "mod_supletivo": sn(df["ENSSUPLET"], "ENSSUPLET"),
        "funcionarios": num["FUNCION"],
        "prof_total_escola": soma(num, PROF_ESCOLA),
    })
    out = out.merge(extra, left_on="id_ie", right_index=True, how="left")
    return out[COLS_IE]


def montar_infra(df: pd.DataFrame, num: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"ano": ANO, "id_ie": df["id_ie"]})
    for origem, destino in COLS_INFRA_ORIGEM.items():
        out[destino] = num[origem]
    return out[COLS_INFRA]


def montar_etapa(df: pd.DataFrame, num: pd.DataFrame, conf: dict) -> pd.DataFrame:
    """Uma linha por (escola, etapa). Os turnos são apenas SOMADOS (D + N), sem interpretar qual é qual."""
    acc: dict[str, dict[str, pd.Series]] = defaultdict(dict)

    # turmas e matrículas: soma das colunas diurnas e noturnas
    por_turno = {t: mapa_turno(t) for t in "DN"}
    for (etapa, medida) in por_turno["D"]:
        cols = por_turno["D"][(etapa, medida)] + por_turno["N"][(etapa, medida)]
        acc[etapa][medida] = soma(num, cols)

    # matrículas por sexo
    for etapa, (masc, fem) in SEXO.items():
        acc[etapa]["matriculas_masc"] = soma(num, masc)
        acc[etapa]["matriculas_fem"] = soma(num, fem)

    # rendimento (a referência é o próprio ano de 1996): fundamental já sem turno; médio soma dos dois turnos
    for etapa, d in REND_FUND.items():
        for medida, cols in d.items():
            acc[etapa][medida] = soma(num, cols)
    for medida in ("abandono", "aprovados", "reprovados"):
        acc["medio"][medida] = soma(num, [c for d in REND_MEDIO.values() for c in d[medida]])

    # professores
    for etapa, prefixos in PROF_PREFIXOS.items():
        todas = [f"{p}{d}" for p in prefixos for d in "1234567"]
        acc[etapa]["prof_total"] = soma(num, todas)
        for medida, digitos in PROF_FORMACAO.items():
            acc[etapa][medida] = soma(num, [f"{p}{d}" for p in prefixos for d in digitos])
        if etapa in PROF_COM_SEM:
            acc[etapa]["prof_com_capacitacao"] = soma(num, [f"{prefixos[0]}{d}" for d in "1234567"])
            acc[etapa]["prof_sem_capacitacao"] = soma(num, [f"{prefixos[1]}{d}" for d in "1234567"])

    # validação: matrículas (D+N) deveria ser igual a M+F
    for etapa in SEXO:
        total = acc[etapa]["matriculas"]
        por_sexo = pd.concat([acc[etapa]["matriculas_masc"], acc[etapa]["matriculas_fem"]], axis=1).sum(axis=1, min_count=1)
        ambos = total.notna() & por_sexo.notna()
        conf[etapa]["comparaveis"] += int(ambos.sum())
        conf[etapa]["iguais"] += int((total[ambos] == por_sexo[ambos]).sum())

    partes = []
    for etapa, medidas in acc.items():
        bloco = pd.DataFrame(medidas).dropna(how="all")
        if bloco.empty:
            continue
        bloco.insert(0, "id_ie", df.loc[bloco.index, "id_ie"])
        bloco.insert(0, "etapa", etapa)
        bloco.insert(0, "ano", ANO)
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


def ler_curso(pasta: Path, stem: str, variaveis: list[str], encoding: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
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
    return chaves, numero(df, variaveis)


def por_escola(chaves: pd.DataFrame, valores: pd.Series, nome: str) -> pd.Series:
    """Soma o valor de todos os cursos da escola (vazio se nenhum curso informou)."""
    return valores.groupby(chaves["id_ie"]).sum(min_count=1).rename(nome)


def montar_extra_escola(pasta: Path, encoding: str) -> pd.DataFrame:
    """Colunas extras de `escola` (índice = id_ie), vindas dos arquivos de curso."""
    series: list[pd.Series] = []

    em8 = ler_curso(pasta, ARQUIVO_EM8, EM8_MATRICULAS, encoding)
    if em8:
        ch, nu = em8
        series.append(por_escola(ch, soma(nu, EM8_MATRICULAS), "tec_matriculas"))
    em11 = ler_curso(pasta, ARQUIVO_EM11, EM11_CONCLUINTES, encoding)
    if em11:
        ch, nu = em11
        series.append(por_escola(ch, soma(nu, EM11_CONCLUINTES), "tec_concluintes_ant"))

    es6 = ler_curso(pasta, ARQUIVO_ES6, [v for par in ES6_SEMESTRES.values() for v in par], encoding)
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
        df = df[self.colunas]
        # float como veio: inteiros saem sem ".0" (12), valores com decimais saem completos (12.5)
        df.to_csv(self.tmp, mode="a", header=self._cabecalho, index=False, sep=self.sep, encoding="utf-8",
                  float_format="%.15g")
        self._cabecalho = False
        self.linhas += len(df)

    def fechar(self) -> None:
        if self._cabecalho:                       # nada foi escrito: grava só o cabeçalho
            pd.DataFrame(columns=self.colunas).to_csv(self.tmp, index=False, sep=self.sep, encoding="utf-8")
        os.replace(self.tmp, self.final)


# ---------------------------------------------------------------------------
# 6. Municípios (dim/municipio.csv de gerar_dim.py)
# ---------------------------------------------------------------------------
def carregar_municipios(caminho: Path) -> pd.DataFrame:
    """CODMUNIC -> (cod_municipio IBGE, sigla_uf)."""
    if not caminho.is_file():
        raise SystemExit(f"{caminho} não encontrado. Rode antes: python gerar_dim.py")
    d = pd.read_csv(caminho, dtype=str, encoding="utf-8")
    d = d.dropna(subset=["codmunic_inep_9606"])
    return d.set_index("codmunic_inep_9606")[["cod_municipio", "sigla_uf"]]


def ligar_municipio(chunk: pd.DataFrame, mapa: pd.DataFrame) -> pd.DataFrame:
    """Troca CODMUNIC pelo código IBGE e confere a UF. Para o script se algo não casar."""
    cod = texto(chunk["CODMUNIC"])
    if cod.isna().any():
        raise SystemExit(f"{int(cod.isna().sum())} escolas sem CODMUNIC "
                         f"(ex.: {chunk.loc[cod.isna(), 'MASCARA'].head(5).tolist()})")
    sem = ~cod.isin(mapa.index)
    if sem.any():
        raise SystemExit(f"CODMUNIC sem código IBGE em municipio.csv: {cod[sem].unique()[:10].tolist()}")
    chunk["cod_municipio"] = cod.map(mapa["cod_municipio"]).values
    chunk["sigla_uf"] = texto(chunk["SIGLA"]).str.upper().values
    dif = chunk["sigla_uf"].values != cod.map(mapa["sigla_uf"]).values
    if dif.any():
        ex = chunk.loc[dif, ["MASCARA", "CODMUNIC", "sigla_uf"]].head(5).to_dict("records")
        raise SystemExit(f"{int(dif.sum())} escolas com SIGLA diferente da UF do município (ex.: {ex})")
    return chunk


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
    ap.add_argument("--municipios", type=Path, default=MUNICIPIOS_PADRAO, help="municipio.csv de gerar_dim.py")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    entrada = localizar_entrada(args.entrada)
    pasta = args.saida or Path("C:/dados_inep/data_cen/dados_processados/Microdados_1996")
    pasta.mkdir(parents=True, exist_ok=True)
    logger.info("Entrada: %s", entrada)
    logger.info("Saída  : %s", pasta)

    numericas = variaveis_numericas()
    precisa = set(COLS_TEXTO) | set(numericas)
    validar_cabecalho(entrada, args.encoding, precisa)

    mapa_mun = carregar_municipios(args.municipios)
    escritores = {
        "escola": Escritor(pasta, "escola", COLS_IE, args.sep_saida),
        "infraestrutura": Escritor(pasta, "infraestrutura", COLS_INFRA, args.sep_saida),
        "escola_etapa": Escritor(pasta, "escola_etapa", COLS_ETAPA, args.sep_saida),
        "pne_modalidade": Escritor(pasta, "pne_modalidade", COLS_PNE_MOD, args.sep_saida),
        "pne_deficiencia": Escritor(pasta, "pne_deficiencia", COLS_PNE_DEF, args.sep_saida),
    }

    vistos: set[str] = set()
    conf = {e: {"comparaveis": 0, "iguais": 0} for e in SEXO}
    lidas = 0
    t0 = time.perf_counter()
    extra = montar_extra_escola(entrada.parent, args.encoding)

    leitor = pd.read_csv(entrada, sep=SEP_ENTRADA, encoding=args.encoding, encoding_errors="replace",
                         dtype=str, usecols=lambda c: _limpar_nome(c) in precisa, chunksize=args.chunk,
                         low_memory=False)
    for chunk in leitor:
        chunk.columns = [_limpar_nome(c) for c in chunk.columns]
        lidas += len(chunk)

        masc = texto(chunk["MASCARA"])
        if masc.isna().any():
            raise SystemExit(f"{int(masc.isna().sum())} escolas sem MASCARA perto da linha {lidas}")
        chunk["id_ie"] = f"MASC{ANO}_" + masc
        repetida = chunk["id_ie"].duplicated() | chunk["id_ie"].isin(vistos)
        if repetida.any():
            raise SystemExit(f"MASCARA repetida: {chunk.loc[repetida, 'MASCARA'].head(5).tolist()}")
        vistos.update(chunk["id_ie"])
        chunk = ligar_municipio(chunk.reset_index(drop=True), mapa_mun)

        num = numero(chunk, numericas)
        escritores["escola"].escrever(montar_ie(chunk, num, extra))
        escritores["infraestrutura"].escrever(montar_infra(chunk, num))
        escritores["escola_etapa"].escrever(montar_etapa(chunk, num, conf))
        mod, de = montar_pne(chunk, num)
        escritores["pne_modalidade"].escrever(mod)
        escritores["pne_deficiencia"].escrever(de)
        logger.info("%d linhas lidas (%.0f s)", lidas, time.perf_counter() - t0)

    if not vistos:
        raise SystemExit("Nenhuma escola encontrada.")
    sem_escola = int((~extra.index.isin(vistos)).sum())
    if sem_escola:
        logger.warning("%d escolas dos arquivos EM8/EM11/ES6 não existem no CENSOESC e foram ignoradas", sem_escola)
    for e in escritores.values():
        e.fechar()

    # ------------------------------ relatório ------------------------------
    print("\n" + "=" * 70)
    print(f"Escolas lidas: {lidas} (todas com MASCARA única e município IBGE)")
    for nome, e in escritores.items():
        print(f"{nome}: {e.linhas} linhas")
    if conf:
        print("\nValidação: matrículas (D+N somados) == M+F (por etapa, entre escolas com os dois valores)")
        for etapa, c in conf.items():
            pct = 100 * c["iguais"] / c["comparaveis"] if c["comparaveis"] else float("nan")
            print(f"  {etapa:9s} {c['iguais']:>7}/{c['comparaveis']:<7} iguais ({pct:.1f}%)")
    print(f"\nArquivos em: {pasta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())