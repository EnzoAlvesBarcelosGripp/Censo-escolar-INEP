"""
Gera os CSVs do bloco 1997-2006 (DDL 04_b9706.sql), um ano por vez, a partir de CENSOESC_<ano>.CSV.

Entrada : <raiz>/<ano>/CENSOESC_<ano>.CSV (delimitador '|')
          + dicionario_unificado.csv (descrições do Leia-me: é delas que sai o mapa variável -> coluna)
          + dim/municipio.csv de gerar_dim.py (CODMUNIC -> código IBGE)
          + EDUCPROF_<ano> da mesma pasta (só escola.tec_matriculas; 1998 e 2004-06, ver ANOS_TECNICO)
Saída   : <saida>/<ano>/escola.csv, infraestrutura.csv, escola_etapa.csv
          + <saida>/<ano>/mapa_variaveis.csv  (cada variável do CSV usada: tabela, etapa, coluna e regra)
          pne_modalidade / pne_deficiencia ainda NÃO são geradas (o contexto dos blocos VEE está só nos
          títulos do Leia-me, como era com os professores; falta a verificação).

Como funciona (duas fases por ano):
  1. MAPA: lê o cabeçalho do CSV e o dicionário do ano e decide, variável por variável, para onde ela vai
     (por família do nome + descrição). Nada é lido dos dados nesta fase: dá para auditar com --so-mapa.
  2. CARGA: lê só as colunas do mapa em blocos e SOMA por (escola, etapa, coluna). Nada é corrigido.

Regras (decididas na verificação):
  - turnos diurno + noturno somados; CA dentro de fund_ini; fundamental de 8 e 9 anos somados
    (1ª-4ª série e 1º-5º ano em fund_ini; 5ª-8ª série e 6º-9º ano em fund_fin);
  - 1997: creche, pré e CA só pela coluna D (na origem D = N); 1998: pré-escola pela soma por sexo;
  - sexo: cores somadas quando só há sexo x cor; coortes ("nascidos"), concluintes, reclassificados,
    repetentes, convênios e "matrícula inicial/final" ficam fora;
  - rendimento (aprovados com + sem dependência): como vier em cada ano (2003-06 = ano anterior);
  - EJA: matrícula por turno; sem turno, pelas coortes; sem as duas (1997), pelo total presencial;
    alfabetização em eja_fund_ai; semipresencial (VES12 / "semi-presencial") e preparatório para exames
    (VES9) em colunas próprias; sexo só do presencial;
  - professores: total = VDG1C* do INEP (não é a soma dos blocos); formação = soma das 7 faixas dos blocos
    da etapa (o mesmo professor pode estar em 2 blocos); com/sem capacitação nos pares pré (2/3), creche
    (E/F) e especial (8/9, "curso específico"); capacitação NEE = VDG1R* (2005-06); auxiliares (N-Q) e o
    total da classe de alfabetização (VDG1C2) ficam fora;
  - nada é descartado em silêncio: MASCARA vazia/repetida, município sem código IBGE, UF divergente,
    domínio desconhecido, valor não numérico ou bloco de professor desconhecido PARAM o script.

Exemplos:
  python gerar_tabelas_9706.py                         # 1997 a 2006
  python gerar_tabelas_9706.py --ano-inicio 2004 --ano-fim 2004
  python gerar_tabelas_9706.py --so-mapa               # só o mapa_variaveis.csv de cada ano (rápido)
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger("tabelas_9706")

RAIZ_PADRAO = Path("C:/dados_inep/data_cen/Microdados da Educação Básica")
DIC_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/dicionario_unificado.csv")
MUNICIPIOS_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/dim/municipio.csv")
SAIDA_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/Microdados_9706")
SEP_ENTRADA = "|"

# ===========================================================================
# 1. Colunas das tabelas (iguais às do DDL 04_b9706.sql)
# ===========================================================================
FLAGS_ESCOLA = {   # coluna -> nomes de variável (regex exata); OR entre as que existirem no ano
    "nivel_creche": r"NIVELCRE", "nivel_pre": r"NIVELPRE",
    "nivel_fund_ini": r"NIVELCA|NIVELALF|NIV_F1A4(_8)?|NIV_F9INI",
    "nivel_fund_fin": r"NIV_F5A8(_8)?|NIV_F9FIM",
    "ef9_anos": r"NIV_F9INI|NIV_F9FIM", "nivel_medio": r"NIVELMED|NIVELMEDIO",
    "nivel_medio_profissional": r"NIVELMEDPR|NIVMEDPR|NIVM_INT", "nivel_normal": r"NIVMEDMA",
    "nivel_eja": r"SUPL_AVA|SUPL_SAVA", "nivel_eja_exames": r"SUPL_P_EXA|SPREPEXC",
    "nivel_tecnico": r"EDPROFIS", "nivel_especial_exclusiva": r"ESP_EXCL",
    "escola_indigena": r"ED_INDIG|EDUC_INDIG", "area_quilombola": r"AREA_QUIL", "area_assentamento": r"ESC_ASSE",
    "predio_compartilhado": r"PRED_COM",
    "ocupacao_propria": r"PROPRIO", "ocupacao_alugada": r"ALUGADO", "ocupacao_cedida": r"CEDIDO",
    "local_predio_escolar": r"PRED_ESC", "local_templo": r"TEMPLO", "local_galpao": r"GALPAO",
    "local_empresa": r"EMPRESA", "local_outra_escola": r"OUTR_ESC", "local_casa_professor": r"CAS_PROF",
    "local_unidade_internacao": r"UN_INT", "local_outra_instituicao": r"OUTR_INS",
}
NUMEROS_ESCOLA = {"funcionarios": r"FUNCION", "prof_total_escola": r"PROFESS"}
COLS_ESCOLA = (["ano", "id_ie", "cod_municipio", "sigla_uf", "dependencia", "situacao_funcionamento", "localizacao"]
               + list(FLAGS_ESCOLA) + list(NUMEROS_ESCOLA) + ["tec_matriculas"])

FLAGS_INFRA = {
    "agua_rede_publica": r"AGUA_PUB", "agua_poco_cacimba_fonte": r"AGUA_ART|AGUA_CAC|AGUA_FON",
    "agua_inexistente": r"AGUA_INE",
    "energia_rede_publica": r"ENER_PUB", "energia_gerador": r"ENER_GER", "energia_renovavel": r"ENER_SOL|ENER_EOL",
    "energia_inexistente": r"ENER_INE",
    "esgoto_rede_publica": r"ESG_PUB", "esgoto_fossa": r"ESG_FOSS", "esgoto_inexistente": r"ESG_INEX",
    "lixo_coleta": r"LIXO_COL", "diretoria": r"DIRETORI", "secretaria": r"SECRETAR", "sala_professores": r"SAL_DE_P",
    "biblioteca": r"BIBLIO|BIBLIOTE", "sala_leitura": r"SALA_LEI|SAL_LEIT",
    "lab_informatica": r"LAB_INFO", "lab_ciencias": r"LAB_CIEN", "quadra_esportes": r"QUADRA|QUAD_DES|QUAD_COB",
    "cozinha": r"COZINHA", "refeitorio": r"REFEITOR", "parque_infantil": r"PARQ_INF", "bercario": r"BERCARIO",
    "sanitario_dentro_predio": r"SANI_DEN", "sanitario_fora_predio": r"SANI_FOR", "sanitario_pne": r"SANI_ESP",
    "dependencias_pne": r"DEP_ESP", "rede_local": r"REDE_LOC",
}
INTERNET = r"INTERNET"                       # 1999-2004
INTERNET_VELOCIDADES = r"CONEX_.*"           # 2005-06: 's' em alguma faixa -> internet = 's'
INTERNET_SEM = r"S_CONEX"                    # 2005-06: 's' (sem conexão) -> internet = 'n'
NUMEROS_INFRA = {   # quantidades: SOMA das variáveis do ano
    "salas_existentes_perm": r"PERMANEN", "salas_existentes_prov": r"PROVISOR",
    "salas_utilizadas_no_estab": r"NOESTAB", "salas_utilizadas_fora": r"FORAESTA",
    "n_tv": r"VTV", "n_videocassete": r"VVIDEO", "n_dvd": r"VDVD",
    "n_parabolica": r"PARABOL|ANALOGTV|DIGIT_TV|PARA_INT|PARAB_INT", "n_impressora": r"VIMPRESS|IMPRESS",
    "n_copiadora": r"VCOPIAD", "n_datashow": r"VDATASHOW|VDATSHOW",
    "n_microcomputadores": r"COMPPENT?|PC486386|COMPOUTR|COMP_OUT|COMPAPP",
}
COLS_INFRA = ["ano", "id_ie", "salas_existentes_perm", "salas_existentes_prov", "salas_utilizadas_no_estab",
              "salas_utilizadas_fora", "agua_rede_publica", "agua_poco_cacimba_fonte", "agua_inexistente",
              "energia_rede_publica", "energia_gerador", "energia_renovavel", "energia_inexistente",
              "esgoto_rede_publica", "esgoto_fossa", "esgoto_inexistente", "lixo_coleta", "diretoria", "secretaria",
              "sala_professores", "biblioteca", "sala_leitura", "lab_informatica", "lab_ciencias", "quadra_esportes",
              "cozinha", "refeitorio", "parque_infantil", "bercario", "sanitario_dentro_predio",
              "sanitario_fora_predio", "sanitario_pne", "dependencias_pne", "internet", "rede_local",
              "n_tv", "n_videocassete", "n_dvd", "n_parabolica", "n_impressora", "n_copiadora", "n_datashow",
              "n_microcomputadores"]   # ordem do DDL

MEDIDAS_ETAPA = ["turmas", "turmas_multisseriadas", "matriculas", "matriculas_masc", "matriculas_fem",
                 "matriculas_semipresencial", "matriculas_preparatorio_exames",
                 "abandono", "aprovados", "reprovados", "transferidos", "admitidos",
                 "turmas_correcao_fluxo", "matriculas_correcao_fluxo", "alunos_transporte_publico",
                 "prof_total", "prof_ate_fundamental", "prof_medio_magisterio", "prof_medio_outro",
                 "prof_superior_licenciatura", "prof_superior_sem_lic",
                 "prof_com_capacitacao", "prof_sem_capacitacao", "prof_cap_nee"]
COLS_ETAPA = ["ano", "id_ie", "etapa"] + MEDIDAS_ETAPA
ETAPAS_VALIDAS = {"creche", "pre", "infantil_unif", "fund_ini", "fund_fin", "fund_total", "medio", "normal",
                  "especial", "medio_tecnico", "eja", "eja_fund_ai", "eja_fund_af", "eja_fund", "eja_medio",
                  "eja_prof"}

# domínios (01_dim.sql); comparação sem acento/maiúscula. Valor desconhecido PARA o script.
DOM_DEPENDENCIA = {"federal": 1, "estadual": 2, "municipal": 3, "particular": 4, "privada": 4}
DOM_SITUACAO = {"ativo": 1, "ematividade": 1, "paralisado": 2, "paralisada": 2, "extinto": 3, "extinta": 3}
DOM_LOCALIZACAO = {"urbana": 1, "rural": 2}

# ===========================================================================
# 2. Professores (verificar_b9706.py, etapa 4 + Leia-me)
#    VDG1 + bloco + faixa (1-7). Totais por etapa: VDG1C<x>; capacitação NEE: VDG1R<x>.
# ===========================================================================
PROF_BLOCOS = {   # bloco -> (etapa, papel no par com/sem capacitação)
    "2": ("pre", "com"), "3": ("pre", "sem"), "E": ("creche", "com"), "F": ("creche", "sem"),
    "4": ("fund_ini", None),                      # classe de alfabetização (1998-2003): CA dentro de fund_ini
    "5": ("fund_ini", None), "L": ("fund_ini", None),   # 1ª-4ª série (8 anos) / 1ª-4ª (9 anos, 2004+)
    "6": ("fund_fin", None), "M": ("fund_fin", None),
    "7": ("medio", None), "G": ("normal", None), "D": ("medio_tecnico", None),
    "8": ("especial", "com"), "9": ("especial", "sem"),  # com / sem curso específico
    "A": ("eja", None),                           # 1998-2000: EJA sem nível
    "H": ("eja_fund_ai", None), "I": ("eja_fund_ai", None),   # alfabetização + 1ª-4ª (2001+)
    "J": ("eja_fund_af", None), "K": ("eja_medio", None),
}
PROF_BLOCOS_FORA = {"N", "O", "P", "Q"}          # auxiliares de creche e de pré-escola (2004-06): não são professores
PROF_TOTAIS = {"C1": "pre", "C3": "fund_total", "C4": "medio", "C5": "especial", "C6": "eja",
               "C7": "fund_ini", "C8": "fund_fin",          # 1997: 1ª-4ª e 5ª-8ª série
               "C9": "medio_tecnico", "CA": "creche", "CB": "normal"}
PROF_TOTAIS_FORA = {"C2"}                        # classe de alfabetização: não há linha própria (CA está em fund_ini)
PROF_NEE = {"R1": "pre", "R3": "fund_total", "R4": "medio", "R5": "especial", "R6": "eja",
            "R9": "medio_tecnico", "RA": "creche"}
PROF_FORMACAO = {"prof_ate_fundamental": "12", "prof_medio_magisterio": "3", "prof_medio_outro": "4",
                 "prof_superior_licenciatura": "5", "prof_superior_sem_lic": "67"}

# casos especiais decididos na verificação
SO_TURNO_DIURNO = {1997: {"creche", "pre", "fund_ini"}}  # família PE de 1997: D = N na origem -> só D
MATRICULA_PELO_SEXO = {(1998, "pre")}                       # 1998: turno vazio na origem


# ===========================================================================
# 3. Utilidades
# ===========================================================================
def comp(s: object) -> str:
    """minúsculas, sem acento, sem espaço/pontuação (descrições do PDF às vezes vêm coladas)."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def limpar_nome(c: str) -> str:
    return str(c).strip().lstrip("\ufeff").replace("ï»¿", "")


def achar(raiz: Path, ano: int, arquivo: str) -> Path | None:
    pasta = raiz / str(ano)
    if not pasta.is_dir():
        return None
    alvo = f"{arquivo}_{ano}".lower()
    achados = sorted(p for p in pasta.rglob("*") if p.is_file() and p.stem.lower() == alvo
                     and p.suffix.lower() in (".csv", ".txt", ""))
    return achados[0] if achados else None


def cabecalho(caminho: Path, encoding: str) -> list[str]:
    return [limpar_nome(c) for c in pd.read_csv(caminho, sep=SEP_ENTRADA, encoding=encoding,
                                                encoding_errors="replace", nrows=0).columns]


def casam(rx: str, cab: list[str]) -> list[str]:
    return [c for c in cab if re.fullmatch(rx, c)]


def texto(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip()
    return s.mask(s == "")


def numero(df: pd.DataFrame, colunas: list[str], ano: int) -> pd.DataFrame:
    """Texto -> float (vazio -> NaN). Valor não numérico NÃO é convertido: o script para e informa."""
    out = {}
    for c in colunas:
        bruto = texto(df[c])
        num = pd.to_numeric(bruto, errors="coerce")
        ruim = bruto.notna() & num.isna()
        if ruim.any():
            raise SystemExit(f"{ano}: valor não numérico na coluna {c}: {bruto[ruim].unique()[:5].tolist()} "
                             f"({int(ruim.sum())} linhas). Nada foi gravado para este ano; decida o tratamento.")
        out[c] = num.astype("float64")
    return pd.DataFrame(out, index=df.index)


def sn(s: pd.Series, nome: str) -> pd.Series:
    low = texto(s).str.lower()
    ruim = low.notna() & ~low.isin(["s", "n"])
    if ruim.any():
        logger.warning("%s: %d valores diferentes de s/n (ex.: %s) -> ficaram vazios", nome, int(ruim.sum()),
                       low[ruim].unique()[:5].tolist())
    return low.where(~ruim)


def ou(df: pd.DataFrame, cols: list[str], nome: str) -> pd.Series:
    """OR de flags: 's' se alguma é 's'; senão 'n' se alguma é 'n'; senão vazio. Sem colunas -> vazio."""
    res = pd.Series(pd.NA, index=df.index, dtype="string")
    if not cols:
        return res
    d = pd.concat([sn(df[c], f"{nome}/{c}") for c in cols], axis=1)
    res = res.mask(d.eq("n").any(axis=1), "n")
    return res.mask(d.eq("s").any(axis=1), "s")


def soma(num: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Soma por linha; se TODAS as colunas são vazias (ou não há colunas) o resultado é vazio, não zero."""
    if not cols:
        return pd.Series(float("nan"), index=num.index)
    return num[cols].sum(axis=1, min_count=1)


def codificar(s: pd.Series, dominio: dict[str, int], nome: str, ano: int) -> pd.Series:
    t = texto(s)
    chave = t.map(lambda x: comp(x) if pd.notna(x) else x)
    desconhecido = t.notna() & ~chave.isin(dominio.keys())
    if desconhecido.any():
        raise SystemExit(f"{ano} {nome}: valores fora do domínio {t[desconhecido].unique()[:5].tolist()} "
                         f"({int(desconhecido.sum())} linhas). Atualize 01_dim.sql e este script.")
    return chave.map(dominio).astype("Int64")


# ===========================================================================
# 4. MAPA de escola_etapa: variável -> (etapa, coluna, método) pela família + descrição
# ===========================================================================
@dataclass
class Regra:
    variavel: str
    etapa: str
    coluna: str
    metodo: str          # turno | sexo | sexo_cor | coorte | total | direto
    descricao: str


RE_FORA = r"nascid|concluint|reclassific|duracao|repetent|promovid|conveniad|comoestado|comomunicipio|" \
          r"matriculafinal|matriculainicial|finaldoanoletivo|naofrequent|naofreqent|frequentaram|organizacao|ciclos"
RE_COR = r"corbranca|corpreta|corparda|coramarela|racaindigena|naodeclarad|corouraca"


def serie(c: str) -> tuple[int, str] | None:
    """(número, 'serie' | 'ano'): 'série' = fundamental de 8 anos, 'ano' = de 9 anos."""
    m = re.search(r"(\d)[oa]?(serie|ano)(?!anterior)", c)
    return (int(m.group(1)), m.group(2)) if m else None


def etapa_regular(familia: str, c: str) -> str | None:
    """Etapa das famílias regulares (PE, CA, EF, E9F, EM, CN)."""
    if familia == "PE":
        if "unificad" in c:
            return "infantil_unif"
        if "creche" in c:
            return "creche"
        if "preescol" in c:
            return "pre"
        if "classedealfabetizacao" in c:
            return "fund_ini"
        return None
    if familia == "CA":
        return "fund_ini"
    if familia in ("EF", "E9F"):
        s = serie(c)
        if s is None:
            return "fund_total"
        n, tipo = s                                    # 8 anos: 1ª-4ª série | 9 anos: 1º-5º ano
        return "fund_ini" if n <= (5 if tipo == "ano" else 4) else "fund_fin"
    if familia == "EM":
        return "medio"
    if familia == "CN":
        return "normal"
    return None


def nivel_eja(c: str) -> str:
    if "profissionaliz" in c or "qualifica" in c or "aprendizagem" in c:
        return "eja_prof"
    if "ensinomedio" in c or "2ograu" in c:           # antes das séries: há descrições coladas pelo PDF
        return "eja_medio"
    if "alfabetiza" in c or re.search(r"1a?a?a?2a?serie|1aa4a|1aa2a|3aa4a|anosiniciais|[1-4]aserie", c):
        return "eja_fund_ai"
    if re.search(r"5aa8a|5aa6a|7aa8a|anosfinais|[5-8]aserie", c):
        return "eja_fund_af"
    if "medio" in c or "2ograu" in c:
        return "eja_medio"
    if "fundamental" in c or "1ograu" in c:
        return "eja_fund"
    return "eja"


def medida_rendimento(c: str) -> str | None:
    if "admitid" in c:                    # antes de "transferência": 1997 tem "admitidos por transferência"
        return "admitidos"
    if "transferencia" in c:
        return "transferidos"
    if "abandono" in c:
        return "abandono"
    if "reprovad" in c:
        return "reprovados"
    if "aprovad" in c:                    # com e sem dependência somados
        return "aprovados"
    return None


def classificar(v: str, desc: str) -> Regra | None:
    """Uma variável do CENSOESC -> regra de escola_etapa (ou None = não entra)."""
    c = comp(desc)
    m = re.match(r"^([DNV])(PE|CA|E9F|EF|EM|CN|ES|TE)", v)
    if not m:
        return None
    pre, fam = m.groups()
    tem_turno = pre in "DN"

    # ---- transporte escolar (VTE*): etapa pela descrição
    if fam == "TE":
        if not re.search(r"alunos|transporte", c):
            return None
        if "eja" in c or "jovenseadultos" in c:
            et = nivel_eja(c)
        elif "creche" in c:
            et = "creche"
        elif "preescol" in c:
            et = "pre"
        elif "classedealfabetizacao" in c:
            et = "fund_ini"
        elif "educacaoespecial" in c or "educesp" in c:
            et = "especial"
        elif "profissional" in c or "tecnico" in c:
            et = "medio_tecnico"
        elif "cursonormal" in c:
            et = "normal"
        elif "medio" in c:
            et = "medio"
        elif re.search(r"1aa4a", c):
            et = "fund_ini"
        elif re.search(r"5aa8a", c):
            et = "fund_fin"
        else:
            return None
        return Regra(v, et, "alunos_transporte_publico", "direto", desc)

    # ---- EJA (DES/NES por turno; VES por coorte, sexo, total, semipresencial, exames)
    if fam == "ES":
        if re.search(r"professor|concluint|organiza", c):
            return None
        if "turma" in c and tem_turno:
            return Regra(v, nivel_eja(c), "turmas", "turno", desc)
        if not re.search(r"matricul|alunos", c):
            return None
        if "semipresencia" in c or v.startswith("VES12"):
            return Regra(v, nivel_eja(c), "matriculas_semipresencial", "direto", desc)
        if "exame" in c or v.startswith("VES9"):
            return Regra(v, nivel_eja(c), "matriculas_preparatorio_exames", "direto", desc)
        if tem_turno:
            return Regra(v, nivel_eja(c), "matriculas", "turno", desc)
        if "nascid" in c:
            return Regra(v, nivel_eja(c), "matriculas", "coorte", desc)
        if "masculino" in c or "feminino" in c:
            col = "matriculas_masc" if "masculino" in c else "matriculas_fem"
            return Regra(v, nivel_eja(c), col, "sexo_cor" if re.search(RE_COR, c) else "sexo", desc)
        if "semestre" in c:
            return None                                   # só 1996
        return Regra(v, nivel_eja(c), "matriculas", "total", desc)

    # ---- famílias regulares
    if re.search(RE_FORA, c):
        return None
    et = etapa_regular(fam, c)

    if "turma" in c and tem_turno:
        if "multi" in c:                                   # multisseriada / multi / multiciclada
            return Regra(v, "fund_total", "turmas_multisseriadas", "turno", desc)
        if "aceleracao" in c or "correcaodefluxo" in c:
            return Regra(v, "fund_total" if fam in ("EF", "E9F") else (et or "fund_total"),
                         "turmas_correcao_fluxo", "turno", desc)
        return Regra(v, et, "turmas", "turno", desc) if et else None

    med = medida_rendimento(c)
    if med:
        return Regra(v, et, med, "direto", desc) if et else None

    if not re.search(r"matricul|alunos", c) or et is None:
        return None
    if "aceleracao" in c or "correcaodefluxo" in c:
        return Regra(v, et, "matriculas_correcao_fluxo", "direto", desc)
    if tem_turno:
        return Regra(v, et, "matriculas", "turno", desc)
    if "masculino" in c or "feminino" in c:
        col = "matriculas_masc" if "masculino" in c else "matriculas_fem"
        return Regra(v, et, col, "sexo_cor" if re.search(RE_COR, c) else "sexo", desc)
    return None                                            # totais V* sem recorte: repetem D + N


def selecionar(regras: list[Regra], ano: int) -> tuple[list[Regra], list[tuple[Regra, str]]]:
    """Escolhe, entre métodos alternativos da MESMA medida, qual entra. Devolve (usadas, descartadas+motivo)."""
    usadas, fora = [], []
    eja_turno = any(r.etapa.startswith("eja") and r.coluna == "matriculas" and r.metodo == "turno" for r in regras)
    eja_coorte = any(r.etapa.startswith("eja") and r.coluna == "matriculas" and r.metodo == "coorte" for r in regras)
    metodo_eja = "turno" if eja_turno else ("coorte" if eja_coorte else "total")
    sexo_simples = {(r.etapa, r.coluna) for r in regras if r.metodo == "sexo"}

    for r in regras:
        motivo = None
        if r.etapa.startswith("eja") and r.coluna == "matriculas" and r.metodo != metodo_eja:
            motivo = f"EJA de {ano} usa o método '{metodo_eja}'"
        elif r.metodo == "sexo_cor" and (r.etapa, r.coluna) in sexo_simples:
            motivo = "já há sexo sem cor para esta etapa"
        elif (r.metodo == "turno" and r.variavel.startswith("N") and r.variavel[1:3] == "PE"
              and r.etapa in SO_TURNO_DIURNO.get(ano, set())):
            motivo = f"{ano}: família PE só pela coluna D (D = N na origem)"
        elif r.coluna == "matriculas" and (ano, r.etapa) in MATRICULA_PELO_SEXO:
            motivo = f"{ano}: matrícula de {r.etapa} pela soma por sexo"
        (fora.append((r, motivo)) if motivo else usadas.append(r))

    # 1998 pré-escola: a matrícula total vem das variáveis de sexo
    for r in list(usadas):
        if r.coluna in ("matriculas_masc", "matriculas_fem") and (ano, r.etapa) in MATRICULA_PELO_SEXO:
            usadas.append(Regra(r.variavel, r.etapa, "matriculas", "sexo->total", r.descricao))
    return usadas, fora


# ===========================================================================
# 5. Professores, técnico, escola e infraestrutura: mapas pelo NOME da variável
# ===========================================================================
def mapa_professores(cab: list[str], ano: int) -> list[Regra]:
    regras = []
    blocos = defaultdict(list)
    for v in cab:
        m = re.fullmatch(r"VDG1([0-9A-Z])([0-9A-Z])", v)
        if not m:
            continue
        b, f = m.groups()
        if f"{b}{f}" in PROF_TOTAIS:
            regras.append(Regra(v, PROF_TOTAIS[b + f], "prof_total", "direto", "total do INEP"))
        elif f"{b}{f}" in PROF_NEE:
            regras.append(Regra(v, PROF_NEE[b + f], "prof_cap_nee", "direto", "capacitação NEE"))
        elif f"{b}{f}" in PROF_TOTAIS_FORA or b in PROF_BLOCOS_FORA:
            continue
        elif b in PROF_BLOCOS and f in "1234567":
            blocos[b].append(v)
        else:
            raise SystemExit(f"{ano}: variável de professor sem regra: {v}. Confira o Leia-me e PROF_BLOCOS.")
    por_etapa = defaultdict(list)
    for b in blocos:
        por_etapa[PROF_BLOCOS[b][0]].append(b)
    for etapa, bs in por_etapa.items():
        for coluna, faixas in PROF_FORMACAO.items():
            for b in bs:
                for v in blocos[b]:
                    if v[-1] in faixas:
                        regras.append(Regra(v, etapa, coluna, "direto", f"bloco {b} faixa {v[-1]}"))
        papeis = {PROF_BLOCOS[b][1]: b for b in bs if PROF_BLOCOS[b][1]}
        if set(papeis) == {"com", "sem"}:
            for papel, b in papeis.items():
                regras += [Regra(v, etapa, f"prof_{papel}_capacitacao", "direto", f"bloco {b}") for v in blocos[b]]
    return regras


# Anos em que o arquivo de cursos é de EDUCAÇÃO PROFISSIONAL. Conferido pelos totais: EM12 (1997-98) e
# EDUCPROF de 1999 somam o ensino médio inteiro (médio por habilitação), não o técnico -> ficam fora.
# 2000-2003 não têm arquivo de cursos.
ANOS_TECNICO = {1998, 2004, 2005, 2006}


def mapa_tecnico(dic_ano: pd.DataFrame, raiz: Path, ano: int, encoding: str) -> tuple[Path, list[str]] | None:
    """EDUCPROF do ano e as colunas de matrícula a somar (2004+: coortes diurno + noturno; 1998: total).
    Em ANOS_TECNICO, arquivo ausente ou sem colunas de matrícula PARA o script (antes passava em silêncio)."""
    if ano not in ANOS_TECNICO:
        return None
    p = achar(raiz, ano, "EDUCPROF")
    if p is None:
        raise SystemExit(f"{ano}: EDUCPROF_{ano} não encontrado em {raiz / str(ano)} "
                         f"(arquivos .csv/.txt cujo nome, sem extensão, seja EDUCPROF_{ano}).")
    cab = set(cabecalho(p, encoding))
    d = dic_ano[dic_ano.arquivo == "EDUCPROF"]
    c = {}
    for v, x in zip(d.variavel, d.descricao.map(comp)):
        # 2004: o Leia-me chama de VEP211 / DEP233, mas o CSV traz EP211 / EP233 (sem o 1º caractere)
        if v not in cab and v[:1] in ("D", "N", "V") and v[1:] in cab:
            v = v[1:]
        c[v] = x
    cols = [v for v, x in c.items() if v in cab and "matricul" in x and "nascid" in x]
    if not cols:
        cols = [v for v, x in c.items() if v in cab and "matricul" in x
                and not re.search(r"masculino|feminino|nascid", x)]
    if not cols:
        raise SystemExit(f"{ano}: {p.name} sem colunas de matrícula reconhecidas. Cabeçalho: {sorted(cab)[:20]} | "
                         f"dicionário: {sorted(c)[:20]}")
    return p, cols


# ===========================================================================
# 6. Mapa completo do ano (para auditoria e para a carga)
# ===========================================================================
@dataclass
class MapaAno:
    ano: int
    escola_flags: dict[str, list[str]]
    escola_numeros: dict[str, list[str]]
    infra_flags: dict[str, list[str]]
    infra_numeros: dict[str, list[str]]
    internet: tuple[list[str], list[str], list[str]]          # (INTERNET, velocidades, sem conexão)
    etapa: list[Regra]
    etapa_fora: list[tuple[Regra, str]]
    tecnico: tuple[Path, list[str]] | None

    def numericas(self) -> list[str]:
        v = {c for cs in self.escola_numeros.values() for c in cs}
        v |= {c for cs in self.infra_numeros.values() for c in cs}
        v |= {r.variavel for r in self.etapa}
        return sorted(v)

    def textos(self) -> list[str]:
        v = {"MASCARA", "CODMUNIC", "SIGLA", "DEP", "LOC", "CODFUNC"}
        v |= {c for cs in self.escola_flags.values() for c in cs}
        v |= {c for cs in self.infra_flags.values() for c in cs}
        v |= {c for cs in self.internet for c in cs}
        return sorted(v)

    def tabela(self) -> pd.DataFrame:
        linhas = []
        for tab, mapa in (("escola", self.escola_flags), ("escola", self.escola_numeros),
                          ("infraestrutura", self.infra_flags), ("infraestrutura", self.infra_numeros)):
            for col, vs in mapa.items():
                for v in vs:
                    linhas.append({"tabela": tab, "etapa": "", "coluna": col, "variavel": v, "metodo": "OR/soma",
                                   "usada": True, "motivo": ""})
        for nome, vs in zip(("internet", "internet (velocidade)", "internet (sem conexão)"), self.internet):
            for v in vs:
                linhas.append({"tabela": "infraestrutura", "etapa": "", "coluna": "internet", "variavel": v,
                               "metodo": nome, "usada": True, "motivo": ""})
        for r in self.etapa:
            linhas.append({"tabela": "escola_etapa", "etapa": r.etapa, "coluna": r.coluna, "variavel": r.variavel,
                           "metodo": r.metodo, "usada": True, "motivo": "", "descricao": r.descricao})
        for r, motivo in self.etapa_fora:
            linhas.append({"tabela": "escola_etapa", "etapa": r.etapa, "coluna": r.coluna, "variavel": r.variavel,
                           "metodo": r.metodo, "usada": False, "motivo": motivo, "descricao": r.descricao})
        if self.tecnico:
            for v in self.tecnico[1]:
                linhas.append({"tabela": "escola", "etapa": "", "coluna": "tec_matriculas", "variavel": v,
                               "metodo": self.tecnico[0].stem, "usada": True, "motivo": ""})
        t = pd.DataFrame(linhas)
        t.insert(0, "ano", self.ano)
        t["descricao"] = t.get("descricao", "").fillna("").map(lambda s: re.sub(r"\s+", " ", str(s))[:120])
        return t


def montar_mapa(ano: int, cab: list[str], dic_ano: pd.DataFrame, raiz: Path, encoding: str) -> MapaAno:
    desc = dic_ano[dic_ano.arquivo == "CENSOESC"].drop_duplicates("variavel").set_index("variavel").descricao
    regras = []
    for v in cab:
        if v in desc.index:
            r = classificar(v, str(desc[v]))
            if r:
                regras.append(r)
    regras += mapa_professores(cab, ano)
    invalidas = {r.etapa for r in regras} - ETAPAS_VALIDAS
    if invalidas:
        raise SystemExit(f"{ano}: etapas fora do domínio: {invalidas}")
    usadas, fora = selecionar(regras, ano)
    return MapaAno(
        ano=ano,
        escola_flags={k: casam(rx, cab) for k, rx in FLAGS_ESCOLA.items()},
        escola_numeros={k: casam(rx, cab) for k, rx in NUMEROS_ESCOLA.items()},
        infra_flags={k: casam(rx, cab) for k, rx in FLAGS_INFRA.items()},
        infra_numeros={k: casam(rx, cab) for k, rx in NUMEROS_INFRA.items()},
        internet=(casam(INTERNET, cab), casam(INTERNET_VELOCIDADES, cab), casam(INTERNET_SEM, cab)),
        etapa=usadas, etapa_fora=fora, tecnico=mapa_tecnico(dic_ano, raiz, ano, encoding))


# ===========================================================================
# 7. Construção das tabelas de um bloco de linhas
# ===========================================================================
def montar_escola(ano: int, df: pd.DataFrame, num: pd.DataFrame, mapa: MapaAno, tec: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame({
        "ano": ano, "id_ie": df["id_ie"], "cod_municipio": df["cod_municipio"], "sigla_uf": df["sigla_uf"],
        "dependencia": codificar(df["DEP"], DOM_DEPENDENCIA, "DEP", ano),
        "situacao_funcionamento": codificar(df["CODFUNC"], DOM_SITUACAO, "CODFUNC", ano),
        "localizacao": codificar(df["LOC"], DOM_LOCALIZACAO, "LOC", ano),
    })
    for col, vs in mapa.escola_flags.items():
        out[col] = ou(df, vs, col)
    for col, vs in mapa.escola_numeros.items():
        out[col] = soma(num, vs)
    out["tec_matriculas"] = df["id_ie"].map(tec)
    return out[COLS_ESCOLA]


def montar_infra(ano: int, df: pd.DataFrame, num: pd.DataFrame, mapa: MapaAno) -> pd.DataFrame:
    out = pd.DataFrame({"ano": ano, "id_ie": df["id_ie"]})
    for col, vs in mapa.infra_flags.items():
        out[col] = ou(df, vs, col)
    for col, vs in mapa.infra_numeros.items():
        out[col] = soma(num, vs)
    internet, veloc, sem = mapa.internet
    if internet:
        out["internet"] = ou(df, internet, "internet")
    else:                                            # 2005-06: pela velocidade / "sem conexão"
        tem = ou(df, veloc, "internet_velocidade").eq("s").fillna(False)
        sem_conexao = ou(df, sem, "internet_sem_conexao").eq("s").fillna(False)
        res = pd.Series(pd.NA, index=df.index, dtype="string")
        res[sem_conexao.to_numpy()] = "n"
        res[tem.to_numpy()] = "s"                    # alguma velocidade marcada vence "sem conexão"
        out["internet"] = res
    return out[COLS_INFRA]


def montar_etapa(ano: int, df: pd.DataFrame, num: pd.DataFrame, grupos: dict, conf: dict) -> pd.DataFrame:
    acc: dict[str, dict[str, pd.Series]] = defaultdict(dict)
    for (etapa, coluna), cols in grupos.items():
        acc[etapa][coluna] = soma(num, cols)
    # validação: matrículas x (masc + fem), por etapa
    for etapa, med in acc.items():
        if "matriculas" in med and "matriculas_masc" in med and (ano, etapa) not in MATRICULA_PELO_SEXO:
            t = med["matriculas"]
            s = pd.concat([med["matriculas_masc"], med.get("matriculas_fem", t * float("nan"))], axis=1) \
                .sum(axis=1, min_count=1)
            ok = t.notna() & s.notna() & ((t > 0) | (s > 0))     # 0 = 0 não prova nada
            conf[etapa]["comparaveis"] += int(ok.sum())
            conf[etapa]["iguais"] += int((t[ok] == s[ok]).sum())
    partes = []
    for etapa, medidas in acc.items():
        bloco = pd.DataFrame(medidas).dropna(how="all")
        if bloco.empty:
            continue
        bloco.insert(0, "id_ie", df.loc[bloco.index, "id_ie"].values)
        bloco.insert(0, "etapa", etapa)
        bloco.insert(0, "ano", ano)
        partes.append(bloco)
    if not partes:
        return pd.DataFrame(columns=COLS_ETAPA)
    out = pd.concat(partes, ignore_index=True)
    return out.reindex(columns=COLS_ETAPA)


# ===========================================================================
# 8. Escrita segura e municípios (iguais ao gerador de 1996)
# ===========================================================================
class Escritor:
    def __init__(self, pasta: Path, nome: str, colunas: list[str], sep: str):
        self.final = pasta / f"{nome}.csv"
        self.tmp = pasta / f"{nome}.csv.tmp"
        self.colunas, self.sep, self.linhas, self._cab = colunas, sep, 0, True
        if self.tmp.exists():
            self.tmp.unlink()

    def escrever(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        df[self.colunas].to_csv(self.tmp, mode="a", header=self._cab, index=False, sep=self.sep,
                                encoding="utf-8", float_format="%.15g")
        self._cab = False
        self.linhas += len(df)

    def fechar(self) -> None:
        if self._cab:
            pd.DataFrame(columns=self.colunas).to_csv(self.tmp, index=False, sep=self.sep, encoding="utf-8")
        os.replace(self.tmp, self.final)

    def descartar(self) -> None:
        if self.tmp.exists():
            self.tmp.unlink()


def carregar_municipios(caminho: Path) -> pd.DataFrame:
    if not caminho.is_file():
        raise SystemExit(f"{caminho} não encontrado. Rode antes: python gerar_dim.py")
    d = pd.read_csv(caminho, dtype=str, encoding="utf-8").dropna(subset=["codmunic_inep_9606"])
    return d.set_index("codmunic_inep_9606")[["cod_municipio", "sigla_uf"]]


def ligar_municipio(chunk: pd.DataFrame, mapa: pd.DataFrame, ano: int) -> pd.DataFrame:
    cod = texto(chunk["CODMUNIC"])
    if cod.isna().any():
        raise SystemExit(f"{ano}: {int(cod.isna().sum())} escolas sem CODMUNIC "
                         f"(ex.: {chunk.loc[cod.isna(), 'MASCARA'].head(5).tolist()})")
    sem = ~cod.isin(mapa.index)
    if sem.any():
        raise SystemExit(f"{ano}: CODMUNIC sem código IBGE em municipio.csv: {cod[sem].unique()[:10].tolist()}")
    chunk["cod_municipio"] = cod.map(mapa["cod_municipio"]).values
    chunk["sigla_uf"] = texto(chunk["SIGLA"]).str.upper().values
    dif = chunk["sigla_uf"].values != cod.map(mapa["sigla_uf"]).values
    if dif.any():
        ex = chunk.loc[dif, ["MASCARA", "CODMUNIC", "sigla_uf"]].head(5).to_dict("records")
        raise SystemExit(f"{ano}: {int(dif.sum())} escolas com SIGLA diferente da UF do município (ex.: {ex})")
    return chunk


def ler_tecnico(ano: int, mapa: MapaAno, encoding: str, raiz: Path) -> pd.Series:
    """tec_matriculas por escola (soma de todos os cursos do EDUCPROF)."""
    if mapa.tecnico is None:
        return pd.Series(dtype="float64")
    caminho, cols = mapa.tecnico

    def somar(p: Path, cs: list[str]) -> pd.Series:
        df = pd.read_csv(p, sep=SEP_ENTRADA, encoding=encoding, encoding_errors="replace", dtype=str,
                         usecols=lambda c: limpar_nome(c) in {"MASCARA", *cs})
        df.columns = [limpar_nome(c) for c in df.columns]
        masc = texto(df["MASCARA"])
        if masc.isna().any():
            raise SystemExit(f"{p.name}: {int(masc.isna().sum())} linhas sem MASCARA")
        return soma(numero(df, cs, ano), cs).groupby(f"MASC{ano}_" + masc).sum(min_count=1)

    tec = somar(caminho, cols)
    logger.info("%s: %d escolas com matrícula > 0, %.0f matrículas (%s)", caminho.name, int((tec > 0).sum()),
                tec.sum(), ",".join(cols))
    return tec


# ===========================================================================
# 9. Execução de um ano
# ===========================================================================
def processar_ano(ano: int, args, dic: pd.DataFrame, mapa_mun: pd.DataFrame | None) -> None:
    entrada = achar(args.raiz, ano, "CENSOESC")
    if entrada is None:
        logger.warning("%d: CENSOESC_%d não encontrado em %s -> ano pulado", ano, ano, args.raiz / str(ano))
        return
    pasta = args.saida / str(ano)
    pasta.mkdir(parents=True, exist_ok=True)
    cab = cabecalho(entrada, args.encoding)
    mapa = montar_mapa(ano, cab, dic[dic.ano == ano], args.raiz, args.encoding)
    tab = mapa.tabela()
    tab.to_csv(pasta / "mapa_variaveis.csv", index=False, encoding="utf-8-sig")
    usadas = tab[tab.usada & tab.tabela.eq("escola_etapa")]
    resumo = usadas.groupby(["etapa", "coluna"]).variavel.count().unstack(fill_value=0)
    logger.info("%d: mapa com %d variáveis (%d descartadas por regra) -> %s", ano, int(tab.usada.sum()),
                int((~tab.usada).sum()), pasta / "mapa_variaveis.csv")
    vazias = [c for c, vs in {**mapa.escola_flags, **mapa.infra_flags, **mapa.infra_numeros}.items() if not vs]
    if vazias:
        logger.info("%d: colunas sem variável neste ano (ficam NULL): %s", ano, ", ".join(vazias))
    if args.so_mapa:
        print(f"\n=== {ano}: nº de variáveis por etapa x coluna (escola_etapa) ===")
        print(resumo.to_string())
        return

    grupos = defaultdict(list)
    for r in mapa.etapa:
        grupos[(r.etapa, r.coluna)].append(r.variavel)
    numericas, textos = mapa.numericas(), mapa.textos()
    faltam = sorted({"MASCARA", "CODMUNIC", "SIGLA", "DEP", "LOC", "CODFUNC"} - set(cab))
    if faltam:
        raise SystemExit(f"{ano}: colunas de identificação ausentes: {faltam}")
    tec = ler_tecnico(ano, mapa, args.encoding, args.raiz)

    esc = {"escola": Escritor(pasta, "escola", COLS_ESCOLA, args.sep_saida),
           "infraestrutura": Escritor(pasta, "infraestrutura", COLS_INFRA, args.sep_saida),
           "escola_etapa": Escritor(pasta, "escola_etapa", COLS_ETAPA, args.sep_saida)}
    vistos: set[str] = set()
    conf = defaultdict(lambda: {"comparaveis": 0, "iguais": 0})
    lidas, t0 = 0, time.perf_counter()
    usar = set(numericas) | set(textos)
    try:
        for chunk in pd.read_csv(entrada, sep=SEP_ENTRADA, encoding=args.encoding, encoding_errors="replace",
                                 dtype=str, usecols=lambda c: limpar_nome(c) in usar, chunksize=args.chunk,
                                 low_memory=False):
            chunk.columns = [limpar_nome(c) for c in chunk.columns]
            chunk = chunk.reset_index(drop=True)
            lidas += len(chunk)
            masc = texto(chunk["MASCARA"])
            if masc.isna().any():
                raise SystemExit(f"{ano}: {int(masc.isna().sum())} escolas sem MASCARA perto da linha {lidas}")
            chunk["id_ie"] = f"MASC{ano}_" + masc
            rep = chunk["id_ie"].duplicated() | chunk["id_ie"].isin(vistos)
            if rep.any():
                raise SystemExit(f"{ano}: MASCARA repetida: {chunk.loc[rep, 'MASCARA'].head(5).tolist()}")
            vistos.update(chunk["id_ie"])
            chunk = ligar_municipio(chunk, mapa_mun, ano)
            num = numero(chunk, numericas, ano)
            esc["escola"].escrever(montar_escola(ano, chunk, num, mapa, tec))
            esc["infraestrutura"].escrever(montar_infra(ano, chunk, num, mapa))
            esc["escola_etapa"].escrever(montar_etapa(ano, chunk, num, grupos, conf))
            logger.info("%d: %d linhas lidas (%.0f s)", ano, lidas, time.perf_counter() - t0)
    except BaseException:
        for e in esc.values():
            e.descartar()              # nada pela metade: os CSVs anteriores do ano ficam como estavam
        raise
    for e in esc.values():
        e.fechar()

    orfas = int((~tec.index.isin(vistos)).sum()) if len(tec) else 0
    print(f"\n=== {ano} ===  escolas: {lidas}  |  " + "  |  ".join(f"{n}: {e.linhas}" for n, e in esc.items()))
    if orfas:
        print(f"  ATENÇÃO: {orfas} escolas do arquivo técnico não existem no CENSOESC (ignoradas)")
    if conf:
        print("  matrículas == masc + fem (escolas com matrícula ou masc + fem > 0):")
        for etapa, c in sorted(conf.items()):
            pct = 100 * c["iguais"] / c["comparaveis"] if c["comparaveis"] else float("nan")
            print(f"    {etapa:13s} {c['iguais']:>7}/{c['comparaveis']:<7} ({pct:.1f}%)")
    print(f"  arquivos em {pasta}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO, help="pasta com as subpastas por ano")
    ap.add_argument("--dicionario", type=Path, default=DIC_PADRAO)
    ap.add_argument("--municipios", type=Path, default=MUNICIPIOS_PADRAO, help="municipio.csv de gerar_dim.py")
    ap.add_argument("--saida", type=Path, default=SAIDA_PADRAO, help="pasta de saída (uma subpasta por ano)")
    ap.add_argument("--ano-inicio", type=int, default=1997)
    ap.add_argument("--ano-fim", type=int, default=2006)
    ap.add_argument("--encoding", default="utf-8-sig")
    ap.add_argument("--sep-saida", default=",")
    ap.add_argument("--chunk", type=int, default=20_000, help="linhas por bloco (2004-06 têm ~3.800 colunas)")
    ap.add_argument("--so-mapa", action="store_true", help="só grava mapa_variaveis.csv (não lê os dados)")
    args = ap.parse_args()
    if not (1997 <= args.ano_inicio <= args.ano_fim <= 2006):
        ap.error("o bloco vai de 1997 a 2006")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dic = pd.read_csv(args.dicionario, encoding="utf-8-sig", dtype={"variavel": str, "arquivo": str})
    dic["ano"] = pd.to_numeric(dic["ano"], errors="coerce")
    mapa_mun = None if args.so_mapa else carregar_municipios(args.municipios)
    for ano in range(args.ano_inicio, args.ano_fim + 1):
        processar_ano(ano, args, dic, mapa_mun)
    return 0


if __name__ == "__main__":
    sys.exit(main())