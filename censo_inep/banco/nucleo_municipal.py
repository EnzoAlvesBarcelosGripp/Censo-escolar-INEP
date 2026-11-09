"""
Monta nucleo.municipal dentro do MySQL: uma linha por ano x município x dependência x situação de
funcionamento, com escolas, matrículas, turmas, docentes e salas comparáveis de 1996 a 2025.
Só agrega (somas e contagens das tabelas por escola de cada bloco). As regras de cada coluna estão no
cabeçalho de nucleo.municipal em ddl nucleo.sql.

Como funciona, por bloco (1996, 9706, 0725):
  1. CONFERIR que nenhuma escola tem município, dependência ou situação vazios (são a chave).
  2. GRAVAR com UM INSERT ... SELECT ... GROUP BY. Em 1996 e 1997-2006 as etapas estão em linhas
     (escola_etapa), então uma subconsulta primeiro "deita" as etapas de cada escola em colunas
     (SUM(CASE WHEN etapa = ... THEN ... END)) e depois se agrupa por município.
  3. VERIFICAR antes do COMMIT, por um caminho independente: os totais Brasil de cada coluna, por ano,
     calculados direto das tabelas de origem (sem o GROUP BY por município), têm de ser iguais à soma do
     núcleo; e a soma de n_escolas tem de ser igual ao número de escolas do ano.
  4. COMMIT por bloco. Rodar de novo apaga e regrava só os anos do bloco (idempotente).
No fim, mostra os totais Brasil por ano (para comparar com as Sinopses Estatísticas).

Pré-requisito: ddl nucleo.sql rodado (cria nucleo.municipal).

Uso:
  python montar_nucleo_municipal.py                      # 1996, 9706 e 0725
  python montar_nucleo_municipal.py --blocos 9706
  python montar_nucleo_municipal.py --mostrar-sql --blocos 1996   # só imprime o SQL
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from carregar_mysql import conectar  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("nucleo_municipal")

DESTINO = "nucleo.municipal"
COLUNAS = ["matriculas_creche", "matriculas_pre", "matriculas_fund_ai", "matriculas_fund_af", "matriculas_medio",
           "matriculas_eja", "matriculas_masc", "matriculas_fem", "turmas_creche", "turmas_pre",
           "turmas_infantil_unificada", "turmas_fund", "turmas_medio", "docentes", "salas_utilizadas"]

# ---------------------------------------------------------------------------------------------------------
# 1996 e 1997-2006: as medidas saem de escola_etapa (uma linha por escola x etapa).
# Cada PARTE = (etapas, coluna de escola_etapa). Cada coluna do núcleo = soma de uma ou mais partes.
# As etapas "totais" (eja, eja_fund, fund_total, especial, medio_tecnico) não têm matrícula (conferido em
# 05/11), então somar as etapas não duplica nada.
# ---------------------------------------------------------------------------------------------------------
EJA_9706 = ("eja", "eja_fund", "eja_fund_ai", "eja_fund_af", "eja_medio")   # eja_prof fica fora (decisão 05/11)
MAT_ETAPAS = ("creche", "pre", "ca", "fund_ini", "fund_fin", "medio", "normal")

PARTES_COMUNS: dict[str, tuple[tuple[str, ...], str]] = {
    "mat_creche": (("creche",), "matriculas"),
    "mat_pre": (("pre",), "matriculas"),
    "mat_fund_ai": (("ca", "fund_ini"), "matriculas"),          # 1996: CA separada; 1997+: já dentro de fund_ini
    "mat_fund_af": (("fund_fin",), "matriculas"),
    "mat_medio": (("medio", "normal"), "matriculas"),
    "tur_creche": (("creche",), "turmas"),
    "tur_pre": (("pre",), "turmas"),
    "tur_inf_unif": (("infantil_unif",), "turmas"),
    "tur_fund_ser": (("ca", "fund_ini", "fund_fin"), "turmas"),
    "tur_multi": (("fund_total",), "turmas_multisseriadas"),
    "tur_medio": (("medio", "normal"), "turmas"),
}
PARTES_9706_EXTRA = {
    "mat_eja": (EJA_9706, "matriculas"),
    "mat_eja_semi": (EJA_9706, "matriculas_semipresencial"),
    "mat_masc": (MAT_ETAPAS + EJA_9706, "matriculas_masc"),
    "mat_fem": (MAT_ETAPAS + EJA_9706, "matriculas_fem"),
}
PARTES_1996_EXTRA = {                                            # 1996 sem EJA no núcleo (decisão 05/11)
    "mat_masc": (MAT_ETAPAS, "matriculas_masc"),
    "mat_fem": (MAT_ETAPAS, "matriculas_fem"),
}
COMPOSICAO_ANTIGOS = {                                           # coluna do núcleo -> partes somadas
    "matriculas_creche": ["mat_creche"], "matriculas_pre": ["mat_pre"],
    "matriculas_fund_ai": ["mat_fund_ai"], "matriculas_fund_af": ["mat_fund_af"],
    "matriculas_medio": ["mat_medio"], "matriculas_eja": ["mat_eja", "mat_eja_semi"],
    "matriculas_masc": ["mat_masc"], "matriculas_fem": ["mat_fem"],
    "turmas_creche": ["tur_creche"], "turmas_pre": ["tur_pre"], "turmas_infantil_unificada": ["tur_inf_unif"],
    "turmas_fund": ["tur_fund_ser", "tur_multi"], "turmas_medio": ["tur_medio"],
}

# 2007-2025: as medidas já vêm prontas, uma coluna por escola, em tabelas por tema.
COLUNAS_0725: dict[str, tuple[str, str] | None] = {
    "matriculas_creche": ("matricula", "qt_mat_inf_cre"), "matriculas_pre": ("matricula", "qt_mat_inf_pre"),
    "matriculas_fund_ai": ("matricula", "qt_mat_fund_ai"), "matriculas_fund_af": ("matricula", "qt_mat_fund_af"),
    "matriculas_medio": ("matricula", "qt_mat_med"), "matriculas_eja": ("matricula", "qt_mat_eja"),
    "matriculas_masc": ("matricula", "qt_mat_bas_masc"), "matriculas_fem": ("matricula", "qt_mat_bas_fem"),
    "turmas_creche": ("turma", "qt_tur_inf_cre"), "turmas_pre": ("turma", "qt_tur_inf_pre"),
    "turmas_infantil_unificada": None,
    "turmas_fund": ("turma", "qt_tur_fund"), "turmas_medio": ("turma", "qt_tur_med"),
    "docentes": ("docente", "qt_doc_bas"), "salas_utilizadas": ("infraestrutura", "qt_salas_utilizadas"),
}


@dataclass(frozen=True)
class Bloco:
    nome: str
    banco: str
    chave: str
    municipio: str
    dependencia: str
    situacao: str
    anos: tuple[int, int]


BLOCOS = {
    "1996": Bloco("1996", "b1996", "id_ie", "cod_municipio", "dependencia", "situacao_funcionamento", (1996, 1996)),
    "9706": Bloco("9706", "b9706", "id_ie", "cod_municipio", "dependencia", "situacao_funcionamento", (1997, 2006)),
    "0725": Bloco("0725", "b0725", "co_entidade", "co_municipio", "tp_dependencia", "tp_situacao_funcionamento",
                  (2007, 2025)),
}


def somar(exprs: list[str]) -> str:
    """Soma de vários SUM(): NULL só se TODOS forem NULL (nenhuma escola informou); senão NULL conta como 0."""
    if not exprs:
        return "NULL"
    if len(exprs) == 1:
        return exprs[0]
    return f"IF(COALESCE({', '.join(exprs)}) IS NULL, NULL, {' + '.join(f'COALESCE({e}, 0)' for e in exprs)})"


def salas(no_estab: str, fora: str) -> str:
    return f"IF({no_estab} IS NULL AND {fora} IS NULL, NULL, COALESCE({no_estab}, 0) + COALESCE({fora}, 0))"


def partes_do_bloco(b: Bloco) -> dict[str, tuple[tuple[str, ...], str]]:
    return {**PARTES_COMUNS, **(PARTES_9706_EXTRA if b.nome == "9706" else PARTES_1996_EXTRA)}


def caso(etapas: tuple[str, ...], coluna: str, alias: str = "ee") -> str:
    lista = ", ".join(f"'{e}'" for e in etapas)
    return f"SUM(CASE WHEN {alias}.etapa IN ({lista}) THEN {alias}.{coluna} END)"


def medidas_antigos(b: Bloco) -> dict[str, str]:
    """Coluna do núcleo -> expressão no nível município (sobre a subconsulta x, a escola e e a infra i)."""
    partes = partes_do_bloco(b)
    m = {col: somar([f"SUM(x.{p})" for p in ps if p in partes]) for col, ps in COMPOSICAO_ANTIGOS.items()}
    m["docentes"] = "SUM(e.prof_total_escola)"
    m["salas_utilizadas"] = f"SUM({salas('i.salas_utilizadas_no_estab', 'i.salas_utilizadas_fora')})"
    return m


def sql_antigos(b: Bloco) -> str:
    partes = partes_do_bloco(b)
    deitadas = ",\n      ".join(f"{caso(et, col)} AS {nome}" for nome, (et, col) in partes.items())
    medidas = ",\n  ".join(f"{expr} AS {col}" for col, expr in medidas_antigos(b).items())
    return (f"INSERT INTO {DESTINO} (ano, cod_municipio, dependencia, situacao, n_escolas, {', '.join(COLUNAS)})\n"
            f"SELECT e.ano, e.{b.municipio}, e.{b.dependencia}, e.{b.situacao}, COUNT(*),\n  {medidas}\n"
            f"FROM {b.banco}.escola e\n"
            f"LEFT JOIN {b.banco}.infraestrutura i ON i.ano = e.ano AND i.{b.chave} = e.{b.chave}\n"
            f"LEFT JOIN (\n  SELECT ee.ano, ee.{b.chave},\n      {deitadas}\n"
            f"  FROM {b.banco}.escola_etapa ee GROUP BY ee.ano, ee.{b.chave}\n"
            f") x ON x.ano = e.ano AND x.{b.chave} = e.{b.chave}\n"
            f"GROUP BY e.ano, e.{b.municipio}, e.{b.dependencia}, e.{b.situacao}")


def sql_0725(b: Bloco) -> str:
    alias = {"matricula": "m", "turma": "t", "docente": "d", "infraestrutura": "i"}
    medidas = ",\n  ".join(f"SUM({alias[o[0]]}.{o[1]})" if o else "NULL" for o in COLUNAS_0725.values())
    joins = "".join(f"\nLEFT JOIN {b.banco}.{t} {a} ON {a}.ano = e.ano AND {a}.{b.chave} = e.{b.chave}"
                    for t, a in alias.items())
    return (f"INSERT INTO {DESTINO} (ano, cod_municipio, dependencia, situacao, n_escolas, {', '.join(COLUNAS)})\n"
            f"SELECT e.ano, e.{b.municipio}, e.{b.dependencia}, e.{b.situacao}, COUNT(*),\n  {medidas}\n"
            f"FROM {b.banco}.escola e{joins}\n"
            f"GROUP BY e.ano, e.{b.municipio}, e.{b.dependencia}, e.{b.situacao}")


def totais_origem(cur, b: Bloco) -> dict[int, list]:
    """Totais Brasil por ano, direto das tabelas de origem, sem GROUP BY por município (caminho independente)."""
    tot: dict[int, dict[str, object]] = {}

    def juntar(sql: str, nomes: list[str]) -> None:
        cur.execute(sql)
        for ano, *vals in cur.fetchall():
            tot.setdefault(int(ano), {}).update(zip(nomes, vals))

    juntar(f"SELECT ano, COUNT(*) FROM {b.banco}.escola GROUP BY ano", ["n_escolas"])
    if b.nome == "0725":
        por_tabela: dict[str, list[str]] = {}
        for col, o in COLUNAS_0725.items():
            if o:
                por_tabela.setdefault(o[0], []).append(col)
        for tabela, cols in por_tabela.items():
            exprs = ", ".join(f"SUM({COLUNAS_0725[c][1]})" for c in cols)
            juntar(f"SELECT ano, {exprs} FROM {b.banco}.{tabela} GROUP BY ano", cols)
        for t in tot.values():
            t["turmas_infantil_unificada"] = None
    else:
        partes = partes_do_bloco(b)
        exprs = {col: somar([caso(*partes[p], alias="ee") for p in ps if p in partes])
                 for col, ps in COMPOSICAO_ANTIGOS.items()}
        juntar(f"SELECT ano, {', '.join(exprs.values())} FROM {b.banco}.escola_etapa ee GROUP BY ano", list(exprs))
        juntar(f"SELECT ano, SUM(prof_total_escola) FROM {b.banco}.escola GROUP BY ano", ["docentes"])
        juntar(f"SELECT ano, SUM({salas('salas_utilizadas_no_estab', 'salas_utilizadas_fora')}) "
               f"FROM {b.banco}.infraestrutura GROUP BY ano", ["salas_utilizadas"])
    return {ano: [t.get(c) for c in ["n_escolas", *COLUNAS]] for ano, t in tot.items()}


def totais_nucleo(cur, b: Bloco) -> dict[int, list]:
    cur.execute(f"SELECT ano, SUM(n_escolas), {', '.join(f'SUM({c})' for c in COLUNAS)} FROM {DESTINO} "
                f"WHERE ano BETWEEN %s AND %s GROUP BY ano", b.anos)
    return {int(ano): list(vals) for ano, *vals in cur.fetchall()}


def normal(v: object) -> int | None:
    return None if v is None else int(v) if isinstance(v, (int, Decimal)) else int(float(v))


def verificar(cur, b: Bloco) -> dict[int, list]:
    origem, nucleo = totais_origem(cur, b), totais_nucleo(cur, b)
    problemas = []
    if sorted(origem) != sorted(nucleo):
        problemas.append(f"anos diferentes: origem {sorted(origem)}, núcleo {sorted(nucleo)}")
    for ano in sorted(set(origem) & set(nucleo)):
        for col, o, n in zip(["n_escolas", *COLUNAS], origem[ano], nucleo[ano]):
            if normal(o) != normal(n):
                problemas.append(f"{ano} {col}: origem {o}, núcleo {n}")
    if problemas:
        raise SystemExit("Verificação falhou (nada foi confirmado):\n  - " + "\n  - ".join(problemas[:30]))
    logger.info("%s: verificação ok (%d anos x %d colunas iguais à origem)", b.banco, len(nucleo), len(COLUNAS) + 1)
    return nucleo


def conferir_chaves(cur, b: Bloco) -> None:
    cur.execute(f"SELECT ano, COUNT(*) FROM {b.banco}.escola WHERE {b.municipio} IS NULL OR "
                f"{b.dependencia} IS NULL OR {b.situacao} IS NULL GROUP BY ano")
    ruins = cur.fetchall()
    if ruins:
        raise SystemExit(f"{b.banco}.escola tem escolas sem chave do núcleo: {ruins}. Nada foi gravado.")


def mostrar_totais(totais: dict[int, list]) -> None:
    mostrar = ["n_escolas", "matriculas_creche", "matriculas_pre", "matriculas_fund_ai", "matriculas_fund_af",
               "matriculas_medio", "matriculas_eja", "turmas_fund", "docentes"]
    idx = [["n_escolas", *COLUNAS].index(c) for c in mostrar]
    curtos = ["escolas", "creche", "pre", "fund_ai", "fund_af", "medio", "eja", "tur_fund", "docentes"]
    print("\nTotais Brasil (para comparar com as Sinopses):")
    print("  ano  " + "".join(f"{c:>12}" for c in curtos))
    for ano in sorted(totais):
        v = totais[ano]
        print(f"  {ano} " + "".join(f"{'-' if v[i] is None else f'{int(v[i]):,}':>12}" for i in idx))


def montar(conn, b: Bloco, mostrar_sql: bool) -> dict[int, list]:
    sql = sql_0725(b) if b.nome == "0725" else sql_antigos(b)
    if mostrar_sql:
        print(f"-- bloco {b.nome}\n{sql};\n")
        return {}
    cur = conn.cursor()
    t0 = time.perf_counter()
    conferir_chaves(cur, b)
    try:
        cur.execute(f"DELETE FROM {DESTINO} WHERE ano BETWEEN %s AND %s", b.anos)
        logger.info("%s: agregando (uma passada pelas escolas)...", b.banco)
        cur.execute(sql)
        logger.info("%s: %s linhas gravadas em %.0f s; verificando...", b.banco, f"{cur.rowcount:,}",
                    time.perf_counter() - t0)
        totais = verificar(cur, b)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    logger.info("%s: concluído em %.0f s", b.banco, time.perf_counter() - t0)
    return totais


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blocos", nargs="+", choices=[*BLOCOS, "todos"], default=["todos"])
    ap.add_argument("--mostrar-sql", action="store_true", help="só imprime o SQL gerado; não executa nada")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--porta", type=int, default=3306)
    ap.add_argument("--usuario", default="root")
    ap.add_argument("--socket", default="", help="socket unix (só para testes em Linux)")
    args = ap.parse_args()
    nomes = list(BLOCOS) if "todos" in args.blocos else [n for n in BLOCOS if n in args.blocos]
    if args.mostrar_sql:
        for n in nomes:
            montar(None, BLOCOS[n], True)
        return 0
    conn = conectar(args)
    totais: dict[int, list] = {}
    try:
        for n in nomes:
            totais.update(montar(conn, BLOCOS[n], False))
    finally:
        conn.close()
    mostrar_totais(totais)
    return 0


if __name__ == "__main__":
    sys.exit(main())
