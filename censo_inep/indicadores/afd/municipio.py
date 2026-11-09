"""Adequação da formação docente (Municípios): xlsx -> 2 CSVs (cadastro_municipio, percentual_docentes_municipio).
Reaproveita ETAPAS, GRUPOS e numero() do gerar_afd_uf e texto() do gerar_afd_escola."""
from __future__ import annotations

import csv
import logging
import re
import shutil
import zipfile
from pathlib import Path

import openpyxl

from gerar_afd_escola import texto
from gerar_afd_uf import ETAPAS, GRUPOS, numero

RAIZ = Path(r"C:\dados_inep\data_ind\adequacao-da-formacao-docente")
SAIDA = Path(r"C:\dados_inep\data_ind\processados\adequacao-da-formacao-docente")
ANCORA = "NU_ANO_CENSO"
CHAVES = ("NU_ANO_CENSO", "NO_REGIAO", "SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO",
          "NO_CATEGORIA", "NO_DEPENDENCIA")
CABECALHO_ESPERADO = CHAVES + tuple(f"{p}_CAT_{n}" for p in ETAPAS for n in range(1, GRUPOS + 1))
N_COLS = len(CABECALHO_ESPERADO)   # 42
INI_VALORES = len(CHAVES)          # 7
I_MUN, I_CAT, I_DEP = 3, 5, 6

log = logging.getLogger("afd_municipio")


def find_zips(raiz: Path) -> list[Path]:
    return sorted(p for p in raiz.rglob("*.zip") if "MUNIC" in p.name.upper())


def extrair_xlsx(zip_path: Path) -> Path:
    """Extrai o único .xlsx do zip (nome pode variar). Idempotente, com .parcial."""
    with zipfile.ZipFile(zip_path) as zf:
        membros = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".xlsx")]
        if len(membros) != 1:
            raise ValueError(f"{zip_path.name}: esperado 1 xlsx, achei {len(membros)}")
        info = membros[0]
        destino = zip_path.parent / "municipio_extraido" / Path(info.filename).name
        if destino.exists():
            return destino
        destino.parent.mkdir(exist_ok=True)
        parcial = destino.with_name(destino.name + ".parcial")
        with zf.open(info) as o, open(parcial, "wb") as s:
            shutil.copyfileobj(o, s)
        parcial.replace(destino)
    return destino


def co_municipio(v: object, ctx: str) -> int:
    try:
        n = int(v.strip() if isinstance(v, str) else v)
    except (TypeError, ValueError):
        raise ValueError(f"CO_MUNICIPIO inválido {v!r} em {ctx}") from None
    if not 1_000_000 <= n <= 9_999_999:
        raise ValueError(f"CO_MUNICIPIO fora de 7 dígitos ({n}) em {ctx}")
    return n


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
        if cab != CABECALHO_ESPERADO:
            raise ValueError(f"{xlsx.name}: cabeçalho diferente do esperado ({len(cab)} colunas).\n"
                             f"  encontrado: {cab}\n"
                             f"  diferenças: {sorted(map(str, set(cab) ^ set(CABECALHO_ESPERADO)))}")

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
            mun = co_municipio(row[I_MUN], ctx0)
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
            id_cad = cadastro[chave]

            for k, prefixo in enumerate(ETAPAS):
                ini_b = INI_VALORES + k * GRUPOS
                ctx = f"{ano} {chave} {prefixo}"
                vals = [numero(v, ctx) for v in row[ini_b: ini_b + GRUPOS]]
                est["nulos"] += sum(v == "" for v in vals)
                if all(v == "" for v in vals):
                    est["etapas_vazias"] += 1
                elif all(vals):
                    s = sum(float(v) for v in vals)
                    if abs(s - 100) > 1.0:
                        est["soma_fora"].append(f"{ctx}: soma={s:.1f}")
                w_fato.writerow([id_cad, ano, prefixo, *vals])
                est["fatos"] += 1
            n += 1
        if n == 0:
            raise ValueError(f"{xlsx.name}: nenhuma linha de dados")
        return combos
    finally:
        wb.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    SAIDA.mkdir(parents=True, exist_ok=True)
    d_cad = SAIDA / "cadastro_municipio.csv"
    d_fato = SAIDA / "percentual_docentes_municipio.csv"
    p_cad, p_fato = (d.with_name(d.name + ".parcial") for d in (d_cad, d_fato))

    est = {"fatos": 0, "nulos": 0, "etapas_vazias": 0, "soma_fora": [],
           "categorias": set(), "dependencias": set()}
    cadastro: dict[tuple[int, str, str], int] = {}
    combos_por_ano: dict[int, set] = {}

    zips = find_zips(RAIZ)
    if not zips:
        raise SystemExit(f"nenhum zip de município em {RAIZ}")

    with open(p_fato, "w", newline="", encoding="utf-8") as f_fato:
        w_fato = csv.writer(f_fato)
        w_fato.writerow(["id_cadastro", "NU_ANO_CENSO", "etapa", *(f"CAT_{n}" for n in range(1, GRUPOS + 1))])
        for z in zips:
            ano = int(re.search(r"_(\d{4})_", z.name).group(1))
            xlsx = extrair_xlsx(z)
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
        log.warning("%d distribuições com soma fora de 100±1 (primeiras 5): %s",
                    len(est["soma_fora"]), est["soma_fora"][:5])
    log.info("cadastro=%d | fatos=%d | etapas totalmente NULL=%d | células NULL=%d",
             len(cadastro), est["fatos"], est["etapas_vazias"], est["nulos"])
    log.info("NO_CATEGORIA=%s | NO_DEPENDENCIA=%s", sorted(est["categorias"]), sorted(est["dependencias"]))


if __name__ == "__main__":
    main()