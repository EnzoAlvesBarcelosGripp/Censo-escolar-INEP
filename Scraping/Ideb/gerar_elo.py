"""Tabelas de LIGAÇÃO entre as famílias de indicadores (AFD, ICG, IED, IDEB).

Cada família tem os seus ids de cadastro (id_cadastro) e o seu database. Estas tabelas dizem, para o mesmo
território, qual é o id_cadastro de cada família, de modo que uma consulta cruzada vira 1 JOIN extra.

Saídas (pasta --saida, padrão processados\\ligacao-indicadores):
  elo_territorio.csv  Brasil/Região/UF/Município x localização x dependência, com ID_CAD_<família>
  elo_escola.csv      1 linha por código de escola (CO_ENTIDADE = ID_ESCOLA), com TEM_<fonte> (1/0/vazio)

Regras:
  - vazio no ID_CAD_<família> = a família não tem essa combinação (ou ainda não foi processada, ver aviso)
  - TEM_<fonte> vazio = fonte ainda não processada (nunca vira 0 por falta de arquivo)
  - nada é descartado; qualquer inconsistência levanta erro
Uso:  python gerar_elo.py [--raiz PASTA_PROCESSADOS] [--saida PASTA]
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import Counter
from pathlib import Path

from comum_ideb import UF_CODIGO, csv_atomico

log = logging.getLogger("elo")

RAIZ_PADRAO = Path(r"C:\dados_inep\data_ind\processados")
PASTAS = {
    "AFD": "adequacao-da-formacao-docente",
    "ICG": "complexidade-de-gestao-da-escola",
    "IED": "esforco-docente",
    "IDEB": "ideb-resultados",
}
OPCIONAIS = {"IDEB"}                       # famílias que podem ainda não existir (aviso em vez de erro)
NIVEIS = ["Brasil", "Região", "UF", "Município"]
CATEGORIAS = ["Total", "Urbana", "Rural"]
DEPENDENCIAS = ["Total", "Federal", "Estadual", "Municipal", "Privada", "Pública"]
NIVEL_UF = {"Brasil": "Brasil", "Região": "Região", "UF": "UF"}   # valor da coluna `nivel` do cadastro_uf

CAB_UF = ["id_cadastro", "UNIDGEO", "NO_CATEGORIA", "NO_DEPENDENCIA", "nivel", "co_uf"]
CAB_MUN = ["id_cadastro", "CO_MUNICIPIO", "NO_CATEGORIA", "NO_DEPENDENCIA"]

# fontes de escola: sigla -> (família, arquivo, coluna do código da escola, traz UF/município?)
FONTES_ESCOLA = {
    "AFD": ("AFD", "escola.csv", "CO_ENTIDADE", False),
    "ICG": ("ICG", "escola.csv", "CO_ENTIDADE", False),
    "IED": ("IED", "escola.csv", "CO_ENTIDADE", False),
    "IDEB_AI": ("IDEB", "escola_AI.csv", "ID_ESCOLA", True),
    "IDEB_AF": ("IDEB", "escola_AF.csv", "ID_ESCOLA", True),
    "IDEB_EM": ("IDEB", "escola_EM.csv", "ID_ESCOLA", True),
}

Chave = tuple  # (nivel, regiao, uf, municipio, categoria, dependencia)


def abrir(caminho: Path, cab_minimo: list[str]) -> csv.DictReader:
    f = caminho.open(encoding="utf-8-sig", newline="")
    r = csv.DictReader(f)
    if (r.fieldnames or [])[: len(cab_minimo)] != cab_minimo:
        f.close()
        raise ValueError(f"{caminho}: cabeçalho {r.fieldnames} não começa com {cab_minimo}")
    return r


def inteiro(v: str, campo: str, ctx: str) -> int:
    if not v.strip().isdigit():
        raise ValueError(f"{ctx}: {campo} não é inteiro: {v!r}")
    return int(v)


def validar_cat_dep(cat: str, dep: str, ctx: str) -> None:
    if cat not in CATEGORIAS:
        raise ValueError(f"{ctx}: NO_CATEGORIA inesperada {cat!r}")
    if dep not in DEPENDENCIAS:
        raise ValueError(f"{ctx}: NO_DEPENDENCIA inesperada {dep!r}")


def ler_cadastro_uf(caminho: Path, fam: str) -> list[tuple[Chave, int, str]]:
    out: list[tuple[Chave, int, str]] = []
    vistos_id: set[int] = set()
    vistas: set[Chave] = set()
    for n, r in enumerate(abrir(caminho, CAB_UF), 2):
        ctx = f"{fam}/cadastro_uf.csv linha {n}"
        idc = inteiro(r["id_cadastro"], "id_cadastro", ctx)
        cat, dep, nivel = r["NO_CATEGORIA"], r["NO_DEPENDENCIA"], r["nivel"]
        validar_cat_dep(cat, dep, ctx)
        if nivel not in NIVEL_UF:
            raise ValueError(f"{ctx}: nivel inesperado {nivel!r}")
        co = r["co_uf"].strip()
        if nivel == "Brasil":
            if co:
                raise ValueError(f"{ctx}: Brasil com co_uf={co!r}")
            chave: Chave = ("Brasil", None, None, None, cat, dep)
        elif nivel == "Região":
            reg = inteiro(co, "co_uf(região)", ctx)
            if not 1 <= reg <= 5:
                raise ValueError(f"{ctx}: código de região {reg} fora de 1..5")
            chave = ("Região", reg, None, None, cat, dep)
        else:
            uf = inteiro(co, "co_uf", ctx)
            if uf not in UF_CODIGO.values():
                raise ValueError(f"{ctx}: co_uf desconhecido {uf}")
            chave = ("UF", uf // 10, uf, None, cat, dep)
        if idc in vistos_id or chave in vistas:
            raise ValueError(f"{ctx}: id_cadastro ou combinação repetidos ({idc}, {chave})")
        vistos_id.add(idc)
        vistas.add(chave)
        out.append((chave, idc, r["UNIDGEO"].strip()))
    return out


def ler_cadastro_municipio(caminho: Path, fam: str) -> list[tuple[Chave, int, str]]:
    out: list[tuple[Chave, int, str]] = []
    vistos_id: set[int] = set()
    vistas: set[Chave] = set()
    for n, r in enumerate(abrir(caminho, CAB_MUN), 2):
        ctx = f"{fam}/cadastro_municipio.csv linha {n}"
        idc = inteiro(r["id_cadastro"], "id_cadastro", ctx)
        mun = inteiro(r["CO_MUNICIPIO"], "CO_MUNICIPIO", ctx)
        uf = mun // 100000
        if len(str(mun)) != 7 or uf not in UF_CODIGO.values():
            raise ValueError(f"{ctx}: CO_MUNICIPIO inválido {mun}")
        cat, dep = r["NO_CATEGORIA"], r["NO_DEPENDENCIA"]
        validar_cat_dep(cat, dep, ctx)
        chave: Chave = ("Município", uf // 10, uf, mun, cat, dep)
        if idc in vistos_id or chave in vistas:
            raise ValueError(f"{ctx}: id_cadastro ou combinação repetidos ({idc}, {chave})")
        vistos_id.add(idc)
        vistas.add(chave)
        out.append((chave, idc, ""))
    return out


def ordenar(chave: Chave) -> tuple:
    nivel, reg, uf, mun, cat, dep = chave
    return (NIVEIS.index(nivel), reg or 0, uf or 0, mun or 0, CATEGORIAS.index(cat), DEPENDENCIAS.index(dep))


def gerar_territorio(raiz: Path, saida: Path) -> list[str]:
    familias = list(PASTAS)
    elo: dict[Chave, dict] = {}
    processadas: list[str] = []
    for fam in familias:
        pasta = raiz / PASTAS[fam]
        arqs = [pasta / "cadastro_uf.csv", pasta / "cadastro_municipio.csv"]
        faltam = [a for a in arqs if not a.exists()]
        if faltam:
            if fam in OPCIONAIS:
                log.warning("%s: %s ainda não existe; ID_CAD_%s ficará vazio (família não processada)", fam,
                            ", ".join(a.name for a in faltam), fam)
                continue
            raise FileNotFoundError(f"{fam}: arquivo(s) ausente(s): {faltam}")
        processadas.append(fam)
        for chave, idc, rotulo in ler_cadastro_uf(arqs[0], fam) + ler_cadastro_municipio(arqs[1], fam):
            e = elo.setdefault(chave, {"rotulo": "", "ids": {}})
            e["ids"][fam] = idc
            if rotulo:
                if e["rotulo"] and e["rotulo"] != rotulo:
                    log.warning("rótulo diferente para %s: %r (%s) x %r", chave, rotulo, fam, e["rotulo"])
                e["rotulo"] = e["rotulo"] or rotulo

    cab = ["id_elo", "NIVEL", "CO_REGIAO", "CO_UF", "CO_MUNICIPIO", "NO_TERRITORIO", "NO_CATEGORIA",
           "NO_DEPENDENCIA"] + [f"ID_CAD_{f}" for f in familias]
    por_nivel: Counter[str] = Counter()
    sem_fam: Counter[str] = Counter()
    with csv_atomico(saida / "elo_territorio.csv", cab) as w:
        for i, chave in enumerate(sorted(elo, key=ordenar), 1):
            nivel, reg, uf, mun, cat, dep = chave
            e = elo[chave]
            w.writerow([i, nivel, "" if reg is None else reg, "" if uf is None else uf,
                        "" if mun is None else mun, e["rotulo"], cat, dep]
                       + [e["ids"].get(f, "") for f in familias])
            por_nivel[nivel] += 1
            for f in processadas:
                if f not in e["ids"]:
                    sem_fam[f] += 1
    print(f"\nelo_territorio.csv: {len(elo)} linhas | por nível: {dict(por_nivel)}")
    print("combinações que a família NÃO tem (das processadas):", dict(sem_fam))
    return processadas


def gerar_escola(raiz: Path, saida: Path) -> None:
    fontes = list(FONTES_ESCOLA)
    tem: dict[int, set[str]] = {}
    geo: dict[int, tuple[str, int]] = {}
    processadas: list[str] = []
    for sigla, (fam, arq, col, traz_geo) in FONTES_ESCOLA.items():
        caminho = raiz / PASTAS[fam] / arq
        if not caminho.exists():
            if fam in OPCIONAIS:
                log.warning("%s: %s ainda não existe; TEM_%s ficará vazio (fonte não processada)", sigla, arq, sigla)
                continue
            raise FileNotFoundError(f"{sigla}: {caminho} ausente")
        processadas.append(sigla)
        with caminho.open(encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            campos = r.fieldnames or []
            falta = [c for c in ([col] + (["SG_UF", "CO_MUNICIPIO"] if traz_geo else [])) if c not in campos]
            if falta:
                raise ValueError(f"{caminho}: colunas ausentes {falta} (cabeçalho: {campos})")
            for n, linha in enumerate(r, 2):
                ctx = f"{sigla}/{arq} linha {n}"
                cod = inteiro(linha[col], col, ctx)
                tem.setdefault(cod, set()).add(sigla)
                if traz_geo:
                    g = (linha["SG_UF"], inteiro(linha["CO_MUNICIPIO"], "CO_MUNICIPIO", ctx))
                    if cod in geo and geo[cod] != g:
                        raise ValueError(f"{ctx}: escola {cod} com UF/município diferentes entre etapas: {geo[cod]} x {g}")
                    geo[cod] = g

    cab = ["CO_ENTIDADE", "SG_UF", "CO_MUNICIPIO"] + [f"TEM_{s}" for s in fontes]
    cont: Counter[str] = Counter()
    sem_geo = 0
    with csv_atomico(saida / "elo_escola.csv", cab) as w:
        for cod in sorted(tem):
            uf, mun = geo.get(cod, ("", ""))
            sem_geo += cod not in geo
            flags = ["" if s not in processadas else int(s in tem[cod]) for s in fontes]
            for s in tem[cod]:
                cont[s] += 1
            w.writerow([cod, uf, mun] + flags)
    print(f"\nelo_escola.csv: {len(tem)} escolas distintas | por fonte: {dict(cont)}")
    print(f"escolas sem UF/município (não aparecem no IDEB processado): {sem_geo}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO)
    ap.add_argument("--saida", type=Path, default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    saida = args.saida or args.raiz / "ligacao-indicadores"
    gerar_territorio(args.raiz, saida)
    gerar_escola(args.raiz, saida)
    return 0


if __name__ == "__main__":
    sys.exit(main())