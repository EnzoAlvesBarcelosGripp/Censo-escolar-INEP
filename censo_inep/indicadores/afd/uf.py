"""Adequação da formação docente (Brasil/Regiões/UF): xlsx -> 3 CSVs (cadastro_uf, etapa, percentual_docentes)."""
from __future__ import annotations

import csv
import logging
import re
import unicodedata
from pathlib import Path

import openpyxl

from unzip import DESTINO, RAIZ, find_zips, unzip  # reaproveita a extração idempotente

SAIDA = Path(r"C:\dados_inep\data_ind\processados\adequacao-da-formacao-docente")
ABA = "BRASIL_REGIOES_UFS"
ANCORA = "NU_ANO_CENSO"
CHAVES = ("NU_ANO_CENSO", "UNIDGEO", "NO_CATEGORIA", "NO_DEPENDENCIA")
GRUPOS = 5

# prefixo do INEP -> rótulo (regra: tipo sem subtipo = nome; subtipo = Tipo_Subtipo)
ETAPAS: dict[str, str] = {
    "ED_INF": "Educação_Infantil",
    "FUN": "Fundamental_Total",
    "FUN_AI": "Fundamental_Anos_Iniciais",
    "FUN_AF": "Fundamental_Anos_Finais",
    "MED": "Médio",
    "EJA_FUN": "EJA_Fundamental",
    "EJA_MED": "EJA_Médio",
}
CABECALHO_ESPERADO = CHAVES + tuple(
    f"{p}_CAT_{n}" for p in ETAPAS for n in range(1, GRUPOS + 1)
)

# códigos IBGE (UF: 2 dígitos; região: 1 dígito)
UFS = {
    "rondonia": 11, "acre": 12, "amazonas": 13, "roraima": 14, "para": 15, "amapa": 16,
    "tocantins": 17, "maranhao": 21, "piaui": 22, "ceara": 23, "rio grande do norte": 24,
    "paraiba": 25, "pernambuco": 26, "alagoas": 27, "sergipe": 28, "bahia": 29,
    "minas gerais": 31, "espirito santo": 32, "rio de janeiro": 33, "sao paulo": 35,
    "parana": 41, "santa catarina": 42, "rio grande do sul": 43,
    "mato grosso do sul": 50, "mato grosso": 51, "goias": 52, "distrito federal": 53,
}
REGIOES = {"norte": 1, "nordeste": 2, "sudeste": 3, "sul": 4, "centro-oeste": 5}
NULOS = {"", "-", "–", "—", "--"}

log = logging.getLogger("afd_uf")


def normalizar(txt: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def classificar(unidade: str) -> tuple[str, int | None]:
    """(nivel, co_uf). Para daqui se o nome não for reconhecido."""
    n = normalizar(unidade)
    if n == "brasil":
        return "Brasil", None
    if n in REGIOES:
        return "Região", REGIOES[n]
    if n in UFS:
        return "UF", UFS[n]
    raise ValueError(f"unidade geográfica não reconhecida: {unidade!r}")


def numero(v: object, ctx: str) -> str:
    """Valor da célula -> texto para o CSV ('' = NULL). Para se algo for estranho."""
    if v is None or (isinstance(v, str) and v.strip() in NULOS):
        return ""
    if isinstance(v, str):
        try:
            v = float(v.strip().replace(",", "."))
        except ValueError:
            raise ValueError(f"valor não numérico {v!r} em {ctx}") from None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"tipo inesperado {type(v).__name__} em {ctx}")
    if not 0 <= v <= 100:
        raise ValueError(f"percentual fora de 0..100 ({v}) em {ctx}")
    if abs(v - round(v, 1)) > 1e-9:
        raise ValueError(f"mais de 1 casa decimal ({v}) em {ctx}; DECIMAL(5,1) perderia dado")
    return f"{v:.1f}"


def ler_xlsx(xlsx: Path, ano: int) -> list[tuple]:
    """Valida o cabeçalho e devolve as linhas de dados (sem rodapé)."""
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    try:
        ws = wb[ABA]
        linhas = ws.iter_rows(values_only=True)
        cab = None
        for row in linhas:
            if row and row[0] == ANCORA:
                cab = tuple(row)
                break
        if cab is None:
            raise ValueError(f"{xlsx.name}: âncora {ANCORA} não encontrada")
        # remove colunas vazias à direita, se houver
        while cab and cab[-1] is None:
            cab = cab[:-1]
        if cab != CABECALHO_ESPERADO:
            dif = set(cab) ^ set(CABECALHO_ESPERADO)
            raise ValueError(f"{xlsx.name}: cabeçalho diferente do esperado. Diferenças: {sorted(map(str, dif))}")

        dados: list[tuple] = []
        fim = False
        for row in linhas:
            ini = row[0] if row else None
            eh_ano = isinstance(ini, (int, float)) and not isinstance(ini, bool)
            if not fim and eh_ano:
                if int(ini) != ano:
                    raise ValueError(f"{xlsx.name}: linha com ano {ini}, esperado {ano}")
                dados.append(tuple(row[: len(cab)]))
            elif dados and not eh_ano:
                fim = True  # rodapé começou
            elif fim and eh_ano:
                raise ValueError(f"{xlsx.name}: linha de dados depois do rodapé")
        return dados
    finally:
        wb.close()


def gravar(destino: Path, cabecalho: list[str], linhas: list[list]) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    with open(parcial, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cabecalho)
        w.writerows(linhas)
    parcial.replace(destino)  # só aqui o CSV "existe"
    log.info("gravado %s (%d linhas)", destino.name, len(linhas))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cadastro: dict[tuple[str, str, str], int] = {}
    cadastro_linhas: list[list] = []
    fatos: list[list] = []
    chaves_fato: set[tuple[int, int, str]] = set()
    combos_por_ano: dict[int, set] = {}
    soma_fora: list[str] = []
    nulos = 0

    for z in find_zips(RAIZ):
        xlsx = unzip(z, DESTINO)
        ano = int(re.search(r"_(\d{4})\.xlsx$", xlsx.name).group(1))
        dados = ler_xlsx(xlsx, ano)
        log.info("%d: %d linhas de dados", ano, len(dados))

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
                cadastro_linhas.append([cadastro[chave], unidade, loc, dep, nivel, "" if co_uf is None else co_uf])
            id_cad = cadastro[chave]

            for k, prefixo in enumerate(ETAPAS):
                bloco = row[4 + k * GRUPOS : 4 + (k + 1) * GRUPOS]
                # confere que a coluna é mesmo desta etapa (rsplit no _CAT_)
                nomes = CABECALHO_ESPERADO[4 + k * GRUPOS : 4 + (k + 1) * GRUPOS]
                assert all(n.rsplit("_CAT_", 1)[0] == prefixo for n in nomes)
                ctx = f"{ano} {chave} {prefixo}"
                vals = [numero(v, ctx) for v in bloco]
                nulos += sum(v == "" for v in vals)
                fk = (id_cad, ano, prefixo)
                if fk in chaves_fato:
                    raise ValueError(f"fato duplicado {fk}")
                chaves_fato.add(fk)
                if all(vals):
                    s = sum(float(v) for v in vals)
                    if abs(s - 100) > 1.0:
                        soma_fora.append(f"{ctx}: soma={s:.1f}")
                fatos.append([id_cad, ano, prefixo, *vals])
        combos_por_ano[ano] = combos

    # conferência entre anos
    ref_ano = min(combos_por_ano)
    for ano, combos in combos_por_ano.items():
        if combos != combos_por_ano[ref_ano]:
            log.warning("%d tem combinações diferentes de %d (+%d / -%d)", ano, ref_ano,
                        len(combos - combos_por_ano[ref_ano]), len(combos_por_ano[ref_ano] - combos))
    if soma_fora:
        log.warning("%d distribuições com soma fora de 100±1 (primeiras 5): %s", len(soma_fora), soma_fora[:5])
    log.info("cadastro=%d combinações | fatos=%d | células NULL=%d", len(cadastro), len(fatos), nulos)

    gravar(SAIDA / "cadastro_uf.csv",
           ["id_cadastro", "UNIDGEO", "NO_CATEGORIA", "NO_DEPENDENCIA", "nivel", "co_uf"], cadastro_linhas)
    gravar(SAIDA / "etapa.csv", ["etapa", "rotulo"], [[p, r] for p, r in ETAPAS.items()])
    gravar(SAIDA / "percentual_docentes.csv",
           ["id_cadastro", "NU_ANO_CENSO", "etapa", *(f"CAT_{n}" for n in range(1, GRUPOS + 1))], fatos)


if __name__ == "__main__":
    main()