"""
Carga do Censo Escolar 1996 (CENSOESC_1996) no SQL Server.

O que faz:
  1. Cria o banco (se nao existir) e executa criar_banco.sql (DROP + CREATE de todas as tabelas).
  2. Passada 1 no CSV: carrega Estadual, Municipio e Instituicao_Imutavel.
  3. Passada 2 no CSV: carrega Instituicao_Anual, Oferta_Ensino, Infraestrutura, Turmas,
     Alunos_Rendimento, Matriculas e Professores (somas conforme modelagem_corrigida.txt).
  4. Valida as regras 4(a) e 4(b) dos professores e grava violacoes_professores.csv (nao descarta linhas).

Requisitos:
  pip install pandas sqlalchemy pyodbc
  + ODBC Driver 17 ou 18 for SQL Server instalado.

ATENCAO: o script apaga e recria as tabelas a cada execucao.
"""
import os
import re
import sys
from urllib.parse import quote_plus

import pandas as pd
import pyodbc
from sqlalchemy import create_engine

# =====================================================================
# CONFIGURACAO - ajuste aqui
# =====================================================================
CSV_PATH = r"C:\dados_inep\data_cen\Microdados da Educação Básica\1996\CENSOESC_1996.csv"
SQL_PATH = r"criar_banco.sql"
SAIDA_VIOLACOES = r"violacoes_professores.csv"

SERVER = r"localhost"                  # ex.: localhost\SQLEXPRESS
DATABASE = "censo_inep"
DRIVER = "ODBC Driver 18 for SQL Server"   # ou "ODBC Driver 17 for SQL Server"
AUTENTICACAO_WINDOWS = True            # False -> usa SQL_USER / SQL_PASSWORD (variaveis de ambiente)

# Dominio dos flags de Oferta (NIVELPRE, NIVELCA, NIV_F1A4_8, NIV_F5A8_8, NIVELMEDIO, ENS*).
# O dicionario so diz "Alfanumerica, tamanho 1", entao CONFIRME os valores reais.
# Se aparecer qualquer valor fora destes conjuntos, o script PARA e lista os valores encontrados.
FLAG_SIM = {"1", "S"}
FLAG_NAO = {"0", "2", "N"}

LINHAS_POR_BLOCO = 20000   # leitura do CSV em blocos (evita estourar memoria)
# =====================================================================

SOMAS = {
    "Turmas": [
        ("Pre_diurno", ["DPE114", "DPE115", "DPE116"]),
        ("Anos_iniciais_diurno", ["DPE118", "DEF113", "DEF114", "DEF115", "DEF116"]),
        ("Anos_finais_diurno", ["DEF117", "DEF118", "DEF119", "DEF11A"]),
        ("Fundamental_multisseriadas_diurno", ["DEF11B"]),
        ("Pre_noturno", ["NPE114", "NPE115", "NPE116"]),
        ("Anos_iniciais_noturno", ["NPE118", "NEF113", "NEF114", "NEF115", "NEF116"]),
        ("Anos_finais_noturno", ["NEF117", "NEF118", "NEF119", "NEF11A"]),
        ("Fundamental_multisseriadas_noturno", ["NEF11B"]),
        ("Medio_turmas_diurno", ["DEM113", "DEM114", "DEM115", "DEM116", "DEM117"]),
        ("Medio_turmas_noturno", ["NEM113", "NEM114", "NEM115", "NEM116", "NEM117"]),
        ("Educacao_especial_turmas_pne_total", ["VEE111"]),
    ],
    "Alunos_Rendimento": [
        ("Fundamental_anos_iniciais_repetentes_reprovados", ["VEF511", "VEF512", "VEF513", "VEF514"]),
        ("Fundamental_anos_finais_repetentes_reprovados", ["VEF515", "VEF516", "VEF517", "VEF518"]),
        ("Fundamental_anos_iniciais_repetentes_abandono", ["VEF521", "VEF522", "VEF523", "VEF524"]),
        ("Fundamental_anos_finais_repetentes_abandono", ["VEF525", "VEF526", "VEF527", "VEF528"]),
        ("Fundamental_anos_iniciais_repetentes_aprovados", ["VEF531", "VEF532", "VEF533", "VEF534"]),
        ("Fundamental_anos_finais_repetentes_aprovados", ["VEF535", "VEF536", "VEF537", "VEF538"]),
        ("Medio_repetentes_reprovados", ["VEM511", "VEM512", "VEM513", "VEM514"]),
        ("Medio_repetentes_abandono", ["VEM521", "VEM522", "VEM523", "VEM524"]),
        ("Medio_repetentes_aprovados", ["VEM531", "VEM532", "VEM533", "VEM534"]),
        ("Medio_abandono_diurno", ["VEM941", "VEM942", "VEM943", "VEM944"]),
        ("Medio_abandono_noturno", ["VEM945", "VEM946", "VEM947", "VEM948"]),
        ("Medio_aprovados_diurno", ["VEM951", "VEM952", "VEM953", "VEM954"]),
        ("Medio_aprovados_noturno", ["VEM955", "VEM956", "VEM957", "VEM958"]),
        ("Medio_reprovados_diurno", ["VEM961", "VEM962", "VEM963", "VEM964"]),
        ("Medio_reprovados_noturno", ["VEM965", "VEM966", "VEM967", "VEM968"]),
        ("Educacao_especial_abandono_total_pre_fund_medio", ["VEE711", "VEE712", "VEE713"]),
        ("Educacao_especial_aprovados_total_fund_medio", ["VEE722", "VEE723"]),
        ("Educacao_especial_reprovados_total_fund_medio", ["VEE732", "VEE733"]),
        ("Educacao_especial_alunos_pne_atendimento_total", ["VEE121"]),
        ("Educacao_especial_pre_visao", ["VEE611"]),
        ("Educacao_especial_pre_audicao", ["VEE612"]),
        ("Educacao_especial_pre_fisica", ["VEE613"]),
        ("Educacao_especial_pre_mental", ["VEE614"]),
        ("Educacao_especial_pre_multipla", ["VEE615"]),
        ("Educacao_especial_fundamental_visao", ["VEE621"]),
        ("Educacao_especial_fundamental_audicao", ["VEE622"]),
        ("Educacao_especial_fundamental_fisica", ["VEE623"]),
        ("Educacao_especial_fundamental_mental", ["VEE624"]),
        ("Educacao_especial_fundamental_multipla", ["VEE625"]),
        ("Educacao_especial_medio_visao", ["VEE631"]),
        ("Educacao_especial_medio_audicao", ["VEE632"]),
        ("Educacao_especial_medio_fisica", ["VEE633"]),
        ("Educacao_especial_medio_mental", ["VEE634"]),
        ("Educacao_especial_medio_multipla", ["VEE635"]),
        ("Supletivo_fundamental_diurno_1sem", ["VES411"]),
        ("Supletivo_fundamental_diurno_2sem", ["VES413"]),
        ("Supletivo_medio_diurno_1sem", ["VES412"]),
        ("Supletivo_medio_diurno_2sem", ["VES414"]),
        ("Supletivo_fundamental_noturno_1sem", ["VES421"]),
        ("Supletivo_fundamental_noturno_2sem", ["VES423"]),
        ("Supletivo_medio_noturno_1sem", ["VES422"]),
        ("Supletivo_medio_noturno_2sem", ["VES424"]),
    ],
    "Matriculas": [
        ("Pre_diurno", ["DPE11A", "DPE11B", "DPE11C"]),
        ("Anos_iniciais_diurno", ["DPE11E", "DEF11C", "DEF11D", "DEF11E", "DEF11F"]),
        ("Anos_finais_diurno", ["DEF11G", "DEF11H", "DEF11I", "DEF11J"]),
        ("Pre_noturno", ["NPE11A", "NPE11B", "NPE11C"]),
        ("Anos_iniciais_noturno", ["NPE11E", "NEF11C", "NEF11D", "NEF11E", "NEF11F"]),
        ("Anos_finais_noturno", ["NEF11G", "NEF11H", "NEF11I", "NEF11J"]),
        ("Pre_masculino", ["VPE411", "VPE412", "VPE413"]),
        ("Anos_iniciais_masculino", ["VPE414", "VEF411", "VEF412", "VEF413", "VEF414"]),
        ("Anos_finais_masculino", ["VEF415", "VEF416", "VEF417", "VEF418"]),
        ("Pre_feminino", ["VPE421", "VPE422", "VPE423"]),
        ("Anos_iniciais_feminino", ["VPE424", "VEF421", "VEF422", "VEF423", "VEF424"]),
        ("Anos_finais_feminino", ["VEF425", "VEF426", "VEF427", "VEF428"]),
        ("Medio_matriculas_diurno", ["DEM118", "DEM119", "DEM11A", "DEM11B", "DEM11C"]),
        ("Medio_matriculas_noturno", ["NEM118", "NEM119", "NEM11A", "NEM11B", "NEM11C"]),
        ("Medio_masculino", ["VEM411", "VEM412", "VEM413", "VEM414", "VEM415"]),
        ("Medio_feminino", ["VEM421", "VEM422", "VEM423", "VEM424", "VEM425"]),
        ("Medio_origem_supletivo_1995", ["VEM611", "VEM612", "VEM613", "VEM614"]),
        ("Medio_origem_fora_do_estado", ["VEM711", "VEM712", "VEM713", "VEM714"]),
        ("Matricula_final_pre_1995", ["VPE512", "VPE513", "VPE514"]),
        ("Matricula_final_classe_alfabetizacao_1995", ["VPE516"]),
    ],
    "Professores": [
        ("Total_docentes_1grau_incompleto", ["VDG111"]),
        ("Total_docentes_1grau_completo", ["VDG112"]),
        ("Total_docentes_magisterio", ["VDG113"]),
        ("Total_docentes_outra_formacao_medio", ["VDG114"]),
        ("Total_docentes_licenciatura_completa", ["VDG115"]),
        ("Total_docentes_superior_sem_licenciatura_com_magisterio", ["VDG116"]),
        ("Total_docentes_superior_sem_licenciatura_sem_magisterio", ["VDG117"]),
        ("Atuacao_pre_1grau_incompleto", ["VDG121", "VDG131"]),
        ("Atuacao_pre_1grau_completo", ["VDG122", "VDG132"]),
        ("Atuacao_pre_magisterio", ["VDG123", "VDG133"]),
        ("Atuacao_pre_outra_formacao_medio", ["VDG124", "VDG134"]),
        ("Atuacao_pre_licenciatura", ["VDG125", "VDG135"]),
        ("Atuacao_pre_superior_sem_lic_com_mag", ["VDG126", "VDG136"]),
        ("Atuacao_pre_superior_sem_lic_sem_mag", ["VDG127", "VDG137"]),
        ("Atuacao_anos_iniciais_1grau_incompleto", ["VDG141", "VDG151"]),
        ("Atuacao_anos_iniciais_1grau_completo", ["VDG142", "VDG152"]),
        ("Atuacao_anos_iniciais_magisterio", ["VDG143", "VDG153"]),
        ("Atuacao_anos_iniciais_outra_formacao_medio", ["VDG144", "VDG154"]),
        ("Atuacao_anos_iniciais_licenciatura", ["VDG145", "VDG155"]),
        ("Atuacao_anos_iniciais_superior_sem_lic_com_mag", ["VDG146", "VDG156"]),
        ("Atuacao_anos_iniciais_superior_sem_lic_sem_mag", ["VDG147", "VDG157"]),
        ("Atuacao_anos_finais_1grau_incompleto", ["VDG161"]),
        ("Atuacao_anos_finais_1grau_completo", ["VDG162"]),
        ("Atuacao_anos_finais_magisterio", ["VDG163"]),
        ("Atuacao_anos_finais_outra_formacao_medio", ["VDG164"]),
        ("Atuacao_anos_finais_licenciatura", ["VDG165"]),
        ("Atuacao_anos_finais_superior_sem_lic_com_mag", ["VDG166"]),
        ("Atuacao_anos_finais_superior_sem_lic_sem_mag", ["VDG167"]),
        ("Atuacao_medio_1grau_incompleto", ["VDG171"]),
        ("Atuacao_medio_1grau_completo", ["VDG172"]),
        ("Atuacao_medio_magisterio", ["VDG173"]),
        ("Atuacao_medio_outra_formacao_medio", ["VDG174"]),
        ("Atuacao_medio_licenciatura", ["VDG175"]),
        ("Atuacao_medio_superior_sem_lic_com_mag", ["VDG176"]),
        ("Atuacao_medio_superior_sem_lic_sem_mag", ["VDG177"]),
        ("Atuacao_especial_1grau_incompleto", ["VDG181", "VDG191"]),
        ("Atuacao_especial_1grau_completo", ["VDG182", "VDG192"]),
        ("Atuacao_especial_magisterio", ["VDG183", "VDG193"]),
        ("Atuacao_especial_outra_formacao_medio", ["VDG184", "VDG194"]),
        ("Atuacao_especial_licenciatura", ["VDG185", "VDG195"]),
        ("Atuacao_especial_superior_sem_lic_com_mag", ["VDG186", "VDG196"]),
        ("Atuacao_especial_superior_sem_lic_sem_mag", ["VDG187", "VDG197"]),
        ("Atuacao_supletivo_1sem_1grau_incompleto", ["VDG1A1"]),
        ("Atuacao_supletivo_1sem_1grau_completo", ["VDG1A2"]),
        ("Atuacao_supletivo_1sem_magisterio", ["VDG1A3"]),
        ("Atuacao_supletivo_1sem_outra_formacao_medio", ["VDG1A4"]),
        ("Atuacao_supletivo_1sem_licenciatura", ["VDG1A5"]),
        ("Atuacao_supletivo_1sem_superior_sem_lic_com_mag", ["VDG1A6"]),
        ("Atuacao_supletivo_1sem_superior_sem_lic_sem_mag", ["VDG1A7"]),
        ("Atuacao_supletivo_2sem_1grau_incompleto", ["VDG1B1"]),
        ("Atuacao_supletivo_2sem_1grau_completo", ["VDG1B2"]),
        ("Atuacao_supletivo_2sem_magisterio", ["VDG1B3"]),
        ("Atuacao_supletivo_2sem_outra_formacao_medio", ["VDG1B4"]),
        ("Atuacao_supletivo_2sem_licenciatura", ["VDG1B5"]),
        ("Atuacao_supletivo_2sem_superior_sem_lic_com_mag", ["VDG1B6"]),
        ("Atuacao_supletivo_2sem_superior_sem_lic_sem_mag", ["VDG1B7"]),
    ],
}


FLAGS_DIRETOS = {          # coluna no banco -> coluna no CSV
    "NIVELPRE": "NIVELPRE",
    "NIV_F5A8_8": "NIV_F5A8_8",
    "NIVELMEDIO": "NIVELMEDIO",
    "ENSREGULAR": "ENSREGULAR",
    "ENSSUPLET": "ENSSUPLET",
    "ENSESPECIAL": "ENSESPECIAL",
}
INFRA = ["PERMANEN", "PROVISOR", "NOESTAB", "FORAESTA"]
TEXTO_ANUAL = ["CODMUNIC", "DEP", "LOC", "CODFUNC"]

# Professores: ordem das 7 formacoes e niveis (prefixos das colunas) para as regras 4(a)/4(b)
PROF_TOTAL = [n for n, _ in SOMAS["Professores"] if n.startswith("Total_docentes_")]
PROF_NIVEIS = ["Atuacao_pre_", "Atuacao_anos_iniciais_", "Atuacao_anos_finais_", "Atuacao_medio_",
               "Atuacao_especial_", "Atuacao_supletivo_1sem_", "Atuacao_supletivo_2sem_"]
PROF_COLS_NIVEL = {p: [n for n, _ in SOMAS["Professores"] if n.startswith(p)] for p in PROF_NIVEIS}


# ---------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------
def conn_str(database):
    partes = [f"DRIVER={{{DRIVER}}}", f"SERVER={SERVER}", f"DATABASE={database}", "TrustServerCertificate=yes"]
    if AUTENTICACAO_WINDOWS:
        partes.append("Trusted_Connection=yes")
    else:
        partes += [f"UID={os.environ['SQL_USER']}", f"PWD={os.environ['SQL_PASSWORD']}"]
    return ";".join(partes)


def criar_banco_e_tabelas():
    with pyodbc.connect(conn_str("master"), autocommit=True) as cn:
        cn.execute(f"IF DB_ID(N'{DATABASE}') IS NULL CREATE DATABASE [{DATABASE}];")
    sql = open(SQL_PATH, encoding="utf-8").read()
    blocos = [b.strip() for b in re.split(r"^\s*GO\s*$", sql, flags=re.MULTILINE | re.IGNORECASE) if b.strip()]
    with pyodbc.connect(conn_str(DATABASE), autocommit=True) as cn:
        for b in blocos:
            cn.execute(b)
    print(f"[ok] banco '{DATABASE}' pronto, {len(blocos)} blocos SQL executados")


def detectar_formato(path):
    with open(path, "r", encoding="latin-1") as f:
        primeira = f.readline()
    sep = max([";", ",", "\t", "|"], key=primeira.count)
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                for _ in range(2000):
                    if not f.readline():
                        break
            return sep, enc
        except UnicodeDecodeError:
            continue
    return sep, "latin-1"


def colunas_necessarias():
    nec = {"MASCARA", "ANO", "UF", "SIGLA", "CODMUNIC", "MUNIC", "REG_CNAS", "NIVELCA", "NIV_F1A4_8"}
    nec |= set(TEXTO_ANUAL) | set(FLAGS_DIRETOS.values()) | set(INFRA)
    for lista in SOMAS.values():
        for _, cods in lista:
            nec |= set(cods)
    return nec


def ler_blocos(path, sep, enc, nec):
    return pd.read_csv(path, sep=sep, encoding=enc, dtype=str, keep_default_na=False,
                       usecols=lambda c: c.strip().upper() in nec, chunksize=LINHAS_POR_BLOCO)


def normalizar(df):
    df.columns = [c.strip().upper() for c in df.columns]
    return df


def texto(s):
    s = s.astype(str).str.strip()
    return s.where(s != "", None)


def numero(s):
    return pd.to_numeric(s.astype(str).str.strip(), errors="coerce")


def inteiro(s):
    return numero(s).round().astype("Int64")


def flag(s, nome):
    v = s.astype(str).str.strip().str.upper()
    desconhecidos = sorted(set(v.unique()) - FLAG_SIM - FLAG_NAO - {""})
    if desconhecidos:
        raise ValueError(f"Coluna {nome}: valores fora de FLAG_SIM/FLAG_NAO: {desconhecidos}. "
                         f"Ajuste FLAG_SIM e FLAG_NAO no topo do script.")
    out = pd.Series(pd.NA, index=s.index, dtype="boolean")
    out[v.isin(FLAG_SIM)] = True
    out[v.isin(FLAG_NAO)] = False
    return out


def para_banco(df):
    return df.astype(object).where(df.notna(), None)


def inserir(engine, tabela, df):
    if len(df):
        para_banco(df).to_sql(tabela, engine, schema="dbo", if_exists="append", index=False, chunksize=5000)


# ---------------------------------------------------------------------
# Construcao das tabelas (independente de banco -> testavel)
# ---------------------------------------------------------------------
def tabelas_pequenas(bloco):
    """Estadual, Municipio, Instituicao_Imutavel (parciais deste bloco)."""
    b = normalizar(bloco.copy())
    b["SIGLA"], b["UF"], b["CODMUNIC"], b["MUNIC"] = texto(b["SIGLA"]), texto(b["UF"]), texto(b["CODMUNIC"]), texto(b["MUNIC"])
    uf = b[["SIGLA", "UF"]].dropna(subset=["SIGLA"]).drop_duplicates()
    mun = b[["CODMUNIC", "MUNIC", "SIGLA"]].dropna(subset=["CODMUNIC", "SIGLA"]).drop_duplicates()
    imut = pd.DataFrame({"MASCARA": inteiro(b["MASCARA"]), "REG_CNAS": texto(b["REG_CNAS"])}).dropna(subset=["MASCARA"]).drop_duplicates()
    return uf, mun, imut


def tabelas_fato(bloco):
    """Instituicao_Anual, Oferta_Ensino, Infraestrutura e tabelas de somas para um bloco."""
    b = normalizar(bloco.copy())
    chave = pd.DataFrame({"MASCARA": inteiro(b["MASCARA"]), "ANO": inteiro(b["ANO"])})
    if chave.isna().any().any():
        raise ValueError("Ha linhas com MASCARA ou ANO ausente/nao numerico.")
    out = {}

    anual = chave.copy()
    for c in TEXTO_ANUAL:
        anual[c] = texto(b[c])
    out["Instituicao_Anual"] = anual

    of = chave.copy()
    for destino, origem in FLAGS_DIRETOS.items():
        of[destino] = flag(b[origem], origem)
    # JUNCAO: NIVELCA OR NIV_F1A4_8 (Kleene: True OR NA = True; False OR NA = NA)
    of["NIV_ANOS_INICIAIS"] = flag(b["NIVELCA"], "NIVELCA") | flag(b["NIV_F1A4_8"], "NIV_F1A4_8")
    out["Oferta_Ensino"] = of[["MASCARA", "ANO", "NIVELPRE", "NIV_ANOS_INICIAIS", "NIV_F5A8_8", "NIVELMEDIO",
                               "ENSREGULAR", "ENSSUPLET", "ENSESPECIAL"]]

    infra = chave.copy()
    for c in INFRA:
        infra[c] = inteiro(b[c])
    out["Infraestrutura"] = infra

    cache = {}
    def col(c):
        if c not in cache:
            cache[c] = numero(b[c])
        return cache[c]

    for tabela, lista in SOMAS.items():
        t = chave.copy()
        for nome, cods in lista:
            soma = pd.concat([col(c) for c in cods], axis=1).sum(axis=1, min_count=1)  # tudo vazio -> NULL
            t[nome] = soma.round().astype("Int64")
        out[tabela] = t
    return out


def violacoes_professores(prof):
    """Regras 4(a) e 4(b). Nao descarta nada: so retorna as linhas que violam."""
    achados = []
    for i, tot_nome in enumerate(PROF_TOTAL):
        total = prof[tot_nome].astype("float64")
        soma_niveis = 0
        for pref, cols in PROF_COLS_NIVEL.items():
            nivel = prof[cols[i]].astype("float64")
            soma_niveis = soma_niveis + nivel.fillna(0)
            ruim = nivel > total          # NaN -> False
            if ruim.any():
                d = prof.loc[ruim, ["MASCARA", "ANO"]].copy()
                d["regra"], d["formacao"], d["nivel"] = "4a: atuacao > total da escola", tot_nome, cols[i]
                d["total"], d["atuacao"] = total[ruim], nivel[ruim]
                achados.append(d)
        ruim = soma_niveis < total
        if ruim.any():
            d = prof.loc[ruim, ["MASCARA", "ANO"]].copy()
            d["regra"], d["formacao"], d["nivel"] = "4b: soma dos niveis < total da escola", tot_nome, "(soma dos niveis)"
            d["total"], d["atuacao"] = total[ruim], soma_niveis[ruim]
            achados.append(d)
    return pd.concat(achados) if achados else pd.DataFrame()


# ---------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------
TABELAS_FATO = ["Instituicao_Anual", "Oferta_Ensino", "Infraestrutura", "Turmas",
                "Alunos_Rendimento", "Matriculas", "Professores"]
TODAS_TABELAS = ["Estadual", "Municipio", "Instituicao_Imutavel"] + TABELAS_FATO


def executar_carga(inserir_fn, sep, enc, nec):
    """Fluxo completo de carga. inserir_fn(tabela, DataFrame) decide o destino (SQL Server, SQLite...)."""
    # ---- Passada 1: Estadual, Municipio, Instituicao_Imutavel
    ufs, muns, imuts = [], [], []
    for bloco in ler_blocos(CSV_PATH, sep, enc, nec):
        u, m, i = tabelas_pequenas(bloco)
        ufs.append(u); muns.append(m); imuts.append(i)
    uf = pd.concat(ufs).drop_duplicates()
    mun = pd.concat(muns).drop_duplicates()
    imut = pd.concat(imuts).drop_duplicates()

    if uf["SIGLA"].duplicated().any():
        sys.exit(f"[erro] SIGLA com mais de um nome de UF: {uf[uf['SIGLA'].duplicated(keep=False)].to_string()}")
    dup = mun[mun["CODMUNIC"].duplicated(keep=False)].sort_values("CODMUNIC")
    if len(dup):
        sys.exit("[erro] CODMUNIC repetido com MUNIC/SIGLA diferentes (a PK de Municipio nao e so CODMUNIC; "
                 "considere PK (SIGLA, CODMUNIC) ou revise o nome do municipio):\n" + dup.head(20).to_string())
    if imut["MASCARA"].duplicated().any():
        sys.exit("[erro] MASCARA com mais de um REG_CNAS: "
                 + imut[imut["MASCARA"].duplicated(keep=False)].head(20).to_string())
    inserir_fn("Estadual", uf)
    inserir_fn("Municipio", mun)
    inserir_fn("Instituicao_Imutavel", imut)
    print(f"[ok] Estadual={len(uf)} Municipio={len(mun)} Instituicao_Imutavel={len(imut)}")

    # ---- Passada 2: tabelas por escola/ano
    total, viol = 0, []
    for bloco in ler_blocos(CSV_PATH, sep, enc, nec):
        t = tabelas_fato(bloco)
        for nome in TABELAS_FATO:
            inserir_fn(nome, t[nome])
        v = violacoes_professores(t["Professores"])
        if len(v):
            viol.append(v)
        total += len(t["Instituicao_Anual"])
        print(f"[info] {total} linhas carregadas", end="\r")
    print(f"\n[ok] {total} linhas (escola/ano) carregadas em cada tabela")

    if viol:
        v = pd.concat(viol)
        v.to_csv(SAIDA_VIOLACOES, index=False, encoding="utf-8-sig")
        print(f"[aviso] {len(v)} violacoes das regras 4(a)/4(b) de professores -> {SAIDA_VIOLACOES}")
        print(v["regra"].value_counts().to_string())
    else:
        print("[ok] nenhuma violacao das regras 4(a)/4(b) de professores")


def main():
    sep, enc = detectar_formato(CSV_PATH)
    print(f"[info] separador={sep!r} encoding={enc}")
    nec = colunas_necessarias()
    cab = normalizar(pd.read_csv(CSV_PATH, sep=sep, encoding=enc, nrows=0, dtype=str))
    faltando = sorted(nec - set(cab.columns))
    if faltando:
        sys.exit(f"[erro] colunas ausentes no CSV ({len(faltando)}): {faltando[:30]} ...\n"
                 f"O CSV precisa ter cabecalho com os codigos do dicionario (MASCARA, ANO, ...).")

    criar_banco_e_tabelas()
    engine = create_engine("mssql+pyodbc:///?odbc_connect=" + quote_plus(conn_str(DATABASE)), fast_executemany=True)
    executar_carga(lambda tabela, df: inserir(engine, tabela, df), sep, enc, nec)

    with pyodbc.connect(conn_str(DATABASE)) as cn:
        for nome in TODAS_TABELAS:
            n = cn.execute(f"SELECT COUNT(*) FROM dbo.{nome}").fetchone()[0]
            print(f"  {nome:<22} {n:>9} linhas")


if __name__ == "__main__":
    main()