"""Complexidade de gestão da escola (Municípios): xlsx -> 2 CSVs (cadastro_municipio, percentual_escolas_municipio)."""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

import openpyxl

from comum_ind import extrair_xlsx, inteiro, numero, texto

RAIZ = Path(r"C:\dados_inep\data_ind\complexidade-de-gestao-da-escola")
SAIDA = Path(r"C:\dados_inep\data_ind\processados\complexidade-de-gestao-da-escola")
ANCORA = "NU_ANO_CENSO"
CHAVES = ("NU_ANO_CENSO", "NO_REGIAO", "SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO",
          "NO_CATEGORIA", "NO_DEPENDENCIA")
N_NIVEIS = 6
N_COLS = len(CHAVES) + N_NIVEIS   # 13
I_MUN, I_CAT, I_DEP = 3, 5, 6

log = logging.getLogger("icg_municipio")


def find_zips(raiz: Path) -> list[Path]:
    return sorted(p for p in raiz.rglob("*.zip") if "MUNIC" in p.name.upper())


def ano_do_zip(z: Path) -> int:
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", z.name)
    if not m:
        raise ValueError(f"não achei o ano em {z.name}")
    return int(m.group(1))


def processar_ano(xlsx: Path, ano: int, cadastro: dict, w_fato: csv.writer, est: dict) -> set:
    """Devolve o conjunto de combinações (município, categoria, dependência) do ano."""
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
        # o cabeçalho do CSV sai do primeiro arquivo; os demais precisam ser idênticos
        if est["cab"] is None:
            est["cab"] = cab
            w_fato.writerow(["id_cadastro", "NU_ANO_CENSO", *cab[len(CHAVES):]])
        elif cab != est["cab"]:
            raise ValueError(f"{xlsx.name}: cabeçalho difere do primeiro ano.\n  {cab}\n  {est['cab']}")

        combos: set = set()
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
            mun = inteiro(row[I_MUN], "CO_MUNICIPIO", ctx0, 1_000_000, 9_999_999)
            cat = texto(row[I_CAT], "NO_CATEGORIA", f"{ano} {mun}")
            dep = texto(row[I_DEP], "NO_DEPENDENCIA", f"{ano} {mun}")
            chave = (mun, cat, dep)
            if chave in combos:
                raise ValueError(f"{ano}: chave duplicada {chave}")
            combos.add(chave)
            est["categorias"].add(cat)
            est["dependencias"].add(dep)
            if chave not in cadastro:
                cadastro[chave] = len(cadastro) + 1

            ctx = f"{ano} {chave}"
            vals = [numero(v, ctx) for v in row[len(CHAVES): N_COLS]]
            est["nulos"] += sum(v == "" for v in vals)
            if all(v == "" for v in vals):
                est["linhas_vazias"] += 1
            elif all(vals):
                s = sum(float(v) for v in vals)
                if abs(s - 100) > 1.0:
                    est["soma_fora"].append(f"{ctx}: soma={s:.1f}")
            w_fato.writerow([cadastro[chave], ano, *vals])
            est["fatos"] += 1
            n += 1
        if n == 0:
            raise ValueError(f"{xlsx.name}: nenhuma linha de dados")
        return combos
    finally:
        wb.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    zips = find_zips(RAIZ)
    if not zips:
        raise SystemExit(f"nenhum zip de município em {RAIZ}")
    SAIDA.mkdir(parents=True, exist_ok=True)
    d_cad = SAIDA / "cadastro_municipio.csv"
    d_fato = SAIDA / "percentual_escolas_municipio.csv"
    p_cad, p_fato = (d.with_name(d.name + ".parcial") for d in (d_cad, d_fato))

    est = {"cab": None, "fatos": 0, "nulos": 0, "linhas_vazias": 0, "soma_fora": [],
           "categorias": set(), "dependencias": set()}
    cadastro: dict[tuple[int, str, str], int] = {}
    combos_por_ano: dict[int, set] = {}

    with open(p_fato, "w", newline="", encoding="utf-8") as f_fato:
        w_fato = csv.writer(f_fato)
        for z in zips:
            ano = ano_do_zip(z)
            xlsx = extrair_xlsx(z, "municipio_extraido")
            log.info("%d: lendo %s", ano, xlsx.name)
            combos_por_ano[ano] = processar_ano(xlsx, ano, cadastro, w_fato, est)
            log.info("%d: %d linhas, %d municípios", ano, len(combos_por_ano[ano]),
                     len({c[0] for c in combos_por_ano[ano]}))

    with open(p_cad, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id_cadastro", "CO_MUNICIPIO", "NO_CATEGORIA", "NO_DEPENDENCIA"])
        for (mun, cat, dep), i in cadastro.items():
            w.writerow([i, mun, cat, dep])

    # só chega aqui se nenhum ano falhou
    p_cad.replace(d_cad)
    p_fato.replace(d_fato)

    ref = min(combos_por_ano)
    for ano, c in combos_por_ano.items():
        if c != combos_por_ano[ref]:
            log.warning("%d difere de %d (+%d / -%d combinações)", ano, ref,
                        len(c - combos_por_ano[ref]), len(combos_por_ano[ref] - c))
    if est["soma_fora"]:
        log.warning("%d linhas com soma fora de 100±1 (primeiras 5): %s",
                    len(est["soma_fora"]), est["soma_fora"][:5])
    log.info("cadastro=%d | fatos=%d | linhas totalmente NULL=%d | células NULL=%d",
             len(cadastro), est["fatos"], est["linhas_vazias"], est["nulos"])
    log.info("NO_CATEGORIA=%s | NO_DEPENDENCIA=%s", sorted(est["categorias"]), sorted(est["dependencias"]))
    log.info("colunas de valor (nomes originais): %s", list(est["cab"][len(CHAVES):]))


if __name__ == "__main__":
    main()