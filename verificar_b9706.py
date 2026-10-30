"""
Verificação antes do gerador do bloco 1997-2006 (04_b9706.sql). Só lê; grava relatórios em 'saida_b9706/'.

  1. Correspondência por ano: para cada coluna de b9706.escola e b9706.infraestrutura, quais variáveis do
     INEP a alimentam em cada ano, se estão no CSV e como vêm preenchidas (s / n / vazio / número).
     -> mostra se a coluna é flag ou contagem (ex.: VTV pode ser nº de televisores, não s/n).
  1b. Variáveis de cadastro/infraestrutura do CSV que NÃO caíram em nenhuma coluna (para você decidir).
  2. Professores (VDG1): faixas de formação que aparecem em cada ano (comparar com as 5 faixas de 1996).
  3. EJA / supletivo:
     a. 1996: 1º x 2º semestre (VES2) - indícios de duplicação;
     b. total por método e por nível em cada ano (turno, coorte, sexo, presencial, exames);
     c. consistência por escola entre métodos (turno x sexo, turno x coorte...).

Exemplos:
  python verificar_b9706.py
  python verificar_b9706.py --etapas 3 --ano-inicio 1996 --ano-fim 1998
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import pandas as pd

logger = logging.getLogger("verif_b9706")

DIC_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/dicionario_unificado.csv")
RAIZ_PADRAO = Path("C:/dados_inep/data_cen/Microdados da Educação Básica")
SEP, ENC, CHUNK = "|", "utf-8-sig", 50_000

# coluna do modelo -> nomes de variável (regex, sem diferenciar maiúsculas) que a alimentam
ESCOLA = {
    "nivel_creche": r"NIVELCRE", "nivel_pre": r"NIVELPRE",
    "nivel_fund_ini": r"NIVELCA|NIVELALF|NIV_F1A4(_8)?|NIV_F9INI", "nivel_fund_fin": r"NIV_F5A8(_8)?|NIV_F9FIM",
    "ef9_anos": r"NIV_F9INI|NIV_F9FIM", "nivel_medio": r"NIVELMED|NIVELMEDIO",
    "nivel_medio_profissional": r"NIVELMEDPR|NIVMEDPR|NIVM_INT", "nivel_normal": r"NIVMEDMA",
    "nivel_eja": r"SUPL_AVA|SUPL_SAVA", "nivel_eja_exames": r"SUPL_P_EXA|SPREPEXC", "nivel_tecnico": r"EDPROFIS",
    "nivel_especial_exclusiva": r"ESP_EXCL",
    "mod_regular": r"ENSREGULAR", "mod_especial": r"ENSESPECIAL", "mod_supletivo": r"ENSSUPLET",
    "escola_indigena": r"ED_INDIG|EDUC_INDIG", "area_quilombola": r"AREA_QUIL", "area_assentamento": r"ESC_ASSE",
    "predio_compartilhado": r"PRED_COM",
    "ocupacao_propria": r"PROPRIO", "ocupacao_alugada": r"ALUGADO", "ocupacao_cedida": r"CEDIDO",
    "local_predio_escolar": r"PRED_ESC", "local_templo": r"TEMPLO", "local_galpao": r"GALPAO",
    "local_empresa": r"EMPRESA", "local_outra_escola": r"OUTR_ESC", "local_unidade_internacao": r"UN_INT",
    "local_outra_instituicao": r"OUTR_INS",
    "funcionarios": r"FUNCION", "prof_total_escola": r"PROFESS",
}
INFRA = {
    "salas_existentes_perm": r"PERMANEN", "salas_existentes_prov": r"PROVISOR",
    "salas_utilizadas_no_estab": r"NOESTAB", "salas_utilizadas_fora": r"FORAESTA",
    "agua_rede_publica": r"AGUA_PUB", "agua_poco_cacimba_fonte": r"AGUA_ART|AGUA_CAC|AGUA_FON",
    "agua_inexistente": r"AGUA_INE",
    "energia_rede_publica": r"ENER_PUB", "energia_gerador": r"ENER_GER", "energia_inexistente": r"ENER_INE",
    "esgoto_rede_publica": r"ESG_PUB", "esgoto_fossa": r"ESG_FOSS", "esgoto_inexistente": r"ESG_INEX",
    "lixo_coleta": r"LIXO_COL", "biblioteca": r"BIBLIO|BIBLIOTE", "sala_leitura": r"SALA_LEI|SAL_LEIT",
    "lab_informatica": r"LAB_INFO", "lab_ciencias": r"LAB_CIEN", "quadra_esportes": r"QUADRA|QUAD_DES|QUAD_COB",
    "cozinha": r"COZINHA", "refeitorio": r"REFEITOR", "parque_infantil": r"PARQ_INF",
    "sanitario_dentro_predio": r"SANI_DEN", "sanitario_fora_predio": r"SANI_FOR", "sanitario_pne": r"SANI_ESP",
    "dependencias_pne": r"DEP_ESP", "internet": r"INTERNET", "rede_local": r"REDE_LOC",
    "banda_larga": r"CONEX_.*|S_CONEX", "tv": r"VTV", "video_dvd": r"VVIDEO|VDVD",
    "parabolica": r"PARABOL|ANALOGTV|DIGIT_TV|PARA_INT|PARAB_INT", "impressora": r"VIMPRESS|IMPRESS",
    "copiadora": r"VCOPIAD", "datashow": r"VDATASHOW|VDATSHOW",
    "n_microcomputadores": r"COMPPENT?|PC486386|COMPOUTR|COMP_OUT|COMPAPP",
}
CONTROLE = r"MASCARA|ANO|CODMUNIC|UF|SIGLA|MUNIC|DEP|LOC|CODFUNC"
MEDIDA = r"^[DNV](PE|CA|EF|E9F|EM|CN|ES|EE|DG|TE|PA|TS|AT)\d"


# ---------------------------------------------------------------------------
def comp(s: object) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def limpar(c: str) -> str:
    return str(c).strip().lstrip("\ufeff").replace("ï»¿", "")


def achar_csv(raiz: Path, ano: int) -> Path | None:
    pasta = raiz / str(ano)
    if not pasta.exists():
        return None
    achados = [p for p in pasta.rglob("*") if p.is_file() and p.stem.lower() == f"censoesc_{ano}"
               and p.suffix.lower() in (".csv", ".txt", "")]
    return achados[0] if achados else None


def cabecalho(p: Path) -> list[str]:
    return [limpar(c) for c in pd.read_csv(p, sep=SEP, encoding=ENC, encoding_errors="replace", nrows=0).columns]


def ler(p: Path, cols: set[str]) -> pd.DataFrame:
    """Lê as colunas pedidas inteiras (como texto)."""
    cab = set(cabecalho(p))
    usar = cols & cab
    partes = [ch for ch in pd.read_csv(p, sep=SEP, encoding=ENC, encoding_errors="replace", dtype=str,
                                        usecols=lambda c: limpar(c) in usar, chunksize=CHUNK, low_memory=False)]
    if not partes:
        return pd.DataFrame(columns=sorted(usar))
    df = pd.concat(partes, ignore_index=True)
    df.columns = [limpar(c) for c in df.columns]
    return df.apply(lambda s: s.str.strip())


def perfil(s: pd.Series) -> dict:
    n = len(s)
    low = s.fillna("").str.lower()
    num = pd.to_numeric(s, errors="coerce")
    return {"pct_s": round(100 * low.eq("s").mean(), 1), "pct_n": round(100 * low.eq("n").mean(), 1),
            "pct_vazio": round(100 * low.eq("").mean(), 1), "pct_numerico": round(100 * num.notna().mean(), 1),
            "max_numerico": float(num.max()) if num.notna().any() else None,
            "outros_valores": ",".join(sorted(set(low[num.isna()]) - {"s", "n", ""})[:5])} if n else {}


# ---------------------------------------------------------------------------
# 1. Correspondência das colunas de escola e infraestrutura
# ---------------------------------------------------------------------------
def etapa1(dic: pd.DataFrame, raiz: Path, anos: range) -> dict[str, pd.DataFrame]:
    linhas, sobras = [], []
    mapas = {**{("escola", k): v for k, v in ESCOLA.items()}, **{("infraestrutura", k): v for k, v in INFRA.items()}}
    for ano in anos:
        p = achar_csv(raiz, ano)
        if p is None:
            continue
        cab = cabecalho(p)
        desc = dic[dic.ano == ano].drop_duplicates("variavel").set_index("variavel").descricao.to_dict()
        usadas = {}
        for (tab, col), rx in mapas.items():
            for v in cab:
                if re.fullmatch(rx, v, flags=re.I):
                    usadas.setdefault(v, []).append((tab, col))
        df = ler(p, set(usadas))
        for v, alvos in usadas.items():
            for tab, col in alvos:
                linhas.append({"ano": ano, "tabela": tab, "coluna_modelo": col, "variavel": v,
                               "descricao": re.sub(r"\s+", " ", str(desc.get(v, "")))[:90], **perfil(df[v])})
        for (tab, col), rx in mapas.items():
            if not any((tab, col) in a for a in usadas.values()):
                linhas.append({"ano": ano, "tabela": tab, "coluna_modelo": col, "variavel": "(nenhuma neste ano)"})
        for v in cab:
            if v in usadas or re.fullmatch(CONTROLE, v) or re.match(MEDIDA, v):
                continue
            sobras.append({"ano": ano, "variavel": v, "descricao": re.sub(r"\s+", " ", str(desc.get(v, "")))[:110]})
        logger.info("%d: %d variáveis mapeadas, %d não mapeadas", ano, len(usadas),
                    sum(1 for s in sobras if s["ano"] == ano))
    return {"1_correspondencia_por_ano": pd.DataFrame(linhas), "1b_nao_mapeadas": pd.DataFrame(sobras)}


# ---------------------------------------------------------------------------
# 2. Faixas de formação dos professores (VDG1)
# ---------------------------------------------------------------------------
RE_FORMACAO = re.compile(r"^(com|sem|superior|ate|nivel)|grau|licenci|magist|posgrad|especializ|mestrad|doutorad")


def etapa2(dic: pd.DataFrame, anos: range) -> dict[str, pd.DataFrame]:
    """A partir de 1998 a descrição das variáveis de formação é só a faixa ("Com Médio (2º Grau) Magistério
    Completo"); a etapa vem do bloco do Leia-me (a letra/dígito após VDG1). Por isso o relatório traz a página."""
    d = dic[dic.variavel.str.match(r"^VDG1", na=False) & dic.ano.isin(list(anos))].drop_duplicates(["ano", "variavel"])
    linhas = []
    for _, r in d.iterrows():
        desc = re.sub(r"\s+", " ", str(r.descricao)).strip()
        c = comp(desc)
        if c.startswith(("numerodeprofessores", "nodeprofessores")):
            tipo, formacao = "total_da_etapa", ""
        else:
            partes = [x.strip() for x in re.split(r"\s+-\s+", desc) if x.strip()]
            form = [x for x in partes if RE_FORMACAO.search(comp(x)) and not re.search(r"\d{2}/\d{2}", x)]
            tipo, formacao = ("formacao", form[-1]) if form else ("outro", "")
        linhas.append({"ano": int(r.ano), "variavel": r.variavel, "bloco": r.variavel[4:-1] or r.variavel[4:],
                       "tipo": tipo, "formacao": formacao, "descricao": desc[:140],
                       "pagina": r.get("pagina", ""), "fonte_pdf": r.get("fonte_pdf", "")})
    det = pd.DataFrame(linhas)
    if det.empty:
        return {"2_vdg1_detalhe": det}
    f = det[det.tipo == "formacao"]
    faixas = (f.groupby(["ano", "formacao"]).agg(variaveis=("variavel", "count"),
                                                  blocos=("bloco", lambda s: ",".join(sorted(set(s)))))
              .reset_index())
    blocos = (f.groupby(["ano", "bloco"]).agg(faixas=("formacao", "nunique"), variaveis=("variavel", "count"),
                                               pagina=("pagina", "first"), exemplo=("variavel", "first"))
              .reset_index())
    return {"2_vdg1_detalhe": det, "2b_faixas_de_formacao_por_ano": faixas, "2c_blocos_por_ano": blocos}


# ---------------------------------------------------------------------------
# 3. EJA / supletivo
# ---------------------------------------------------------------------------
def nivel_eja(c: str) -> str:
    if "profissionaliz" in c or "qualifica" in c or "aprendizagem" in c:
        return "eja_prof"
    if "alfabetiza" in c or re.search(r"1a?a?a?2a?serie|1aa4a|1aa2a|3aa4a|anosiniciais|1aserie|2aserie|3aserie|4aserie", c):
        return "eja_fund_ai"
    if re.search(r"5aa8a|5aa6a|7aa8a|anosfinais|5aserie|6aserie|7aserie|8aserie", c):
        return "eja_fund_af"
    if "medio" in c or "2ograu" in c:
        return "eja_medio"
    if "fundamental" in c or "1ograu" in c:
        return "eja_fund"
    return "eja"


def classificar_eja(v: str, desc: str) -> tuple[str, str] | None:
    """(metodo, nivel) ou None (fora: professores, concluintes, turmas, organização)."""
    c = comp(desc)
    if re.search(r"professor|concluint|organiza|turma", c):
        return None
    if not re.search(r"matricul|alunos", c):
        return None
    if "exame" in c or v.startswith("VES9"):          # VES9 = cursos preparatórios para exames
        return ("exames", nivel_eja(c))
    if "nascid" in c:
        return ("coorte", nivel_eja(c))
    if v[0] in "DN":
        return ("turno", nivel_eja(c))
    if re.search(r"masculino|feminino", c):
        return ("sexo", nivel_eja(c))
    if re.search(r"semestre", c):
        return (f"{v[:4]}_turno_semestre", nivel_eja(c))
    return ("total_sem_corte", nivel_eja(c))


def etapa3(dic: pd.DataFrame, raiz: Path, anos: range) -> dict[str, pd.DataFrame]:
    totais, consist, semestres = [], [], []
    for ano in anos:
        p = achar_csv(raiz, ano)
        if p is None:
            continue
        cab = set(cabecalho(p))
        d = dic[(dic.ano == ano) & (dic.arquivo == "CENSOESC") & dic.variavel.str.match(r"^[DNV]ES\d", na=False)
                & dic.variavel.isin(cab)].drop_duplicates("variavel")
        mapa = {}
        for v, desc in zip(d.variavel, d.descricao):
            r = classificar_eja(v, desc)
            if r:
                mapa[v] = (*r, comp(desc))
        if not mapa:
            continue
        df = ler(p, set(mapa) | {"MASCARA"})
        num = df[list(mapa)].apply(pd.to_numeric, errors="coerce")
        por_metodo = {}
        for (met, niv), cols in _agrupar(mapa).items():
            s = num[cols].sum(axis=1, min_count=1)
            totais.append({"ano": ano, "metodo": met, "nivel": niv, "colunas": len(cols), "total": float(s.sum()),
                           "escolas_com_valor": int((s > 0).sum()), "exemplos": ",".join(cols[:5])})
            por_metodo.setdefault(met, []).append(s)
        tot = {m: pd.concat(ss, axis=1).sum(axis=1, min_count=1) for m, ss in por_metodo.items()}
        metodos = sorted(tot)
        for i, a in enumerate(metodos):
            for b in metodos[i + 1:]:
                ok = tot[a].notna() & tot[b].notna() & ((tot[a] > 0) | (tot[b] > 0))
                consist.append({"ano": ano, "metodo_a": a, "metodo_b": b, "escolas_comparaveis": int(ok.sum()),
                                "pct_iguais": round(100 * (tot[a][ok] == tot[b][ok]).mean(), 1) if ok.any() else None,
                                "soma_a": float(tot[a][ok].sum()), "soma_b": float(tot[b][ok].sum())})
        # 1996: 1º x 2º semestre
        sem = {k: [v for v, (m, _, c) in mapa.items() if m.endswith("turno_semestre") and f"{k}osemestre" in c]
               for k in ("1", "2")}
        if sem["1"] and sem["2"]:
            for fam in sorted({v[:4] for v in sem["1"] + sem["2"]}):
                s1 = num[[v for v in sem["1"] if v.startswith(fam)]].sum(axis=1, min_count=1)
                s2 = num[[v for v in sem["2"] if v.startswith(fam)]].sum(axis=1, min_count=1)
                ambos = s1.notna() & s2.notna() & ((s1 > 0) | (s2 > 0))
                semestres.append({"ano": ano, "familia": fam, "total_1o_semestre": float(s1.sum()),
                                  "total_2o_semestre": float(s2.sum()), "escolas_com_algum": int(ambos.sum()),
                                  "pct_escolas_s1_igual_s2": round(100 * (s1[ambos] == s2[ambos]).mean(), 1),
                                  "escolas_so_1o": int(((s1 > 0) & ~(s2 > 0)).sum()),
                                  "escolas_so_2o": int(((s2 > 0) & ~(s1 > 0)).sum())})
        logger.info("%d: EJA com %d variáveis em %d métodos", ano, len(mapa), len(metodos))
    return {"3a_eja_1996_semestres": pd.DataFrame(semestres), "3b_eja_totais_por_metodo": pd.DataFrame(totais),
            "3c_eja_consistencia_por_escola": pd.DataFrame(consist)}


def _agrupar(mapa: dict) -> dict[tuple[str, str], list[str]]:
    g = defaultdict(list)
    for v, (m, n, _) in mapa.items():
        g[(m, n)].append(v)
    return dict(sorted(g.items()))


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO)
    ap.add_argument("--dicionario", type=Path, default=DIC_PADRAO)
    ap.add_argument("--ano-inicio", type=int, default=1996, help="1996 entra só na EJA (semestres)")
    ap.add_argument("--ano-fim", type=int, default=2006)
    ap.add_argument("--etapas", default="1,2,3")
    ap.add_argument("--saida", type=Path, default=Path("saida_b9706"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dic = pd.read_csv(args.dicionario, encoding="utf-8-sig", dtype={"variavel": str, "arquivo": str})
    dic["ano"] = pd.to_numeric(dic["ano"], errors="coerce")
    dic = dic[dic.arquivo == "CENSOESC"]
    etapas = {e.strip() for e in args.etapas.split(",")}
    anos = range(args.ano_inicio, args.ano_fim + 1)
    anos_bloco = range(max(args.ano_inicio, 1997), args.ano_fim + 1)
    args.saida.mkdir(parents=True, exist_ok=True)
    saidas: dict[str, pd.DataFrame] = {}
    if "1" in etapas:
        saidas.update(etapa1(dic, args.raiz, anos_bloco))
    if "2" in etapas:
        saidas.update(etapa2(dic, anos))
    if "3" in etapas:
        saidas.update(etapa3(dic, args.raiz, anos))
    for nome, df in saidas.items():
        df.to_csv(args.saida / f"{nome}.csv", index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 230)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.max_colwidth", 60)
    if "1_correspondencia_por_ano" in saidas:
        c = saidas["1_correspondencia_por_ano"]
        print("\n=== 1. Colunas sem variável em algum ano (coluna: anos sem) ===")
        sem = c[c.variavel == "(nenhuma neste ano)"].groupby("coluna_modelo").ano.apply(
            lambda s: ",".join(map(str, sorted(s))))
        print(sem.to_string() if len(sem) else "  nenhuma")
        print("\n=== 1. Variáveis que parecem CONTAGEM, não flag s/n (pct_numerico > 50) ===")
        cont = c[(c.pct_numerico.fillna(0) > 50) & c.tabela.eq("infraestrutura") | (c.pct_numerico.fillna(0) > 50)
                 & c.coluna_modelo.str.startswith(("nivel", "local", "ocup", "area", "escola_", "predio", "mod_"))]
        print(cont.groupby(["coluna_modelo", "variavel"]).ano.apply(lambda s: f"{s.min()}-{s.max()}").to_string()
              if len(cont) else "  nenhuma")
        print(f"\nNão mapeadas: {len(saidas['1b_nao_mapeadas'])} (ver 1b_nao_mapeadas.csv)")
    if "2b_faixas_de_formacao_por_ano" in saidas:
        f = saidas["2b_faixas_de_formacao_por_ano"]
        print("\n=== 2. Faixas de formação por ano (as 7 de 1996 devem se repetir) ===")
        print(f.pivot_table(index="formacao", columns="ano", values="variaveis", aggfunc="sum").fillna(0)
              .astype(int).to_string())
        print("Blocos (etapa) por ano: ver 2c_blocos_por_ano.csv; a etapa de cada bloco está no Leia-me (coluna pagina)")
    if "3b_eja_totais_por_metodo" in saidas:
        print("\n=== 3a. 1996: 1º x 2º semestre ===")
        print(saidas["3a_eja_1996_semestres"].to_string(index=False) if len(saidas["3a_eja_1996_semestres"]) else "  -")
        t = saidas["3b_eja_totais_por_metodo"]
        print("\n=== 3b. EJA: total por método (soma dos níveis) ===")
        print(t.pivot_table(index="ano", columns="metodo", values="total", aggfunc="sum").round(0).to_string())
        print("\n=== 3c. Consistência por escola entre métodos ===")
        print(saidas["3c_eja_consistencia_por_escola"].to_string(index=False))
    print(f"\nArquivos em: {args.saida.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
