"""Adequação da formação docente (Escolas): xlsx -> 2 CSVs (escola, percentual_docentes_escola).
Reaproveita ETAPAS, GRUPOS e numero() do gerar_afd_uf. A conferência de CO_ENTIDADE contra b0725.escola fica para a carga."""
from __future__ import annotations

import csv
import logging
import re
import shutil
import zipfile
from contextlib import ExitStack
from pathlib import Path

import openpyxl

from gerar_afd_uf import ETAPAS, GRUPOS, numero

RAIZ = Path(r"C:\dados_inep\data_ind\adequacao-da-formacao-docente")
SAIDA = Path(r"C:\dados_inep\data_ind\processados\adequacao-da-formacao-docente")
ABA = "Ind. adeq. form. doc."
ANCORA = "NU_ANO_CENSO"
CHAVES = ("NU_ANO_CENSO", "NO_REGIAO", "SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO",
          "CO_ENTIDADE", "NO_ENTIDADE", "NO_CATEGORIA", "NO_DEPENDENCIA")
CABECALHO_ESPERADO = CHAVES + tuple(f"{p}_CAT_{n}" for p in ETAPAS for n in range(1, GRUPOS + 1))
N_COLS = len(CABECALHO_ESPERADO)   # 44
INI_VALORES = len(CHAVES)          # 9

log = logging.getLogger("afd_escola")


def find_zips(raiz: Path) -> list[Path]:
    return sorted(raiz.rglob("AFD_*_ESCOLAS.zip"))


def extrair_xlsx(zip_path: Path) -> Path:
    """Extrai o único .xlsx do zip (o nome varia: _v1.1 em 2023). Idempotente, com .parcial."""
    with zipfile.ZipFile(zip_path) as zf:
        membros = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".xlsx")]
        if len(membros) != 1:
            raise ValueError(f"{zip_path.name}: esperado 1 xlsx, achei {len(membros)}")
        info = membros[0]
        destino = zip_path.parent / "escola_extraido" / Path(info.filename).name
        if destino.exists():
            return destino
        destino.parent.mkdir(exist_ok=True)
        parcial = destino.with_name(destino.name + ".parcial")
        with zf.open(info) as o, open(parcial, "wb") as s:
            shutil.copyfileobj(o, s)
        parcial.replace(destino)
    return destino


def co_entidade(v: object, ctx: str) -> int:
    try:
        if isinstance(v, str):
            v = v.strip()
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"CO_ENTIDADE inválido {v!r} em {ctx}") from None
    if str(n) != str(v).strip() and not (isinstance(v, float) and v == n):
        raise ValueError(f"CO_ENTIDADE não inteiro {v!r} em {ctx}")
    return n


def texto(v: object, campo: str, ctx: str) -> str:
    t = "" if v is None else str(v).strip()
    if not t:
        raise ValueError(f"{campo} vazio em {ctx}")
    return t


def processar_ano(xlsx: Path, ano: int, w_esc: csv.writer, w_fato: csv.writer, est: dict) -> int:
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    try:
        if ABA not in wb.sheetnames:
            raise ValueError(f"{xlsx.name}: aba {ABA!r} não encontrada ({wb.sheetnames})")
        linhas = wb[ABA].iter_rows(values_only=True)

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
            dif = set(cab) ^ set(CABECALHO_ESPERADO)
            raise ValueError(f"{xlsx.name}: cabeçalho diferente. Diferenças: {sorted(map(str, dif))}")

        vistos: set[int] = set()
        n = 0
        fim = False
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
            co = co_entidade(row[5], ctx0)
            if co in vistos:
                raise ValueError(f"{ano}: CO_ENTIDADE duplicado {co}")
            vistos.add(co)
            cat = texto(row[7], "NO_CATEGORIA", f"{ano} {co}")
            dep = texto(row[8], "NO_DEPENDENCIA", f"{ano} {co}")
            est["categorias"].add(cat)
            est["dependencias"].add(dep)
            w_esc.writerow([ano, co, cat, dep])

            for k, prefixo in enumerate(ETAPAS):
                ini_b = INI_VALORES + k * GRUPOS
                ctx = f"{ano} {co} {prefixo}"
                vals = [numero(v, ctx) for v in row[ini_b: ini_b + GRUPOS]]
                est["nulos"] += sum(v == "" for v in vals)
                if all(v == "" for v in vals):
                    est["etapas_vazias"] += 1
                elif all(vals):
                    s = sum(float(v) for v in vals)
                    if abs(s - 100) > 1.0:
                        est["soma_fora"].append(f"{ctx}: soma={s:.1f}")
                w_fato.writerow([ano, co, prefixo, *vals])
                est["fatos"] += 1
            n += 1
        if n == 0:
            raise ValueError(f"{xlsx.name}: nenhuma linha de dados")
        return n
    finally:
        wb.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    SAIDA.mkdir(parents=True, exist_ok=True)
    destinos = {"escola": SAIDA / "escola.csv", "fato": SAIDA / "percentual_docentes_escola.csv"}
    parciais = {k: v.with_name(v.name + ".parcial") for k, v in destinos.items()}
    est = {"fatos": 0, "nulos": 0, "etapas_vazias": 0, "soma_fora": [],
           "categorias": set(), "dependencias": set()}
    escolas_por_ano: dict[int, int] = {}

    with ExitStack() as stack:
        f_esc = stack.enter_context(open(parciais["escola"], "w", newline="", encoding="utf-8"))
        f_fato = stack.enter_context(open(parciais["fato"], "w", newline="", encoding="utf-8"))
        w_esc, w_fato = csv.writer(f_esc), csv.writer(f_fato)
        w_esc.writerow(["NU_ANO_CENSO", "CO_ENTIDADE", "NO_CATEGORIA", "NO_DEPENDENCIA"])
        w_fato.writerow(["NU_ANO_CENSO", "CO_ENTIDADE", "etapa", *(f"CAT_{n}" for n in range(1, GRUPOS + 1))])

        zips = find_zips(RAIZ)
        if not zips:
            raise SystemExit(f"nenhum zip de escola em {RAIZ}")
        for z in zips:
            ano = int(re.search(r"_(\d{4})_", z.name).group(1))
            xlsx = extrair_xlsx(z)
            log.info("%d: lendo %s", ano, xlsx.name)
            escolas_por_ano[ano] = processar_ano(xlsx, ano, w_esc, w_fato, est)
            log.info("%d: %d escolas", ano, escolas_por_ano[ano])

    # só chega aqui se nenhum ano falhou: publica os dois arquivos
    for k in destinos:
        parciais[k].replace(destinos[k])

    if est["soma_fora"]:
        log.warning("%d distribuições com soma fora de 100±1 (primeiras 5): %s",
                    len(est["soma_fora"]), est["soma_fora"][:5])
    log.info("escolas-ano=%d | fatos=%d | etapas totalmente NULL=%d | células NULL=%d",
             sum(escolas_por_ano.values()), est["fatos"], est["etapas_vazias"], est["nulos"])
    log.info("NO_CATEGORIA=%s | NO_DEPENDENCIA=%s", sorted(est["categorias"]), sorted(est["dependencias"]))


if __name__ == "__main__":
    main()