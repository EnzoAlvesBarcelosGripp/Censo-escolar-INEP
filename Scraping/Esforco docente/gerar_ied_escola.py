"""Esforço docente (Escolas): xlsx -> 2 CSVs (escola, percentual_docentes_escola).
A tabela etapa vem do gerar_ied_uf (mesmo database)."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from comum_ind import extrair_xlsx, inteiro, linhas_dados, numero, texto
from gerar_ied_uf import ETAPAS, NIVEIS, RAIZ, SAIDA, ano_do_zip

CHAVES = ("NU_ANO_CENSO", "NO_REGIAO", "SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO",
          "CO_ENTIDADE", "NO_ENTIDADE", "NO_CATEGORIA", "NO_DEPENDENCIA")
ESPERADO = CHAVES + tuple(f"{p}_CAT_{n}" for p in ETAPAS for n in range(1, NIVEIS + 1))
I_MUN, I_ESC, I_CAT, I_DEP = 3, 5, 7, 8

log = logging.getLogger("ied_escola")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    zips = sorted(p for p in RAIZ.rglob("*.zip") if "ESCOLAS" in p.name.upper())
    if not zips:
        raise SystemExit(f"nenhum zip de escola em {RAIZ}")
    SAIDA.mkdir(parents=True, exist_ok=True)
    d_esc, d_fato = SAIDA / "escola.csv", SAIDA / "percentual_docentes_escola.csv"
    p_esc, p_fato = (d.with_name(d.name + ".parcial") for d in (d_esc, d_fato))

    fatos = nulos = etapas_vazias = 0
    soma_fora: list[str] = []
    categorias: set[str] = set()
    dependencias: set[str] = set()
    por_ano: dict[int, int] = {}

    with open(p_esc, "w", newline="", encoding="utf-8") as f_esc, \
            open(p_fato, "w", newline="", encoding="utf-8") as f_fato:
        w_esc, w_fato = csv.writer(f_esc), csv.writer(f_fato)
        w_esc.writerow(["NU_ANO_CENSO", "CO_ENTIDADE", "NO_CATEGORIA", "NO_DEPENDENCIA"])
        w_fato.writerow(["NU_ANO_CENSO", "CO_ENTIDADE", "etapa", *(f"CAT_{n}" for n in range(1, NIVEIS + 1))])

        for z in zips:
            ano = ano_do_zip(z)
            xlsx = extrair_xlsx(z, "escola_extraido")
            log.info("%d: lendo %s", ano, xlsx.name)
            vistos: set[int] = set()
            for row in linhas_dados(xlsx, ano, ESPERADO):
                ctx0 = f"{ano} linha {len(vistos) + 1}"
                inteiro(row[I_MUN], "CO_MUNICIPIO", ctx0, 1_000_000, 9_999_999)
                co = inteiro(row[I_ESC], "CO_ENTIDADE", ctx0, 10_000_000, 99_999_999)
                if co in vistos:
                    raise ValueError(f"{ano}: CO_ENTIDADE duplicado {co}")
                vistos.add(co)
                cat = texto(row[I_CAT], "NO_CATEGORIA", f"{ano} {co}")
                dep = texto(row[I_DEP], "NO_DEPENDENCIA", f"{ano} {co}")
                categorias.add(cat)
                dependencias.add(dep)
                w_esc.writerow([ano, co, cat, dep])

                for k, prefixo in enumerate(ETAPAS):
                    ini = len(CHAVES) + k * NIVEIS
                    ctx = f"{ano} {co} {prefixo}"
                    vals = [numero(v, ctx) for v in row[ini: ini + NIVEIS]]
                    nulos += sum(v == "" for v in vals)
                    if all(v == "" for v in vals):
                        etapas_vazias += 1
                    elif all(vals):
                        s = sum(float(v) for v in vals)
                        if abs(s - 100) > 1.0:
                            soma_fora.append(f"{ctx}: soma={s:.1f}")
                    w_fato.writerow([ano, co, prefixo, *vals])
                    fatos += 1
            por_ano[ano] = len(vistos)
            log.info("%d: %d escolas", ano, len(vistos))

    # só chega aqui se nenhum ano falhou
    p_esc.replace(d_esc)
    p_fato.replace(d_fato)
    if soma_fora:
        log.warning("%d distribuições com soma fora de 100±1 (primeiras 5): %s", len(soma_fora), soma_fora[:5])
    log.info("escolas-ano=%d | fatos=%d | etapas totalmente NULL=%d | células NULL=%d",
             sum(por_ano.values()), fatos, etapas_vazias, nulos)
    log.info("NO_CATEGORIA=%s | NO_DEPENDENCIA=%s", sorted(categorias), sorted(dependencias))


if __name__ == "__main__":
    main()