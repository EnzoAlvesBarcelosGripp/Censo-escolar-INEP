"""Esforço docente (Municípios): xlsx -> 2 CSVs (cadastro_municipio, percentual_docentes_municipio)."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from comum_ind import extrair_xlsx, inteiro, linhas_dados, numero, texto
from gerar_ied_uf import ETAPAS, NIVEIS, RAIZ, SAIDA, ano_do_zip

CHAVES = ("NU_ANO_CENSO", "NO_REGIAO", "SG_UF", "CO_MUNICIPIO", "NO_MUNICIPIO",
          "NO_CATEGORIA", "NO_DEPENDENCIA")
ESPERADO = CHAVES + tuple(f"{p}_CAT_{n}" for p in ETAPAS for n in range(1, NIVEIS + 1))
I_MUN, I_CAT, I_DEP = 3, 5, 6

log = logging.getLogger("ied_municipio")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    zips = sorted(p for p in RAIZ.rglob("*.zip") if "MUNIC" in p.name.upper())
    if not zips:
        raise SystemExit(f"nenhum zip de município em {RAIZ}")
    SAIDA.mkdir(parents=True, exist_ok=True)
    d_cad, d_fato = SAIDA / "cadastro_municipio.csv", SAIDA / "percentual_docentes_municipio.csv"
    p_cad, p_fato = (d.with_name(d.name + ".parcial") for d in (d_cad, d_fato))

    cadastro: dict[tuple[int, str, str], int] = {}
    combos_por_ano: dict[int, set] = {}
    fatos = nulos = etapas_vazias = 0
    soma_fora: list[str] = []
    categorias: set[str] = set()
    dependencias: set[str] = set()

    with open(p_fato, "w", newline="", encoding="utf-8") as f_fato:
        w_fato = csv.writer(f_fato)
        w_fato.writerow(["id_cadastro", "NU_ANO_CENSO", "etapa", *(f"CAT_{n}" for n in range(1, NIVEIS + 1))])
        for z in zips:
            ano = ano_do_zip(z)
            xlsx = extrair_xlsx(z, "municipio_extraido")
            log.info("%d: lendo %s", ano, xlsx.name)
            combos: set = set()
            for row in linhas_dados(xlsx, ano, ESPERADO):
                ctx0 = f"{ano} linha {len(combos) + 1}"
                mun = inteiro(row[I_MUN], "CO_MUNICIPIO", ctx0, 1_000_000, 9_999_999)
                cat = texto(row[I_CAT], "NO_CATEGORIA", f"{ano} {mun}")
                dep = texto(row[I_DEP], "NO_DEPENDENCIA", f"{ano} {mun}")
                chave = (mun, cat, dep)
                if chave in combos:
                    raise ValueError(f"{ano}: chave duplicada {chave}")
                combos.add(chave)
                categorias.add(cat)
                dependencias.add(dep)
                if chave not in cadastro:
                    cadastro[chave] = len(cadastro) + 1

                for k, prefixo in enumerate(ETAPAS):
                    ini = len(CHAVES) + k * NIVEIS
                    ctx = f"{ano} {chave} {prefixo}"
                    vals = [numero(v, ctx) for v in row[ini: ini + NIVEIS]]
                    nulos += sum(v == "" for v in vals)
                    if all(v == "" for v in vals):
                        etapas_vazias += 1
                    elif all(vals):
                        s = sum(float(v) for v in vals)
                        if abs(s - 100) > 1.0:
                            soma_fora.append(f"{ctx}: soma={s:.1f}")
                    w_fato.writerow([cadastro[chave], ano, prefixo, *vals])
                    fatos += 1
            combos_por_ano[ano] = combos
            log.info("%d: %d linhas, %d municípios", ano, len(combos), len({c[0] for c in combos}))

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
    if soma_fora:
        log.warning("%d distribuições com soma fora de 100±1 (primeiras 5): %s", len(soma_fora), soma_fora[:5])
    log.info("cadastro=%d | fatos=%d | etapas totalmente NULL=%d | células NULL=%d",
             len(cadastro), fatos, etapas_vazias, nulos)
    log.info("NO_CATEGORIA=%s | NO_DEPENDENCIA=%s", sorted(categorias), sorted(dependencias))


if __name__ == "__main__":
    main()