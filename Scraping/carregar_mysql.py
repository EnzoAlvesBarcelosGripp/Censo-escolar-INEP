"""
Carrega no MySQL os CSVs gerados pelos geradores de tabelas (dim, 1996, 1997-2006 e 2007-2025).

Entrada : <raiz>/dim/estado.csv e municipio.csv            (gerar_dim.py)
          <raiz>/Microdados_1996/<tabela>.csv               (gerar_tabelas_1996.py)
          <raiz>/Microdados_9706/<tabela>.csv               (gerar_tabelas_9706.py, arquivos consolidados)
          <raiz>/Microdados_0725/<tabela>.csv               (gerar_tabelas_0725.py, arquivos consolidados)
Destino : os databases criados pelos DDLs (dim, b1996, b9706, b0725). Os DDLs precisam ter rodado antes.

Como funciona, em quatro passos:
  1. CONFERÊNCIA (nada é alterado no banco): para cada tabela, o CSV existe? o cabeçalho tem exatamente as
     colunas da tabela? Se algo estiver errado, o script lista TUDO e para. É rápida (só lê o cabeçalho);
     as linhas de cada arquivo só são contadas na hora da carga (ou na conferência, com --contar).
  2. ESVAZIAR o bloco (TRUNCATE, dos filhos para os pais): é isso que torna o script idempotente. Rodar duas
     vezes dá o mesmo banco, nunca dados duplicados. O bloco `dim` não é esvaziado (outras tabelas apontam
     para ele): usa uma tabela temporária e um UPSERT (INSERT ... ON DUPLICATE KEY UPDATE).
  3. CARREGAR cada tabela com LOAD DATA LOCAL INFILE, dos pais para os filhos (as chaves estrangeiras são
     conferidas pelo MySQL linha a linha). Campo vazio vira NULL (NULLIF) e o NULL significa "não coletado".
  4. VERIFICAR antes de confirmar: o LOAD DATA LOCAL transforma erros em AVISOS e segue em frente (linha ruim
     é descartada em silêncio!). Por isso o script lê os avisos e compara linhas do arquivo x linhas gravadas.
     Qualquer diferença desfaz a tabela (rollback) e para.

Exemplos:
  python carregar_mysql.py --conferir                  # só o passo 1: instantâneo, não mexe no banco
  python carregar_mysql.py                             # tudo: dim, 1996, 9706, 0725
  python carregar_mysql.py --blocos 0725               # só o bloco 2007-2025
  python carregar_mysql.py --blocos dim 1996 9706      # senha: variável de ambiente MYSQL_PWD ou prompt

Requisitos: pip install pymysql cryptography
            no servidor: SET GLOBAL local_infile = 1;   (e, para valer depois de reiniciar, local-infile=1 em [mysqld] do my.ini)
"""
from __future__ import annotations

import argparse
import csv
import getpass
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymysql

sys.dont_write_bytecode = True
logger = logging.getLogger("carregar_mysql")

RAIZ_PADRAO = Path("C:/dados_inep/data_cen/dados_processados")


# ===========================================================================
# 1. O que carregar e em que ordem
# ===========================================================================
@dataclass(frozen=True)
class Bloco:
    nome: str                       # como aparece na linha de comando
    banco: str                      # database (o "schema" do MySQL)
    pasta: str                      # subpasta de --raiz com os CSVs
    tabelas: tuple[str, ...]        # ORDEM DE CARGA: pais antes dos filhos (as FKs exigem)
    opcionais: frozenset[str] = frozenset()   # tabelas que ainda podem não ter CSV (são puladas)
    upsert: bool = False            # True = não esvazia; atualiza/insere (tabelas que outras tabelas referenciam)


BLOCOS: dict[str, Bloco] = {b.nome: b for b in (
    Bloco("dim", "dim", "dim", ("estado", "municipio"), upsert=True),
    Bloco("1996", "b1996", "Microdados_1996",
          ("escola", "infraestrutura", "escola_etapa", "pne_modalidade", "pne_deficiencia")),
    Bloco("9706", "b9706", "Microdados_9706",
          ("escola", "infraestrutura", "escola_etapa", "pne_modalidade", "pne_deficiencia"),
          opcionais=frozenset({"pne_modalidade", "pne_deficiencia"})),      # ainda não são geradas
    Bloco("0725", "b0725", "Microdados_0725",
          ("curso", "variavel", "dominio", "escola", "oferta", "infraestrutura", "equipamento",
           "profissionais", "matricula", "turma", "docente", "curso_tecnico")),
)}


@dataclass
class Plano:
    """Tudo o que o script sabe sobre UMA tabela depois da conferência."""
    bloco: Bloco
    tabela: str
    csv: Path
    colunas_csv: list[str] = field(default_factory=list)     # na ordem do arquivo
    colunas_db: dict[str, str] = field(default_factory=dict)  # minúscula -> nome no banco
    fim_de_linha: str = "\n"
    linhas: int = -1                # -1 = ainda não contadas (a contagem lê o arquivo inteiro: só na hora da carga)
    problemas: list[str] = field(default_factory=list)
    pular: bool = False             # tabela opcional sem CSV


# ===========================================================================
# 2. Conferência (passo 1): lê só o arquivo e o catálogo do banco
# ===========================================================================
def cabecalho(caminho: Path, sep: str) -> list[str]:
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f, delimiter=sep), [])


def fim_de_linha(caminho: Path) -> str:
    """'\\r\\n' (arquivo gerado no Windows) ou '\\n'. O LOAD DATA precisa saber qual é."""
    with open(caminho, "rb") as f:
        ini = f.read(1 << 16)
    i = ini.find(b"\n")
    return "\r\n" if i > 0 and ini[i - 1:i] == b"\r" else "\n"


def contar_linhas(caminho: Path) -> int:
    """Linhas de DADOS (sem o cabeçalho), lendo em blocos de 16 MB: memória constante mesmo com GBs."""
    n, ultimo = 0, b"\n"
    with open(caminho, "rb") as f:
        while bloco := f.read(1 << 24):
            n += bloco.count(b"\n")
            ultimo = bloco[-1:]
    if ultimo != b"\n":
        n += 1                                   # última linha sem quebra no final
    return max(n - 1, 0)


def colunas_do_banco(cur, banco: str, tabela: str) -> dict[str, str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                "ORDER BY ordinal_position", (banco, tabela))
    return {r[0].lower(): r[0] for r in cur.fetchall()}


def conferir(cur, bloco: Bloco, raiz: Path, sep: str, contar: bool) -> list[Plano]:
    planos = []
    for tabela in bloco.tabelas:
        p = Plano(bloco, tabela, raiz / bloco.pasta / f"{tabela}.csv")
        p.colunas_db = colunas_do_banco(cur, bloco.banco, tabela)
        if not p.colunas_db:
            p.problemas.append(f"tabela {bloco.banco}.{tabela} não existe no banco (rode o DDL do bloco)")
        if not p.csv.is_file():
            if tabela in bloco.opcionais:
                p.pular = True
                logger.warning("%s.%s: sem CSV (%s) -> tabela opcional pulada", bloco.banco, tabela, p.csv.name)
            else:
                p.problemas.append(f"arquivo não encontrado: {p.csv}")
            planos.append(p)
            continue
        p.colunas_csv = cabecalho(p.csv, sep)
        csv_min = [c.lower() for c in p.colunas_csv]
        if len(set(csv_min)) != len(csv_min):
            p.problemas.append("cabeçalho do CSV com coluna repetida")
        if p.colunas_db:
            faltam = sorted(set(p.colunas_db) - set(csv_min))
            sobram = sorted(set(csv_min) - set(p.colunas_db))
            if faltam:
                p.problemas.append(f"colunas da tabela que o CSV não tem ({len(faltam)}): {faltam[:8]}")
            if sobram:
                p.problemas.append(f"colunas do CSV que a tabela não tem ({len(sobram)}): {sobram[:8]}")
        p.fim_de_linha = fim_de_linha(p.csv)
        if contar:
            logger.info("contando linhas de %s (%.1f GB)...", p.csv.name, p.csv.stat().st_size / 1e9)
            p.linhas = contar_linhas(p.csv)
        planos.append(p)
    return planos


# ===========================================================================
# 3. O comando LOAD DATA
# ===========================================================================
def sql_load(p: Plano, destino: str, sep: str) -> str:
    """LOAD DATA que lê cada coluna do CSV numa variável (@c0, @c1...) e grava NULLIF(@cN, '').

    Sem isso, o modo estrito do MySQL rejeita '' em coluna numérica. Aqui vazio vira NULL em TODAS as colunas.
    ESCAPED BY '' desliga a barra invertida como escape: um texto com \\ ou \\n é gravado como está.
    As aspas dentro de campos ("") são tratadas pelo ENCLOSED BY."""
    variaveis = ", ".join(f"@c{i}" for i in range(len(p.colunas_csv)))
    atribuicoes = ",\n    ".join(f"`{p.colunas_db[c.lower()]}` = NULLIF(@c{i}, '')"
                                 for i, c in enumerate(p.colunas_csv))
    eol = r"'\r\n'" if p.fim_de_linha == "\r\n" else r"'\n'"
    return (f"LOAD DATA LOCAL INFILE %s\nINTO TABLE {destino}\nCHARACTER SET utf8mb4\n"
            f"FIELDS TERMINATED BY '{sep}' OPTIONALLY ENCLOSED BY '\"' ESCAPED BY ''\n"
            f"LINES TERMINATED BY {eol}\nIGNORE 1 LINES\n({variaveis})\nSET\n    {atribuicoes}")


def carregar_arquivo(conn, p: Plano, destino: str, sep: str) -> None:
    """Executa o LOAD e confere (avisos + contagem). Em qualquer problema: rollback e para."""
    cur = conn.cursor()
    try:
        gravadas = cur.execute(sql_load(p, destino, sep), (p.csv.as_posix(),))
        cur.execute("SHOW COUNT(*) WARNINGS")
        n_avisos = int(cur.fetchone()[0])
        if n_avisos:
            cur.execute("SHOW WARNINGS LIMIT 8")
            exemplos = "\n    ".join(f"[{c}] {m}" for _, c, m in cur.fetchall())
            raise SystemExit(f"{p.bloco.banco}.{p.tabela}: {n_avisos} avisos do MySQL (linhas descartadas ou "
                             f"valores alterados). Esta tabela não foi gravada: corrija o CSV e rode de novo (a carga do bloco recomeça do zero).\n    {exemplos}")
        cur.execute(f"SELECT COUNT(*) FROM {destino}")
        no_banco = int(cur.fetchone()[0])
        if gravadas != p.linhas or no_banco != p.linhas:
            raise SystemExit(f"{p.bloco.banco}.{p.tabela}: o arquivo tem {p.linhas} linhas, o LOAD gravou "
                             f"{gravadas} e a tabela tem {no_banco}. Esta tabela não foi gravada: corrija o CSV e rode de novo (a carga do bloco recomeça do zero).")
    except pymysql.MySQLError as e:
        conn.rollback()
        raise SystemExit(f"{p.bloco.banco}.{p.tabela}: erro do MySQL {e.args}. Esta tabela não foi gravada: corrija o CSV e rode de novo (a carga do bloco recomeça do zero).") from e
    except SystemExit:
        conn.rollback()
        raise


def esvaziar(conn, bloco: Bloco, planos: list[Plano]) -> None:
    """TRUNCATE dos filhos para os pais. FOREIGN_KEY_CHECKS=0 só durante isto (o MySQL não deixa truncar
    tabela referenciada por FK); logo depois volta a 1, antes de qualquer carga."""
    cur = conn.cursor()
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for p in reversed(planos):
        if p.colunas_db:
            cur.execute(f"TRUNCATE TABLE `{bloco.banco}`.`{p.tabela}`")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")


def chave_primaria(cur, banco: str, tabela: str) -> set[str]:
    cur.execute("SELECT column_name FROM information_schema.key_column_usage WHERE table_schema = %s "
                "AND table_name = %s AND constraint_name = 'PRIMARY'", (banco, tabela))
    return {r[0] for r in cur.fetchall()}


def upsert(conn, p: Plano, sep: str) -> None:
    """Tabelas de dimensão: carrega numa tabela TEMPORÁRIA (cópia da estrutura) e faz INSERT ... ON DUPLICATE
    KEY UPDATE. Linha nova entra, linha existente é atualizada, nada que outra tabela referencia é apagado."""
    conn.select_db(p.bloco.banco)               # tabela temporária precisa de um database corrente
    cur = conn.cursor()
    stg = f"stg_{p.tabela}"
    alvo = f"`{p.bloco.banco}`.`{p.tabela}`"
    cur.execute(f"DROP TEMPORARY TABLE IF EXISTS `{stg}`")
    cur.execute(f"CREATE TEMPORARY TABLE `{stg}` LIKE {alvo}")
    carregar_arquivo(conn, p, f"`{stg}`", sep)
    cols = list(p.colunas_db.values())
    lista = ", ".join(f"`{c}`" for c in cols)
    pk = chave_primaria(cur, p.bloco.banco, p.tabela)
    atualizar = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in cols if c not in pk)
    cur.execute(f"INSERT INTO {alvo} ({lista}) SELECT {lista} FROM `{stg}` ON DUPLICATE KEY UPDATE {atualizar}")
    cur.execute(f"DROP TEMPORARY TABLE `{stg}`")
    conn.commit()


# ===========================================================================
# 4. Execução
# ===========================================================================
def conectar(args) -> pymysql.connections.Connection:
    senha = os.environ.get("MYSQL_PWD")
    if senha is None:
        senha = getpass.getpass(f"Senha de {args.usuario}@{args.host}: ")
    try:
        conn = pymysql.connect(host=args.host, port=args.porta, user=args.usuario, password=senha,
                               charset="utf8mb4", local_infile=True, autocommit=False,
                               unix_socket=args.socket or None)
    except pymysql.MySQLError as e:
        raise SystemExit(f"Não foi possível conectar em {args.host}:{args.porta} -> {e.args}") from e
    cur = conn.cursor()
    cur.execute("SELECT @@GLOBAL.local_infile")
    if not cur.fetchone()[0]:
        raise SystemExit("O servidor está com local_infile desligado. No Workbench, como root, rode:\n"
                         "    SET GLOBAL local_infile = 1;\n"
                         "e, para valer depois de reiniciar o serviço, acrescente  local-infile=1  na seção "
                         "[mysqld] do my.ini.")
    cur.execute("SET SESSION foreign_key_checks = 1, unique_checks = 1")
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO, help="pasta dados_processados (padrão: %(default)s)")
    ap.add_argument("--blocos", nargs="+", choices=[*BLOCOS, "todos"], default=["todos"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--porta", type=int, default=3306)
    ap.add_argument("--usuario", default="root")
    ap.add_argument("--socket", default="", help="socket unix (só para testes em Linux)")
    ap.add_argument("--sep", default=",", help="separador dos CSVs (padrão: vírgula)")
    ap.add_argument("--conferir", action="store_true", help="só confere os arquivos e as colunas; não altera o banco")
    ap.add_argument("--contar", action="store_true",
                    help="na conferência, conta também as linhas de cada CSV (lê os arquivos inteiros: demora)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(args.sep) != 1:
        ap.error("--sep deve ter um caractere")
    nomes = list(BLOCOS) if "todos" in args.blocos else [b for b in BLOCOS if b in args.blocos]

    conn = conectar(args)
    cur = conn.cursor()

    # ---- passo 1: conferência de tudo antes de alterar qualquer coisa
    t0 = time.perf_counter()
    planos: dict[str, list[Plano]] = {n: conferir(cur, BLOCOS[n], args.raiz, args.sep, args.contar) for n in nomes}
    ruins = [p for ps in planos.values() for p in ps if p.problemas]
    print(f"\n{'tabela':32s}{'linhas':>12s}{'colunas':>9s}   situação")
    for n in nomes:
        for p in planos[n]:
            situacao = "PULADA (sem CSV)" if p.pular else ("; ".join(p.problemas) if p.problemas else "ok")
            linhas = f"{p.linhas:,}" if p.linhas >= 0 else "-"
            print(f"{p.bloco.banco + '.' + p.tabela:32s}{linhas:>12s}{len(p.colunas_csv):>9}   {situacao}")
    print(f"(conferência em {time.perf_counter() - t0:.0f} s)")
    if ruins:
        raise SystemExit(f"\n{len(ruins)} tabela(s) com problema. Nada foi alterado no banco.")
    if args.conferir:
        print("\nConferência ok. Nada foi alterado no banco (--conferir).")
        return 0

    # ---- passos 2 a 4: esvaziar, carregar, verificar (tabela por tabela)
    for n in nomes:
        bloco = BLOCOS[n]
        if not bloco.upsert:
            esvaziar(conn, bloco, planos[n])
        for p in planos[n]:
            if p.pular:
                continue
            t1 = time.perf_counter()
            if p.linhas < 0:                    # a contagem é o que permite conferir o LOAD depois
                logger.info("%s.%s: contando linhas de %s (%.1f GB)...", bloco.banco, p.tabela, p.csv.name,
                            p.csv.stat().st_size / 1e9)
                p.linhas = contar_linhas(p.csv)
            logger.info("%s.%s: carregando %s linhas...", bloco.banco, p.tabela, f"{p.linhas:,}")
            if bloco.upsert:
                upsert(conn, p, args.sep)
            else:
                carregar_arquivo(conn, p, f"`{bloco.banco}`.`{p.tabela}`", args.sep)
                conn.commit()
            logger.info("%s.%s: %s linhas carregadas e conferidas (%.0f s)", bloco.banco, p.tabela,
                        f"{p.linhas:,}", time.perf_counter() - t1)

    print(f"\nCarga concluída em {time.perf_counter() - t0:.0f} s. Confira no Workbench, por exemplo:\n"
          "  SELECT table_schema, table_name, table_rows FROM information_schema.tables\n"
          "  WHERE table_schema IN ('dim','b1996','b9706','b0725') ORDER BY 1, 2;")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())