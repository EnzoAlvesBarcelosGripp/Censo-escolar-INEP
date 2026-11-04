"""Complexidade de gestão da escola (Escolas): xlsx -> 1 CSV (escola), com o nível de cada escola-ano."""
from __future__ import annotations

import csv
import logging
import re
from collections import Counter
from pathlib import Path

import openpyxl

from comum_ind import NULOS, extrair_xlsx, inteiro, texto

RAIZ = Path(r"C:\dados_inep\data_ind\complexidade-de-gestao-da-escola")
SAIDA = Path(r"C:\dados_inep\data_ind\processados\complexidade-de-gestao-da-escola")
ANCORA = "NU_ANO_CENSO"
CHAVES = ("NU_ANO_CENSO", "NO_REGIAO", "SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO",
          "CO_ENTIDADE", "NO_ENTIDADE", "NO_CATEGORIA", "NO_DEPENDENCIA")
N_COLS = len(CHAVES) + 1   # 9 chaves + o nível
I_MUN, I_ESC, I_CAT, I_DEP, I_NIVEL = 3, 5, 7, 8, 9

log = logging.getLogger("icg_escola")


def find_zips(raiz: Path) -> list[Path]:
    return sorted(p for p in raiz.rglob("*.zip") if "ESCOLAS" in p.name.upper())


def ano_do_zip(z: Path) -> int:
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", z.name)
    if not m:
        raise ValueError(f"não achei o ano em {z.name}")
    return int(m.group(1))


def nivel(v: object, ctx: str) -> str:
    """'Nível 3' (ou o número 3) -> '3'. '' = NULL. Qualquer outra coisa para a execução."""
    if v is None:
        return ""
    if isinstance(v, str):
        s = v.strip()
        if s in NULOS:
            return ""
        m = re.fullmatch(r"N[ií]vel\s+([1-6])", s, re.IGNORECASE)
        if not m:
            raise ValueError(f"nível inesperado {v!r} em {ctx}")
        return m.group(1)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == int(v) and 1 <= v <= 6:
        return str(int(v))
    raise ValueError(f"nível inesperado {v!r} em {ctx}")


def processar_ano(xlsx: Path, ano: int, w: csv.writer, est: dict) -> tuple[int, tuple]:
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    try:
        if len(wb.sheetnames) != 1:
            raise ValueError(f"{xlsx.name}: esperada 1 aba, achei {wb.sheetnames}")
        linhas = wb[wb.sheetnames[0]].iter_rows(values_only=True)
        cab = None
        for row in linhas:
            if row and row[0] == ANCORA:
                cab = tuple(row)
                break
        if cab is None:
            raise ValueError(f"{xlsx.name}: âncora {ANCORA} não encontrada")
        while cab and cab[-1] is None:
            cab = cab[:-1]
        if len(cab) != N_COLS or cab[: len(CHAVES)] != CHAVES:
            raise ValueError(f"{xlsx.name}: cabeçalho diferente do esperado ({len(cab)} colunas).\n  encontrado: {cab}")

        vistos: set[int] = set()
        n, fim = 0, False
        for row in linhas:
            ini = row[0] if row else None
            eh_ano = isinstance(ini, (int, float)) and not isinstance(ini, bool)
            if not eh_ano:
                if n:
                    fim = True          # rodapé começou
                continue
            if fim:
                raise ValueError(f"{xlsx.name}: linha de dados depois do rodapé")
            if int(ini) != ano:
                raise ValueError(f"{xlsx.name}: linha com ano {ini}, esperado {ano}")
            row = tuple(row) + (None,) * (N_COLS - len(row))

            ctx0 = f"{ano} linha {n + 1}"
            inteiro(row[I_MUN], "CO_MUNICIPIO", ctx0, 1_000_000, 9_999_999)
            co = inteiro(row[I_ESC], "CO_ENTIDADE", ctx0, 10_000_000, 99_999_999)
            if co in vistos:
                raise ValueError(f"{ano}: CO_ENTIDADE duplicado {co}")
            vistos.add(co)
            cat = texto(row[I_CAT], "NO_CATEGORIA", f"{ano} {co}")
            dep = texto(row[I_DEP], "NO_DEPENDENCIA", f"{ano} {co}")
            niv = nivel(row[I_NIVEL], f"{ano} {co}")
            est["categorias"].add(cat)
            est["dependencias"].add(dep)
            est["niveis"][(ano, niv or "NULL")] += 1
            w.writerow(["NU_ANO_CENSO", "CO_ENTIDADE", "NO_CATEGORIA", "NO_DEPENDENCIA", "NIVEL"])
            n += 1
        if n == 0:
            raise ValueError(f"{xlsx.name}: nenhuma linha de dados")
        return n, cab
    finally:
        wb.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    zips = find_zips(RAIZ)
    if not zips:
        raise SystemExit(f"nenhum zip de escola em {RAIZ}")
    SAIDA.mkdir(parents=True, exist_ok=True)
    destino = SAIDA / "escola.csv"
    parcial = destino.with_name(destino.name + ".parcial")

    est = {"categorias": set(), "dependencias": set(), "niveis": Counter()}
    por_ano: dict[int, int] = {}
    cab_ref: tuple | None = None

    with open(parcial, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for z in zips:
            ano = ano_do_zip(z)
            xlsx = extrair_xlsx(z, "escola_extraido")
            log.info("%d: lendo %s", ano, xlsx.name)
            n, cab = processar_ano(xlsx, ano, w, est)
            if cab_ref is None:
                cab_ref = cab
            elif cab != cab_ref:
                raise ValueError(f"{xlsx.name}: cabeçalho difere do primeiro ano.\n  {cab}\n  {cab_ref}")
            por_ano[ano] = n
            log.info("%d: %d escolas", ano, n)

    # só chega aqui se nenhum ano falhou
    parcial.replace(destino)
    log.info("escolas-ano=%d | coluna do nível (nome original): %s", sum(por_ano.values()), cab_ref[I_NIVEL])
    log.info("NO_CATEGORIA=%s | NO_DEPENDENCIA=%s", sorted(est["categorias"]), sorted(est["dependencias"]))
    for ano in sorted(por_ano):
        dist = {k[1]: v for k, v in sorted(est["niveis"].items()) if k[0] == ano}
        log.info("%d níveis: %s", ano, dist)


if __name__ == "__main__":
    main()