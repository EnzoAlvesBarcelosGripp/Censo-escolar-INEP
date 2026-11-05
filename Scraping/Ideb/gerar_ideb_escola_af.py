"""IDEB resultados | ESCOLA | Anos Finais  (divulgacao_anos_finais_escolas_2025.xlsx)

Layout da aba (confirmado no cabeçalho técnico da linha 10):
    6 chaves + 11 anos x 6 (aprovação 6º-9º + indicador de rendimento) + 11 anos x 3 (notas SAEB)
    + 11 anos x 1 (Ideb observado) + 8 anos x 1 (meta/projeção 2007-2021) = 124 colunas.
Saídas (modelo "meio-termo": longo por ano, vários valores por linha):
    escola_AF.csv       1 linha por escola (cadastro)
    ideb_escola_AF.csv  1 linha por escola x ano (NADA é descartado: escola-ano sem dado também é gravada)
Uso:  python gerar_ideb_escola_af.py [--xlsx CAMINHO] [--saida PASTA]
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

import openpyxl

from comum_ideb import (FAMILIAS, UF_CODIGO, converter, csv_atomico, inteiro, normaliza, texto)

log = logging.getLogger("ideb_escola_af")

XLSX_PADRAO = Path(r"C:\dados_inep\data_ind\ideb\resultados\2005-2025\_extraido\divulgacao_anos_finais_escolas_2025.xlsx")
SAIDA_PADRAO = Path(r"C:\dados_inep\data_ind\processados\ideb-resultados")
ABA = "IDEB_Escolas (Anos_Finais)"
ETAPA = "AF"
LINHAS_ESPERADAS = 48_005          # contagem do explorador (conferência, não regra)
ANOS = list(range(2005, 2026, 2))
ANOS_META = list(range(2007, 2022, 2))
REDES = {"Estadual", "Federal", "Municipal"}

CHAVES = ["SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO", "ID_ESCOLA", "NO_ESCOLA", "REDE"]
ROTULOS_CHAVES = ["Sigla da UF", "Código do Município", "Nome do Município", "Código da Escola",
                  "Nome da Escola", "Rede"]
APROVACAO = [("SI_4", "6º a 9º ano"), ("1", "6º"), ("2", "7º"), ("3", "8º"), ("4", "9º")]
NOTAS = [("MATEMATICA", "Matemática"), ("PORTUGUES", "Língua Portuguesa"), ("MEDIA", "Nota Média Padronizada (N)")]
# colunas de valor na ordem do arquivo: ordem das famílias na saída
FAMILIAS_AF = ["VL_APROVACAO_SI_4", "VL_APROVACAO_1", "VL_APROVACAO_2", "VL_APROVACAO_3", "VL_APROVACAO_4",
               "VL_INDICADOR_REND", "VL_NOTA_MATEMATICA", "VL_NOTA_PORTUGUES", "VL_NOTA_MEDIA",
               "VL_OBSERVADO", "VL_PROJECAO"]


def layout_esperado() -> list[tuple[str, str, int, str, str]]:
    """[(nome técnico, família, ano, prefixo esperado da linha 7, rótulo esperado da linha 8/9)]"""
    cols: list[tuple[str, str, int, str, str]] = []
    for a in ANOS:
        pref = f"Taxa de Aprovação - {a}"
        for suf, rot in APROVACAO:
            cols.append((f"VL_APROVACAO_{a}_{suf}", f"VL_APROVACAO_{suf}", a, pref, rot))
        cols.append((f"VL_INDICADOR_REND_{a}", "VL_INDICADOR_REND", a, pref, "Indicador de Rendimento (P)"))
    for a in ANOS:
        for suf, rot in NOTAS:
            cols.append((f"VL_NOTA_{suf}_{a}", f"VL_NOTA_{suf}", a, f"Nota SAEB - {a}", rot))
    for a in ANOS:
        cols.append((f"VL_OBSERVADO_{a}", "VL_OBSERVADO", a, f"IDEB {a}", ""))   # linha 8 vazia: o rótulo só aparece por mesclagem
    for a in ANOS_META:
        cols.append((f"VL_PROJECAO_{a}", "VL_PROJECAO", a, "Metas do 1º ciclo do Ideb", str(a)))
    return cols


def validar_cabecalho(topo: list[list]) -> list[tuple[int, str, int]]:
    """Confere nomes técnicos (linha 10) E rótulos visíveis (linhas 7 a 9) contra o layout esperado.
    Devolve [(índice da coluna, família, ano)]."""
    esperado = layout_esperado()
    n = len(CHAVES) + len(esperado)
    linhas = [(r + [None] * n)[:n] for r in topo]
    r7, r8, r9, tec = linhas[6], linhas[7], linhas[8], linhas[9]
    if any(c is not None for c in tec[n:]) or len(topo[9]) > n and any(c is not None for c in topo[9][n:]):
        raise ValueError(f"há colunas além das {n} esperadas")
    if [t for t in tec[:6]] != CHAVES:
        raise ValueError(f"chaves técnicas {tec[:6]} != {CHAVES}")
    if [normaliza(c) for c in r7[:6]] != ROTULOS_CHAVES:
        raise ValueError(f"rótulos das chaves {[normaliza(c) for c in r7[:6]]} != {ROTULOS_CHAVES}")
    ultimo = None
    r7f = []
    for c in r7:                                   # linha 7 tem células mescladas: preenche para a direita
        ultimo = c if c is not None else ultimo
        r7f.append(ultimo)
    out: list[tuple[int, str, int]] = []
    for k, (nome, fam, ano, pref, rot) in enumerate(esperado):
        i = len(CHAVES) + k
        if tec[i] != nome:
            raise ValueError(f"coluna {i + 1}: nome técnico {tec[i]!r} != esperado {nome!r}")
        if not normaliza(r7f[i]).startswith(pref):
            raise ValueError(f"coluna {i + 1} ({nome}): rótulo da linha 7 {normaliza(r7f[i])!r} não começa com {pref!r}")
        achado = normaliza(r9[i]) if fam.startswith(("VL_APROVACAO", "VL_INDICADOR", "VL_PROJECAO")) else normaliza(r8[i])
        if achado != rot:
            raise ValueError(f"coluna {i + 1} ({nome}): rótulo {achado!r} != esperado {rot!r}")
        out.append((i, fam, ano))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", type=Path, default=XLSX_PADRAO)
    ap.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    wb = openpyxl.load_workbook(args.xlsx, read_only=True)
    try:
        if wb.sheetnames != [ABA]:
            raise ValueError(f"abas {wb.sheetnames} != [{ABA!r}]")
        ws = wb[ABA]
        topo = [list(r) for r in ws.iter_rows(min_row=1, max_row=10, values_only=True)]
        colunas = validar_cabecalho(topo)
        log.info("cabeçalho validado: %d colunas de valor", len(colunas))
        por_ano: dict[int, list[tuple[int, str]]] = {a: [] for a in ANOS}
        for i, fam, ano in colunas:
            por_ano[ano].append((i, fam))
        ordem = {f: k for k, f in enumerate(FAMILIAS_AF)}
        for a in ANOS:
            por_ano[a].sort(key=lambda t: ordem[t[1]])
        n = len(CHAVES) + len(colunas)

        cab_escola = ["ID_ESCOLA", "SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO", "NO_ESCOLA", "REDE"]
        cab_fato = ["ID_ESCOLA", "ETAPA", "ANO"] + FAMILIAS_AF + ["OBS_NOTA"]
        lidas = gravadas = vazias = 0
        vistas: set[int] = set()
        em_rodape = False
        nd_por_ano: Counter[tuple[int, str]] = Counter()
        grava_por_ano: Counter[int] = Counter()
        redes: Counter[str] = Counter()
        ufs: Counter[str] = Counter()
        ideb_diverge = ideb_comparados = 0

        with csv_atomico(args.saida / f"escola_{ETAPA}.csv", cab_escola) as w_esc, \
                csv_atomico(args.saida / f"ideb_escola_{ETAPA}.csv", cab_fato) as w_fato:
            for k, bruta in enumerate(ws.iter_rows(min_row=11, values_only=True), 11):
                linha = (list(bruta) + [None] * n)[:n]
                ctx = f"linha {k}"
                if em_rodape:
                    if any(isinstance(c, (int, float)) and not isinstance(c, bool) for c in linha[6:]):
                        raise ValueError(f"{ctx}: números depois do início do rodapé")
                    continue
                if all(c is None for c in linha[:6]) or (isinstance(linha[0], str) and linha[0].startswith("Fonte")):
                    em_rodape = True
                    continue
                uf = texto(linha[0], "SG_UF", ctx)
                co_mun = inteiro(linha[1], "CO_MUNICIPIO", ctx, 7)
                no_mun = texto(linha[2], "NO_MUNICIPIO", ctx)
                id_esc = inteiro(linha[3], "ID_ESCOLA", ctx, 8)
                no_esc = texto(linha[4], "NO_ESCOLA", ctx)
                rede = texto(linha[5], "REDE", ctx)
                if uf not in UF_CODIGO:
                    raise ValueError(f"{ctx}: SG_UF desconhecida {uf!r}")
                if co_mun // 100000 != UF_CODIGO[uf]:
                    raise ValueError(f"{ctx}: município {co_mun} não pertence à UF {uf}")
                if rede not in REDES:
                    raise ValueError(f"{ctx}: REDE inesperada {rede!r}")
                if id_esc in vistas:
                    raise ValueError(f"{ctx}: ID_ESCOLA duplicado {id_esc}")
                vistas.add(id_esc)
                lidas += 1
                redes[rede] += 1
                ufs[uf] += 1
                w_esc.writerow([id_esc, uf, co_mun, no_mun, no_esc, rede])

                for a in ANOS:
                    valores: dict[str, str] = {}
                    obs: set[str] = set()
                    for i, fam in por_ano[a]:
                        txt, o = converter(linha[i], fam, f"{ctx}|{topo[9][i]}")
                        valores[fam] = txt
                        if o:
                            obs.add(o)
                    if len(obs) > 1:
                        raise ValueError(f"{ctx}|{a}: observações diferentes entre as notas: {sorted(obs)}")
                    o_nota = next(iter(obs), "")
                    if not o_nota and not any(valores.values()):
                        vazias += 1                    # só conta: a linha é gravada igual
                    if o_nota:
                        nd_por_ano[(a, o_nota)] += 1
                    n_, p_, i_ = valores["VL_NOTA_MEDIA"], valores["VL_INDICADOR_REND"], valores["VL_OBSERVADO"]
                    if n_ and p_ and i_:
                        ideb_comparados += 1
                        if abs(float(n_) * float(p_) - float(i_)) > 0.1:
                            ideb_diverge += 1
                    gravadas += 1
                    grava_por_ano[a] += 1
                    w_fato.writerow([id_esc, ETAPA, a] + [valores.get(f, "") for f in FAMILIAS_AF] + [o_nota])

            if lidas != LINHAS_ESPERADAS:
                log.warning("linhas de dados %d != %d do explorador", lidas, LINHAS_ESPERADAS)
            if gravadas != lidas * len(ANOS):
                raise ValueError("contagem não fecha")

        print(f"\nescolas (escola_{ETAPA}.csv): {lidas}")
        print(f"ideb_escola_{ETAPA}.csv: {gravadas} linhas (= {lidas} escolas x {len(ANOS)} anos) "
              f"| das quais sem nenhum dado: {vazias}")
        print("gravadas por ano:", dict(sorted(grava_por_ano.items())))
        print("REDE:", dict(redes))
        print("UFs:", len(ufs))
        print("OBS_NOTA por ano/código:", dict(sorted(nd_por_ano.items())))
        print(f"conferência Ideb ≈ N x P (tolerância 0,1): {ideb_diverge} divergentes de {ideb_comparados}")
    finally:
        wb.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())