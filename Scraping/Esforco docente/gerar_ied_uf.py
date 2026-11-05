"""Esforço docente (Brasil/Regiões/UF): xlsx -> 3 CSVs (cadastro_uf, etapa, percentual_docentes)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import openpyxl

from comum_ind import classificar, extrair_xlsx, gravar_csv, numero

RAIZ = Path(r"C:\dados_inep\data_ind\esforco-docente")
SAIDA = Path(r"C:\dados_inep\data_ind\processados\esforco-docente")
ANCORA = "NU_ANO_CENSO"
CHAVES = ("NU_ANO_CENSO", "UNIDGEO", "NO_CATEGORIA", "NO_DEPENDENCIA")
NIVEIS = 6

# prefixo do INEP -> rótulo (tipo sem subtipo = nome; subtipo = Tipo_Subtipo). A ordem é a das colunas.
ETAPAS: dict[str, str] = {
    "FUN": "Fundamental_Total",
    "FUN_AI": "Fundamental_Anos_Iniciais",
    "FUN_AF": "Fundamental_Anos_Finais",
    "MED": "Médio",
}
CABECALHO_ESPERADO = CHAVES + tuple(f"{p}_CAT_{n}" for p in ETAPAS for n in range(1, NIVEIS + 1))
N_COLS = len(CABECALHO_ESPERADO)   # 28

log = logging.getLogger("ied_uf")


def find_zips(raiz: Path) -> list[Path]:
    return sorted(p for p in raiz.rglob("*.zip") if "BRASIL_REGIOES_UF" in p.name.upper())


def ano_do_zip(z: Path) -> int:
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", z.name)
    if not m:
        raise ValueError(f"não achei o ano em {z.name}")
    return int(m.group(1))


def ler_xlsx(xlsx: Path, ano: int) -> list[tuple]:
    """Valida o cabeçalho e devolve as linhas de dados (sem rodapé)."""
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
            dif = sorted(map(str, set(cab) ^ set(CABECALHO_ESPERADO)))
            raise ValueError(f"{xlsx.name}: cabeçalho diferente do esperado ({len(cab)} colunas).\n"
                             f"  encontrado: {cab}\n  diferenças: {dif}")
        dados: list[tuple] = []
        fim = False
        for row in linhas:
            ini = row[0] if row else None
            eh_ano = isinstance(ini, (int, float)) and not isinstance(ini, bool)
            if not eh_ano:
                if dados:
                    fim = True          # rodapé começou
                continue
            if fim:
                raise ValueError(f"{xlsx.name}: linha de dados depois do rodapé")
            if int(ini) != ano:
                raise ValueError(f"{xlsx.name}: linha com ano {ini}, esperado {ano}")
            dados.append(tuple(row) + (None,) * (N_COLS - len(row)))
        if not dados:
            raise ValueError(f"{xlsx.name}: nenhuma linha de dados")
        return dados
    finally:
        wb.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    zips = find_zips(RAIZ)
    if not zips:
        raise SystemExit(f"nenhum zip Brasil/Regiões/UF em {RAIZ}")

    cadastro: dict[tuple[str, str, str], int] = {}
    cad_linhas: list[list] = []
    fatos: list[list] = []
    combos_por_ano: dict[int, set] = {}
    soma_fora: list[str] = []
    nulos = etapas_vazias = 0

    for z in zips:
        ano = ano_do_zip(z)
        xlsx = extrair_xlsx(z, "uf_extraido")
        dados = ler_xlsx(xlsx, ano)
        log.info("%d: %d linhas (%s)", ano, len(dados), xlsx.name)

        combos: set = set()
        for row in dados:
            unidade, loc, dep = (str(c).strip() for c in row[1:4])
            chave = (unidade, loc, dep)
            if chave in combos:
                raise ValueError(f"{ano}: chave duplicada {chave}")
            combos.add(chave)
            if chave not in cadastro:
                nivel, co_uf = classificar(unidade)
                cadastro[chave] = len(cadastro) + 1
                cad_linhas.append([cadastro[chave], unidade, loc, dep, nivel, "" if co_uf is None else co_uf])
            id_cad = cadastro[chave]

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
                fatos.append([id_cad, ano, prefixo, *vals])
        combos_por_ano[ano] = combos

    ref = min(combos_por_ano)
    for ano, c in combos_por_ano.items():
        if c != combos_por_ano[ref]:
            log.warning("%d difere de %d (+%d / -%d combinações)", ano, ref,
                        len(c - combos_por_ano[ref]), len(combos_por_ano[ref] - c))
    if soma_fora:
        log.warning("%d distribuições com soma fora de 100±1 (primeiras 5): %s", len(soma_fora), soma_fora[:5])

    gravar_csv(SAIDA / "cadastro_uf.csv",
               ["id_cadastro", "UNIDGEO", "NO_CATEGORIA", "NO_DEPENDENCIA", "nivel", "co_uf"], cad_linhas)
    gravar_csv(SAIDA / "etapa.csv", ["etapa", "rotulo"], [[p, r] for p, r in ETAPAS.items()])
    gravar_csv(SAIDA / "percentual_docentes.csv",
               ["id_cadastro", "NU_ANO_CENSO", "etapa", *(f"CAT_{n}" for n in range(1, NIVEIS + 1))], fatos)
    log.info("cadastro=%d | fatos=%d | etapas totalmente NULL=%d | células NULL=%d",
             len(cadastro), len(fatos), etapas_vazias, nulos)


if __name__ == "__main__":
    main()