"""
Teste da modelagem SEM SQL Server, usando SQLite (ja vem com o Python, nada para baixar).

Uso:
  python testar_sqlite.py                      -> gera um CSV sintetico e testa com ele
  python testar_sqlite.py CENSOESC_1996.csv    -> testa com o arquivo real

Precisa de: pandas (e carga_censo_1996.py + criar_banco.sql na mesma pasta).
O que valida: DDL (adaptado), PKs, FKs, o fluxo real de carga e o tamanho dos textos.
O que NAO valida: particularidades do T-SQL (tipos, BIT, collation, performance).
"""
import os
import re
import sqlite3
import sys
import types

# O loader importa pyodbc/sqlalchemy no topo; aqui nao sao necessarios.
for nome in ("pyodbc", "sqlalchemy"):
    try:
        __import__(nome)
    except ImportError:
        m = types.ModuleType(nome)
        m.create_engine = lambda *a, **k: None
        sys.modules[nome] = m

import pandas as pd
import carga_censo_1996 as c

DB = "teste_censo.sqlite"
TAMANHOS = {  # tamanhos declarados no DDL (SQLite nao os impoe; SQL Server sim)
    "Estadual": {"SIGLA": 2, "UF": 50},
    "Municipio": {"CODMUNIC": 12, "MUNIC": 50, "SIGLA": 2},
    "Instituicao_Imutavel": {"REG_CNAS": 15},
    "Instituicao_Anual": {"CODMUNIC": 12, "DEP": 10, "LOC": 10, "CODFUNC": 11},
}


def gerar_csv_sintetico(caminho, n=3000):
    import numpy as np
    dic_path = "96.csv"
    if not os.path.exists(dic_path):
        sys.exit("Para o teste sintetico preciso do 96.csv (dicionario) nesta pasta.")
    dic = pd.read_csv(dic_path)
    rng = np.random.default_rng(1)
    df = pd.DataFrame({v: rng.integers(0, 6, n).astype(str) for v in dic.variavel})
    df["MASCARA"] = np.arange(1, n + 1).astype(str)
    df["ANO"] = "1996"
    ufs = {"SP": "SAO PAULO", "MG": "MINAS GERAIS", "RJ": "RIO DE JANEIRO"}
    df["SIGLA"] = [list(ufs)[i % 3] for i in range(n)]
    df["UF"] = df["SIGLA"].map(ufs)
    df["CODMUNIC"] = [f"{i % 3}{i % 50:04d}" for i in range(n)]
    df["MUNIC"] = "MUN " + df["CODMUNIC"]
    for f in ["NIVELPRE", "NIVELCA", "NIV_F1A4_8", "NIV_F5A8_8", "NIVELMEDIO", "ENSREGULAR", "ENSSUPLET", "ENSESPECIAL"]:
        df[f] = rng.choice(["1", "2", ""], n)
    df.to_csv(caminho, sep=";", index=False, encoding="latin-1")


def main():
    csv = sys.argv[1] if len(sys.argv) > 1 else r"C:\dados_inep\data_cen\Microdados da Educação Básica\1996\CENSOESC_1996.csv"
    if csv is None:
        csv = "CENSOESC_sintetico.csv"
        gerar_csv_sintetico(csv)
        print(f"[info] CSV sintetico gerado: {csv}")
    c.CSV_PATH = csv
    c.SAIDA_VIOLACOES = "violacoes_professores_teste.csv"

    # --- DDL adaptado para SQLite (tira 'dbo.', separa blocos GO)
    if os.path.exists(DB):
        os.remove(DB)
    cn = sqlite3.connect(DB)
    cn.execute("PRAGMA foreign_keys = ON")
    sql = open(c.SQL_PATH, encoding="utf-8").read().replace("dbo.", "")
    blocos = [b.strip() for b in re.split(r"^\s*GO\s*$", sql, flags=re.M | re.I) if b.strip()]
    for b in blocos:
        cn.executescript(b)
    print(f"[ok] DDL executado ({len(blocos)} blocos) - PKs e FKs ativas")

    def inserir_sqlite(tabela, df):
        if len(df):
            c.para_banco(df).to_sql(tabela, cn, if_exists="append", index=False, chunksize=2000)

    # --- fluxo REAL de carga (o mesmo do SQL Server), destino SQLite
    sep, enc = c.detectar_formato(csv)
    nec = c.colunas_necessarias()
    cab = c.normalizar(pd.read_csv(csv, sep=sep, encoding=enc, nrows=0, dtype=str))
    faltando = sorted(nec - set(cab.columns))
    if faltando:
        sys.exit(f"[erro] colunas ausentes no CSV ({len(faltando)}): {faltando[:30]}")
    c.executar_carga(inserir_sqlite, sep, enc, nec)
    cn.commit()

    # --- verificacoes
    print("\n== Contagens")
    for t in c.TODAS_TABELAS:
        print(f"  {t:<22} {cn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>9}")

    print("\n== Integridade referencial")
    fk = cn.execute("PRAGMA foreign_key_check").fetchall()
    print("  FKs violadas:", len(fk))

    print("\n== Textos x tamanho declarado no DDL (o SQL Server daria 'truncated')")
    problemas = 0
    for t, cols in TAMANHOS.items():
        for col, lim in cols.items():
            mx = cn.execute(f"SELECT MAX(LENGTH({col})) FROM {t}").fetchone()[0] or 0
            if mx > lim:
                problemas += 1
                print(f"  [ERRO] {t}.{col}: maior valor tem {mx} caracteres, limite {lim}")
    print("  nenhum estouro de tamanho" if not problemas else f"  {problemas} coluna(s) estourando")

    print("\n== PK/FK reagem? (insercao invalida deve falhar)")
    for desc, cmd in [("Turmas para escola/ano inexistente", "INSERT INTO Turmas (MASCARA, ANO) VALUES (-1, 1996)"),
                      ("Escola/ano duplicado", "INSERT INTO Infraestrutura (MASCARA, ANO) SELECT MASCARA, ANO FROM Infraestrutura LIMIT 1")]:
        try:
            cn.execute(cmd)
            print(f"  [ERRO] aceitou: {desc}")
        except sqlite3.IntegrityError as e:
            print(f"  ok, recusou: {desc} ({e})")
    cn.rollback()

    print("\n== Consulta de exemplo: matricula por turno x por sexo (deve bater)")
    q = """SELECT COUNT(*) total,
                  SUM(CASE WHEN COALESCE(d.Medio_matriculas_diurno,0)+COALESCE(d.Medio_matriculas_noturno,0)
                          = COALESCE(d.Medio_masculino,0)+COALESCE(d.Medio_feminino,0) THEN 1 ELSE 0 END) batem
           FROM Matriculas d"""
    tot, batem = cn.execute(q).fetchone()
    print(f"  Medio: {batem} de {tot} escolas batem (em dado real, divergencias merecem investigacao)")
    cn.close()
    print(f"\n[fim] banco de teste salvo em {DB} (abra no DB Browser for SQLite, se quiser olhar)")


if __name__ == "__main__":
    main()