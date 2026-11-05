"""
Monta nucleo.municipal_infraestrutura dentro do MySQL, a partir das tabelas por escola de cada bloco e do
de-para dim.item_infraestrutura_origem. Só agrega: conta escolas (sim / não / vazio) e soma quantidades.

Como funciona, por bloco (1996, 9706, 0725):
  1. LER o de-para do banco (quais colunas formam cada item neste bloco).
  2. CONFERIR que nenhuma escola tem município, dependência ou situação vazios (são a chave do núcleo;
     o script não inventa valor: se houver, lista e para).
  3. AGREGAR em UMA passada pelas escolas: uma tabela temporária "larga", com uma linha por
     (ano, município, dependência, situação) e 2-3 colunas por item. Ler 4 milhões de escolas uma vez só é
     muito mais barato do que ler uma vez por item (seriam ~160 leituras).
  4. ANOS COLETADOS: um item só ganha linhas nos anos em que alguma escola respondeu (sim/não ou
     quantidade). Ano em que a coluna não existia fica sem linha, em vez de aparecer como "todas vazias".
  5. GRAVAR em formato longo, item a item (INSERT ... SELECT da tabela larga), com COMMIT por item:
     a transação não cresce sem parar e uma interrupção perde só o item em andamento.
  6. VERIFICAR o bloco: para cada item e ano, a soma de n_escolas é igual ao número de escolas do bloco
     naquele ano; e sim + não + vazio = n_escolas. Se falhar, o bloco fica marcado como inválido no log:
     rode de novo (o início de cada execução apaga os anos do bloco, então nada se duplica).

Carga rápida (versão 2, 05/11): durante a gravação a tabela fica SÓ com a chave primária. As FKs e os
índices secundários (cada FK de município/dependência/situação cria o seu índice) são removidos antes e
recriados no fim, de uma vez: construir um índice ordenando 65 milhões de linhas é muito mais rápido do que
mantê-lo linha a linha. Depois de recriar, o script confere ele mesmo que não há valor sem par em dim
(o que a FK faria na inserção). Com todos os blocos, a tabela é esvaziada com TRUNCATE (instantâneo) em vez
de DELETE.

Pré-requisitos: ddl nucleo.sql rodado; gerar_catalogo_infra.py + carregar_mysql.py --blocos catalogo.

Uso:
  python montar_nucleo_infra.py                       # 1996, 9706 e 0725
  python montar_nucleo_infra.py --blocos 9706
  python montar_nucleo_infra.py --mostrar-sql --blocos 1996   # só imprime o SQL (não executa nada)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from carregar_mysql import conectar  # noqa: E402  (mesma conexão/senha do carregador)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("nucleo_infra")

DESTINO = "nucleo.municipal_infraestrutura"

# O que a tabela tem além da chave primária (igual ao ddl nucleo.sql). Removido para a carga e recriado no fim.
INDICES = {"ix_nucleo_infra_municipio": "(ano, cod_municipio)"}
FKS = {  # nome -> (coluna, tabela de dim, coluna de dim)
    "fk_nucleo_infra_item": ("id_item", "dim.item_infraestrutura", "id_item"),
    "fk_nucleo_infra_municipio": ("cod_municipio", "dim.municipio", "cod_municipio"),
    "fk_nucleo_infra_dependencia": ("dependencia", "dim.dependencia", "codigo"),
    "fk_nucleo_infra_situacao": ("situacao", "dim.situacao_funcionamento", "codigo"),
}


@dataclass(frozen=True)
class Base:
    """Onde estão a escola e suas chaves em cada bloco (os nomes mudam entre os blocos)."""
    banco: str
    chave: str                      # identificador da escola (junto com ano)
    municipio: str
    dependencia: str
    situacao: str
    anos: tuple[int, int]


BASES = {
    "1996": Base("b1996", "id_ie", "cod_municipio", "dependencia", "situacao_funcionamento", (1996, 1996)),
    "9706": Base("b9706", "id_ie", "cod_municipio", "dependencia", "situacao_funcionamento", (1997, 2006)),
    "0725": Base("b0725", "co_entidade", "co_municipio", "tp_dependencia", "tp_situacao_funcionamento", (2007, 2025)),
}


@dataclass
class ItemBloco:
    id_item: int
    codigo: str
    tipo: str                                       # existencia | quantidade
    colunas: list[tuple[str, str, str, str]]        # (tabela, coluna, valor_sim, valor_nao)


def ler_itens(cur, bloco: str) -> list[ItemBloco]:
    cur.execute("SELECT i.id_item, i.codigo, i.tipo_valor, o.tabela, o.coluna, o.valor_sim, o.valor_nao "
                "FROM dim.item_infraestrutura_origem o JOIN dim.item_infraestrutura i USING (id_item) "
                "WHERE o.bloco = %s ORDER BY i.id_item, o.tabela, o.coluna", (bloco,))
    itens: dict[int, ItemBloco] = {}
    for id_item, codigo, tipo, tabela, coluna, sim, nao in cur.fetchall():
        itens.setdefault(id_item, ItemBloco(id_item, codigo, tipo, [])).colunas.append((tabela, coluna, sim, nao))
    return list(itens.values())


def literal(v: str) -> str:
    return v if v.isdigit() else "'" + v.replace("'", "''") + "'"


def sql_largo(base: Base, itens: list[ItemBloco]) -> str:
    """SELECT ... GROUP BY que produz a tabela larga. Cada tabela do bloco usada no de-para entra uma vez,
    com LEFT JOIN (escola sem linha na tabela de infraestrutura = vazio, não some da contagem)."""
    tabelas = sorted({t for it in itens for t, *_ in it.colunas if t != "escola"})
    alias = {"escola": "e", **{t: f"t{n}" for n, t in enumerate(tabelas, 1)}}
    joins = "".join(f"\n  LEFT JOIN {base.banco}.{t} {alias[t]} ON {alias[t]}.ano = e.ano "
                    f"AND {alias[t]}.{base.chave} = e.{base.chave}" for t in tabelas)
    exprs = []
    for it in itens:
        cols = [(f"{alias[t]}.{c}", s, n) for t, c, s, n in it.colunas]
        if it.tipo == "existencia":
            # OU das colunas: "sim" se alguma diz sim; "não" se nenhuma diz sim e alguma diz não; senão vazio.
            sim = " OR ".join(f"COALESCE({c} = {literal(s)}, 0)" for c, s, _ in cols)
            nao = " OR ".join(f"COALESCE({c} = {literal(n)}, 0)" for c, _, n in cols)
            exprs.append(f"SUM({sim}) AS s_{it.id_item}")
            exprs.append(f"SUM(NOT ({sim}) AND ({nao})) AS n_{it.id_item}")
        else:
            # SOMA das colunas; escola vazia = nenhuma coluna informada.
            soma = " + ".join(f"COALESCE({c}, 0)" for c, *_ in cols)
            vazia = " AND ".join(f"{c} IS NULL" for c, *_ in cols)
            exprs.append(f"SUM({soma}) AS q_{it.id_item}")
            exprs.append(f"SUM({vazia}) AS v_{it.id_item}")
    medidas = ",\n  ".join(exprs)
    return (f"CREATE TEMPORARY TABLE nucleo.tmp_infra_larga AS\n"
            f"SELECT e.ano, e.{base.municipio} AS cod_municipio, e.{base.dependencia} AS dependencia,\n"
            f"  e.{base.situacao} AS situacao, COUNT(*) AS n_escolas,\n  {medidas}\n"
            f"FROM {base.banco}.escola e{joins}\n"
            f"GROUP BY e.ano, e.{base.municipio}, e.{base.dependencia}, e.{base.situacao}")


def sql_coletados(itens: list[ItemBloco]) -> str:
    """Por ano, quantas respostas cada item teve no país inteiro (0 = não coletado naquele ano)."""
    partes = [f"SUM(s_{i.id_item} + n_{i.id_item})" if i.tipo == "existencia"
              else f"SUM(n_escolas - v_{i.id_item})" for i in itens]
    return f"SELECT ano, {', '.join(partes)} FROM nucleo.tmp_infra_larga GROUP BY ano ORDER BY ano"


def sql_inserir(it: ItemBloco, anos: list[int]) -> str:
    i = it.id_item
    if it.tipo == "existencia":
        medidas = f"s_{i}, n_{i}, n_escolas - s_{i} - n_{i}, NULL"
    else:
        medidas = f"NULL, NULL, v_{i}, IF(n_escolas = v_{i}, NULL, q_{i})"
    return (f"INSERT INTO {DESTINO} (id_item, ano, cod_municipio, dependencia, situacao, n_escolas, "
            f"escolas_sim, escolas_nao, escolas_vazio, quantidade)\n"
            f"SELECT {i}, ano, cod_municipio, dependencia, situacao, n_escolas, {medidas}\n"
            f"FROM nucleo.tmp_infra_larga WHERE ano IN ({', '.join(map(str, anos))})")


def conferir_chaves(cur, base: Base) -> None:
    cur.execute(f"SELECT ano, SUM({base.municipio} IS NULL), SUM({base.dependencia} IS NULL), "
                f"SUM({base.situacao} IS NULL) FROM {base.banco}.escola "
                f"WHERE {base.municipio} IS NULL OR {base.dependencia} IS NULL OR {base.situacao} IS NULL "
                f"GROUP BY ano")
    ruins = cur.fetchall()
    if ruins:
        linhas = "\n".join(f"  {a}: município {m}, dependência {d}, situação {s}" for a, m, d, s in ruins)
        raise SystemExit(f"{base.banco}.escola tem escolas sem chave do núcleo (vazias):\n{linhas}\n"
                         "Nada foi gravado. Decida o que fazer com elas antes de montar o núcleo.")


def verificar(cur, base: Base, anos_item: dict[int, list[int]]) -> None:
    a0, a1 = base.anos
    cur.execute(f"SELECT COUNT(*) FROM (SELECT n.ano, n.id_item, SUM(n.n_escolas) AS soma FROM {DESTINO} n "
                f"WHERE n.ano BETWEEN %s AND %s GROUP BY n.ano, n.id_item) x "
                f"JOIN (SELECT ano, COUNT(*) AS total FROM {base.banco}.escola GROUP BY ano) e USING (ano) "
                f"WHERE x.soma <> e.total", (a0, a1))
    (dif,) = cur.fetchone()
    cur.execute(f"SELECT COUNT(*) FROM {DESTINO} WHERE ano BETWEEN %s AND %s AND escolas_sim IS NOT NULL "
                f"AND escolas_sim + escolas_nao + escolas_vazio <> n_escolas", (a0, a1))
    (soma_ruim,) = cur.fetchone()
    cur.execute(f"SELECT COUNT(*) FROM {DESTINO} WHERE ano BETWEEN %s AND %s", (a0, a1))
    (linhas,) = cur.fetchone()
    esperado_pares = sum(len(v) for v in anos_item.values())
    cur.execute(f"SELECT COUNT(DISTINCT id_item, ano) FROM {DESTINO} WHERE ano BETWEEN %s AND %s", (a0, a1))
    (pares,) = cur.fetchone()
    problemas = []
    if dif:
        problemas.append(f"{dif} par(es) (ano, item) cuja soma de n_escolas difere do total de escolas do ano")
    if soma_ruim:
        problemas.append(f"{soma_ruim} linha(s) com sim + não + vazio <> n_escolas")
    if pares != esperado_pares:
        problemas.append(f"{pares} pares (ano, item) gravados, esperados {esperado_pares}")
    if problemas:
        raise SystemExit("Verificação falhou:\n  - " + "\n  - ".join(problemas))
    logger.info("%s: verificação ok (%s linhas, %s pares ano x item)", base.banco, f"{linhas:,}", pares)


def preparar_carga(conn) -> None:
    """Deixa a tabela só com a PK: remove as FKs e depois todos os índices que não são a PK."""
    cur = conn.cursor()
    cur.execute("SELECT constraint_name FROM information_schema.table_constraints WHERE table_schema = 'nucleo' "
                "AND table_name = 'municipal_infraestrutura' AND constraint_type = 'FOREIGN KEY'")
    fks = [r[0] for r in cur.fetchall()]
    for fk in fks:
        cur.execute(f"ALTER TABLE {DESTINO} DROP FOREIGN KEY `{fk}`")
    cur.execute("SELECT DISTINCT index_name FROM information_schema.statistics WHERE table_schema = 'nucleo' "
                "AND table_name = 'municipal_infraestrutura' AND index_name <> 'PRIMARY'")
    indices = [r[0] for r in cur.fetchall()]
    for ix in indices:
        cur.execute(f"ALTER TABLE {DESTINO} DROP INDEX `{ix}`")
    logger.info("tabela pronta para carga rápida: %d FK(s) e %d índice(s) removidos (ficou só a PK)",
                len(fks), len(indices))


def finalizar_carga(conn) -> None:
    """Recria índices e FKs de uma vez e confere, por conta própria, que todo valor tem par em dim."""
    cur = conn.cursor()
    t0 = time.perf_counter()
    partes = [f"ADD INDEX `{n}` {cols}" for n, cols in INDICES.items()]
    partes += [f"ADD CONSTRAINT `{n}` FOREIGN KEY ({c}) REFERENCES {t} ({tc})" for n, (c, t, tc) in FKS.items()]
    logger.info("recriando %d índice(s) e %d FK(s) (constrói cada índice ordenando, de uma vez)...",
                len(INDICES), len(FKS))
    # Com FOREIGN_KEY_CHECKS=0 o MySQL cria as FKs no lugar (INPLACE), sem copiar a tabela inteira;
    # a conferência que ele deixaria de fazer é feita logo abaixo.
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    try:
        cur.execute(f"ALTER TABLE {DESTINO} " + ", ".join(partes))
    finally:
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    logger.info("índices e FKs recriados em %.0f s; conferindo valores sem par em dim...", time.perf_counter() - t0)
    orfaos = []
    for n, (c, t, tc) in FKS.items():
        # DISTINCT usa o índice da própria FK: lê poucos milhares de valores, não 65 milhões de linhas
        cur.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT {c} AS v FROM {DESTINO}) x "
                    f"LEFT JOIN {t} d ON d.{tc} = x.v WHERE d.{tc} IS NULL")
        (q,) = cur.fetchone()
        if q:
            orfaos.append(f"{c}: {q} valor(es) sem par em {t}")
    if orfaos:
        raise SystemExit("Valores sem par nas dimensões (a FK foi criada, mas os dados não fecham):\n  - "
                         + "\n  - ".join(orfaos))
    logger.info("conferência das FKs ok (%.0f s no total)", time.perf_counter() - t0)


def montar_bloco(conn, bloco: str, mostrar_sql: bool, esvaziada: bool = False) -> None:
    base = BASES[bloco]
    cur = conn.cursor()
    itens = ler_itens(cur, bloco)
    if not itens:
        raise SystemExit(f"Nenhum item no de-para para o bloco {bloco}: carregue o catálogo "
                         "(gerar_catalogo_infra.py + carregar_mysql.py --blocos catalogo).")
    if mostrar_sql:
        print(f"-- bloco {bloco}: {len(itens)} itens\n{sql_largo(base, itens)};\n\n{sql_coletados(itens)};\n\n"
              f"-- exemplo de gravação (um INSERT por item):\n{sql_inserir(itens[0], [base.anos[0]])};\n")
        return

    t0 = time.perf_counter()
    conferir_chaves(cur, base)
    cur.execute("DROP TEMPORARY TABLE IF EXISTS nucleo.tmp_infra_larga")
    logger.info("%s: agregando %d itens numa passada pelas escolas...", base.banco, len(itens))
    cur.execute(sql_largo(base, itens))
    cur.execute("SELECT COUNT(*) FROM nucleo.tmp_infra_larga")
    (grupos,) = cur.fetchone()
    logger.info("%s: %s grupos (ano x município x dependência x situação) em %.0f s",
                base.banco, f"{grupos:,}", time.perf_counter() - t0)

    cur.execute(sql_coletados(itens))
    anos_item: dict[int, list[int]] = {it.id_item: [] for it in itens}
    for ano, *respostas in cur.fetchall():
        for it, r in zip(itens, respostas):
            if r:                      # None ou 0 = ninguém respondeu no país: não coletado nesse ano
                anos_item[it.id_item].append(int(ano))
    sem_ano = [it.codigo for it in itens if not anos_item[it.id_item]]
    if sem_ano:
        logger.warning("%s: %d item(ns) sem nenhum ano coletado (não terão linhas): %s",
                       base.banco, len(sem_ano), ", ".join(sem_ano))
    previstas = sum(len(v) for v in anos_item.values()) * grupos // max(1, len({a for v in anos_item.values() for a in v}))
    logger.info("%s: gravando ~%s linhas...", base.banco, f"{previstas:,}")

    if not esvaziada:
        logger.info("%s: apagando os anos %s-%s que já existiam...", base.banco, *base.anos)
        cur.execute(f"DELETE FROM {DESTINO} WHERE ano BETWEEN %s AND %s", base.anos)
        conn.commit()
    n = 0
    try:
        for n, it in enumerate(itens, 1):
            if anos_item[it.id_item]:
                cur.execute(sql_inserir(it, anos_item[it.id_item]))
                conn.commit()                    # COMMIT por item: a transação nunca passa de ~1 item
            if n % 20 == 0:
                logger.info("%s: %d/%d itens gravados (%.0f s)", base.banco, n, len(itens), time.perf_counter() - t0)
    except BaseException:
        conn.rollback()
        logger.error("%s: interrompido no item %d de %d. Os itens anteriores ficaram gravados; rode de novo "
                     "(os anos do bloco são apagados no início, nada se duplica).", base.banco, n, len(itens))
        raise
    try:
        verificar(cur, base, {k: v for k, v in anos_item.items() if v})
    except SystemExit as e:
        raise SystemExit(f"{e}\nOs dados do bloco {base.banco} ficaram gravados, mas INCONSISTENTES: "
                         "rode o script de novo para este bloco.") from None
    cur.execute("DROP TEMPORARY TABLE IF EXISTS nucleo.tmp_infra_larga")
    logger.info("%s: concluído em %.0f s", base.banco, time.perf_counter() - t0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blocos", nargs="+", choices=[*BASES, "todos"], default=["todos"])
    ap.add_argument("--mostrar-sql", action="store_true", help="só imprime o SQL gerado; não executa nada")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--porta", type=int, default=3306)
    ap.add_argument("--usuario", default="root")
    ap.add_argument("--socket", default="", help="socket unix (só para testes em Linux)")
    args = ap.parse_args()
    blocos = list(BASES) if "todos" in args.blocos else [b for b in BASES if b in args.blocos]
    if args.mostrar_sql:
        conn = conectar(args)
        try:
            for b in blocos:
                montar_bloco(conn, b, True)
        finally:
            conn.close()
        return 0
    conn = conectar(args)
    t0 = time.perf_counter()
    try:
        # Sem log binário nesta sessão: as ~65 milhões de linhas não são gravadas de novo no binlog (~2,5 GB de
        # disco e tempo de escrita). Este banco é local, sem replicação; o núcleo pode ser remontado a qualquer
        # momento a partir dos blocos, então não há o que recuperar pelo binlog.
        try:
            conn.cursor().execute("SET SESSION sql_log_bin = 0")
        except Exception as e:  # sem privilégio: segue com o binlog ligado
            logger.warning("não consegui desligar o binlog desta sessão (%s); a carga segue normalmente", e)
        preparar_carga(conn)
        todos = blocos == list(BASES)
        if todos:
            conn.cursor().execute(f"TRUNCATE TABLE {DESTINO}")   # mais rápido que DELETE de 65 milhões de linhas
            logger.info("todos os blocos: tabela esvaziada com TRUNCATE")
        for b in blocos:
            montar_bloco(conn, b, False, esvaziada=todos)
        finalizar_carga(conn)
    finally:
        conn.close()
    logger.info("núcleo de infraestrutura concluído em %.0f s", time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
