"""
Exporta para CSV as tabelas do MySQL (por padrão: dim, b1996, b9706, b0725 e nucleo), uma pasta por database.
É a SAÍDA ÚNICA do projeto: tudo o que vai para o Agente de IA (ou, um dia, para o S3) sai daqui, do banco já
conferido. Os CSVs dos geradores são intermediários (servem para carregar o banco).

Saída: <saida>/<database>/<tabela>.csv — UM arquivo por tabela, com todos os anos
       (padrão: C:/dados_inep/data_cen/dados_processados/microdados).

Formato: UTF-8, vírgula, cabeçalho com os nomes das colunas, NULL = campo vazio (o mesmo que os carregadores
         usam: vazio sempre significa "não coletado"; zero continua zero).

Como funciona, por tabela:
  1. Lê com cursor de servidor (SSCursor): as linhas vêm em fluxo, sem carregar 80 milhões na memória.
  2. Escreve num arquivo .parcial e só renomeia para .csv no fim: um arquivo .csv nunca fica pela metade
     (se a luz cair, sobra um .parcial, que a próxima execução sobrescreve). Rodar de novo = mesmo resultado.
  3. Confere: linhas escritas = COUNT(*) da tabela. Se não bater, apaga o .parcial e para.

Uso:
  python exportar_csv.py                                  # tudo: dim, b1996, b9706, b0725, nucleo
  python exportar_csv.py --bancos dim nucleo              # só dimensões e núcleo
  python exportar_csv.py --bancos nucleo --tabelas municipal_infraestrutura
  python exportar_csv.py --saida "D:/outra_pasta"
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import pymysql.cursors

sys.path.insert(0, str(Path(__file__).resolve().parent))
from carregar_mysql import conectar  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("exportar_csv")

SAIDA_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/microdados")
PROGRESSO = 5_000_000          # tabelas grandes: uma linha de log a cada 5 milhões de linhas


def texto(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):           # SUM() do MySQL volta como Decimal: "12" e não "12.0"
        return format(v, "f")
    return str(v)


def tabelas_do_banco(cur, banco: str) -> list[str]:
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
                "AND table_type = 'BASE TABLE' ORDER BY table_name", (banco,))
    return [r[0] for r in cur.fetchall()]


def chave_primaria(cur, banco: str, tabela: str) -> list[str]:
    cur.execute("SELECT column_name FROM information_schema.key_column_usage WHERE table_schema = %s "
                "AND table_name = %s AND constraint_name = 'PRIMARY' ORDER BY ordinal_position", (banco, tabela))
    return [r[0] for r in cur.fetchall()]


def exportar(conn, banco: str, tabela: str, destino: Path) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM `{banco}`.`{tabela}`")
    (esperado,) = cur.fetchone()
    pk = chave_primaria(cur, banco, tabela)
    ordem = "ORDER BY " + ", ".join(f"`{c}`" for c in pk) if pk else ""   # mesma ordem sempre: arquivos comparáveis

    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(".parcial")
    escritas = 0
    ss = conn.cursor(pymysql.cursors.SSCursor)
    try:
        ss.execute(f"SELECT * FROM `{banco}`.`{tabela}` {ordem}")
        with parcial.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow([d[0] for d in ss.description])
            while lote := ss.fetchmany(50_000):
                w.writerows([texto(v) for v in linha] for linha in lote)
                antes, escritas = escritas, escritas + len(lote)
                if esperado > PROGRESSO and escritas // PROGRESSO > antes // PROGRESSO:
                    logger.info("%s.%s: %s de %s linhas", banco, tabela, f"{escritas:,}", f"{esperado:,}")
    finally:
        ss.close()
    if escritas != esperado:
        parcial.unlink(missing_ok=True)
        raise SystemExit(f"{banco}.{tabela}: {escritas} linhas escritas, mas a tabela tem {esperado}. "
                         "Nada foi gravado.")
    os.replace(parcial, destino)        # troca atômica: o .csv antigo só some quando o novo está completo
    return escritas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", type=Path, default=SAIDA_PADRAO, help="pasta de saída (padrão: %(default)s)")
    ap.add_argument("--bancos", nargs="+", default=["dim", "b1996", "b9706", "b0725", "nucleo"])
    ap.add_argument("--tabelas", nargs="+", default=None, help="só estas tabelas (de qualquer banco listado)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--porta", type=int, default=3306)
    ap.add_argument("--usuario", default="root")
    ap.add_argument("--socket", default="", help="socket unix (só para testes em Linux)")
    args = ap.parse_args()

    conn = conectar(args)
    t0 = time.perf_counter()
    total = 0
    try:
        cur = conn.cursor()
        for banco in args.bancos:
            for tabela in tabelas_do_banco(cur, banco):
                if args.tabelas and tabela not in args.tabelas:
                    continue
                t1 = time.perf_counter()
                n = exportar(conn, banco, tabela, args.saida / banco / f"{tabela}.csv")
                total += n
                logger.info("%s/%s.csv: %s linhas (%.0f s)", banco, tabela, f"{n:,}", time.perf_counter() - t1)
    finally:
        conn.close()
    print(f"\nExportação concluída: {total:,} linhas em {time.perf_counter() - t0:.0f} s -> {args.saida.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())