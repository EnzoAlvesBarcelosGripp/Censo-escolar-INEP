"""
Gera os CSVs das dimensões compartilhadas (DDL 01_dim.sql): estado.csv e municipio.csv.

Os domínios (dependência, situação, localização) e as etapas já são inseridos pelo próprio 01_dim.sql.

Município (chave = código IBGE de 7 dígitos), SCD tipo 1:
  - lista de municípios = todos os CO_MUNICIPIO de 2007-2025 + os CODMUNIC de 1996-2006;
  - CODMUNIC -> IBGE: dígitos 1-2 (UF) + 8-11 (município) = 6 primeiros dígitos do código IBGE
    (regra comprovada por verificar_municipios.py: 100% casado, 0 conflitos); o 7º dígito vem da lista de 2007+;
  - nome = o do ano mais recente em que o município aparece;
  - UF = a mais frequente entre todos os anos (protege contra um erro isolado de um ano; divergências vão para o relatório);
  - ano_inicio = primeiro ano em que aparece; meso/microrregião e regiões geográficas = valor mais recente não vazio.

Saída (padrão): C:/dados_inep/data_cen/dados_processados/dim/  -> estado.csv, municipio.csv,
                municipio_relatorio.csv (UF divergente, nomes diferentes ao longo dos anos)

Exemplos:
  python gerar_dim.py
  python gerar_dim.py --saida "D:/outro/lugar"
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

logger = logging.getLogger("gerar_dim")

RAIZ_PADRAO = Path("C:/dados_inep/data_cen/Microdados da Educação Básica")
SAIDA_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/dim")
ANO_CORTE = 2007

# colunas opcionais de 2007+ (pegas quando existirem): nome no arquivo -> nome no modelo
GEO_2007 = {
    "CO_MESORREGIAO": "cod_mesorregiao", "NO_MESORREGIAO": "nome_mesorregiao",
    "CO_MICRORREGIAO": "cod_microrregiao", "NO_MICRORREGIAO": "nome_microrregiao",
    "CO_REGIAO_GEOG_INTERM": "cod_regiao_geog_intermediaria", "NO_REGIAO_GEOG_INTERM": "nome_regiao_geog_intermediaria",
    "CO_REGIAO_GEOG_IMED": "cod_regiao_geog_imediata", "NO_REGIAO_GEOG_IMED": "nome_regiao_geog_imediata",
}
COLS_MUNICIPIO = ["cod_municipio", "codmunic_inep_9606", "nome_municipio", "sigla_uf", "ano_inicio",
                  *GEO_2007.values()]


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def detectar_formato(caminho: Path) -> tuple[str, str]:
    bruto = caminho.open("rb").read(500_000)
    try:
        bruto.decode("utf-8")
        enc = "utf-8-sig"
    except UnicodeDecodeError:
        enc = "cp1252"
    cab = bruto.split(b"\n", 1)[0].decode(enc.replace("-sig", ""), errors="replace")
    return enc, max([";", "|", ",", "\t"], key=cab.count)


def limpar(c: str) -> str:
    return str(c).strip().lstrip("\ufeff").replace("ï»¿", "")


def achar(raiz: Path, ano: int, padrao: str) -> Path | None:
    pasta = raiz / str(ano)
    if not pasta.exists():
        return None
    achados = sorted((p for p in pasta.rglob("*") if p.is_file() and p.suffix.lower() in (".csv", ".txt", "")
                      and re.fullmatch(padrao, p.stem, flags=re.I)), key=lambda p: -p.stat().st_size)
    return achados[0] if achados else None


def ler(caminho: Path, obrigatorias: dict[str, str], opcionais: dict[str, str]) -> pd.DataFrame:
    """Lê só as colunas pedidas (nome no arquivo -> nome final), contando combinações distintas."""
    enc, sep = detectar_formato(caminho)
    cab = {limpar(c) for c in pd.read_csv(caminho, sep=sep, encoding=enc, nrows=0, encoding_errors="replace").columns}
    faltam = [c for c in obrigatorias if c not in cab]
    if faltam:
        raise SystemExit(f"{caminho.name}: colunas obrigatórias ausentes {faltam}")
    usar = {**obrigatorias, **{k: v for k, v in opcionais.items() if k in cab}}
    df = pd.read_csv(caminho, sep=sep, encoding=enc, encoding_errors="replace", dtype=str,
                     usecols=lambda c: limpar(c) in usar, low_memory=False)
    df.columns = [usar[limpar(c)] for c in df.columns]
    df = df.apply(lambda s: s.str.strip()).replace("", pd.NA)
    return df.groupby(list(df.columns), dropna=False).size().reset_index(name="escolas")


def ler_antigos(raiz: Path, anos: range) -> pd.DataFrame:
    partes = []
    for ano in anos:
        p = achar(raiz, ano, rf"censoesc_{ano}")
        if p is None:
            logger.warning("%d: CENSOESC não encontrado", ano)
            continue
        d = ler(p, {"CODMUNIC": "codmunic", "MUNIC": "nome", "SIGLA": "sigla_uf", "UF": "nome_uf"}, {})
        d.insert(0, "ano", ano)
        partes.append(d)
        logger.info("%d: %d municípios", ano, d.codmunic.nunique())
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


def ler_novos(raiz: Path, anos: range) -> pd.DataFrame:
    partes = []
    for ano in anos:
        p = achar(raiz, ano, rf"microdados_ed_basica_{ano}") or achar(raiz, ano, rf"tabela_escola_{ano}(_v\d+)?")
        if p is None:
            logger.warning("%d: arquivo de escolas não encontrado", ano)
            continue
        d = ler(p, {"CO_MUNICIPIO": "cod_municipio", "NO_MUNICIPIO": "nome", "SG_UF": "sigla_uf"},
                {"NO_UF": "nome_uf", **GEO_2007})
        d.insert(0, "ano", ano)
        partes.append(d)
        logger.info("%d: %d municípios", ano, d.cod_municipio.nunique())
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------
def ibge6(codmunic: pd.Series) -> pd.Series:
    """Dígitos 1-2 (UF) + 8-11 (município) do CODMUNIC = 6 primeiros dígitos do código IBGE."""
    return codmunic.str[0:2] + codmunic.str[7:11]


def normalizar(nome: object) -> str:
    """Para comparar nomes: sem acento, maiúsculo, só letras e números."""
    s = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def mais_frequente(s: pd.Series, pesos: pd.Series) -> str | None:
    t = pesos.groupby(s).sum()
    return None if t.empty else str(t.sort_values(ascending=False).index[0])


def montar(antigos: pd.DataFrame, novos: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # 1. CODMUNIC -> código IBGE de 7 dígitos (pela lista de 2007+)
    lista = novos.dropna(subset=["cod_municipio"]).cod_municipio.drop_duplicates()
    por6 = pd.DataFrame({"cod_municipio": lista, "c6": lista.str[:6]})
    dup6 = por6[por6.c6.duplicated(keep=False)]
    if not dup6.empty:
        raise SystemExit(f"Códigos IBGE com os mesmos 6 primeiros dígitos: {dup6.cod_municipio.tolist()[:10]}")
    mapa6 = por6.set_index("c6").cod_municipio
    cod = antigos.dropna(subset=["codmunic"]).codmunic.drop_duplicates()
    corresp = pd.DataFrame({"codmunic_inep_9606": cod, "cod_municipio": ibge6(cod).map(mapa6)})
    sem = corresp[corresp.cod_municipio.isna()]
    if not sem.empty:
        raise SystemExit(f"{len(sem)} CODMUNIC sem código IBGE correspondente (ex.: {sem.codmunic_inep_9606.tolist()[:10]}). "
                         "Rode verificar_municipios.py.")
    conflito = corresp.groupby("cod_municipio").codmunic_inep_9606.nunique()
    if (conflito > 1).any():
        raise SystemExit(f"Código IBGE com mais de um CODMUNIC: {conflito[conflito > 1].index.tolist()[:10]}")

    # 2. todas as ocorrências (ano, código, nome, UF) dos dois períodos
    a = antigos.merge(corresp, left_on="codmunic", right_on="codmunic_inep_9606")
    todas = pd.concat([a[["ano", "cod_municipio", "nome", "sigla_uf", "nome_uf", "escolas"]],
                       novos[["ano", "cod_municipio", "nome", "sigla_uf", "escolas"]
                             + [c for c in ["nome_uf", *GEO_2007.values()] if c in novos]]], ignore_index=True)
    todas = todas.dropna(subset=["cod_municipio"]).sort_values("ano")

    # 3. município: nome mais recente, UF mais frequente, primeiro ano, geografia mais recente
    g = todas.groupby("cod_municipio")
    mun = pd.DataFrame({
        "nome_municipio": g.nome.agg(lambda s: s.dropna().iloc[-1] if s.notna().any() else None),
        "sigla_uf": g.apply(lambda x: mais_frequente(x.sigla_uf, x.escolas)),
        "ano_inicio": g.ano.min(),
    })
    for col in GEO_2007.values():
        mun[col] = g[col].agg(lambda s: s.dropna().iloc[-1] if s.notna().any() else None) if col in todas else None
    mun = mun.reset_index().merge(corresp, on="cod_municipio", how="left")
    mun = mun[COLS_MUNICIPIO].sort_values("cod_municipio").reset_index(drop=True)

    # 4. estado: nome mais recente por sigla, usando só as linhas em que a UF bate com a do município
    #    (uma linha com UF errada num ano não cria estado nem troca o nome)
    uf_mun = mun.set_index("cod_municipio").sigla_uf
    coerentes = todas[todas.sigla_uf == todas.cod_municipio.map(uf_mun)]
    est = (coerentes.dropna(subset=["nome_uf"]).groupby("sigla_uf").nome_uf
           .agg(lambda s: s.iloc[-1]).reset_index())
    faltam_uf = sorted(set(mun.sigla_uf) - set(est.sigla_uf))
    if faltam_uf:
        raise SystemExit(f"UFs sem nome em nenhum ano: {faltam_uf}")

    # 5. relatório: UF divergente e nomes que mudam (só para conferência; não altera nada)
    rel = []
    uf_ref = mun.set_index("cod_municipio").sigla_uf
    div = todas[todas.sigla_uf.notna() & (todas.sigla_uf != todas.cod_municipio.map(uf_ref))]
    for _, r in div.drop_duplicates(["ano", "cod_municipio", "sigla_uf"]).iterrows():
        rel.append({"tipo": "uf_diferente_da_mais_frequente", "cod_municipio": r.cod_municipio, "ano": r.ano,
                    "valor": r.sigla_uf, "valor_no_modelo": uf_ref[r.cod_municipio]})
    nomes = todas.dropna(subset=["nome"]).groupby("cod_municipio").nome.unique()
    for cod_m, ns in nomes.items():
        if len({normalizar(n) for n in ns}) > 1:          # ignora diferença só de acento/maiúscula/pontuação
            rel.append({"tipo": "nomes_ao_longo_dos_anos", "cod_municipio": cod_m, "ano": None,
                        "valor": " | ".join(ns), "valor_no_modelo": mun.set_index("cod_municipio").nome_municipio[cod_m]})
    return est[["sigla_uf", "nome_uf"]], mun, pd.DataFrame(rel, columns=["tipo", "cod_municipio", "ano", "valor",
                                                                          "valor_no_modelo"])


def gravar(df: pd.DataFrame, caminho: Path) -> None:
    """Grava em .tmp e troca no fim (não deixa CSV pela metade)."""
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, caminho)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO)
    ap.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    ap.add_argument("--ano-inicio", type=int, default=1996)
    ap.add_argument("--ano-fim", type=int, default=2025)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    antigos = ler_antigos(args.raiz, range(args.ano_inicio, min(args.ano_fim, ANO_CORTE - 1) + 1))
    novos = ler_novos(args.raiz, range(max(args.ano_inicio, ANO_CORTE), args.ano_fim + 1))
    if antigos.empty or novos.empty:
        raise SystemExit("Preciso de 1996-2006 e de 2007+ para montar o município.")
    est, mun, rel = montar(antigos, novos)

    args.saida.mkdir(parents=True, exist_ok=True)
    gravar(est, args.saida / "estado.csv")
    gravar(mun, args.saida / "municipio.csv")
    gravar(rel, args.saida / "municipio_relatorio.csv")

    print("\n" + "=" * 70)
    print(f"estado: {len(est)} | municipio: {len(mun)} "
          f"(com CODMUNIC: {mun.codmunic_inep_9606.notna().sum()}, criados após 2006: {mun.codmunic_inep_9606.isna().sum()})")
    print("municípios por ano de início:", mun.ano_inicio.value_counts().sort_index().to_dict())
    print(f"relatório: {(rel.tipo == 'uf_diferente_da_mais_frequente').sum()} ocorrências de UF divergente, "
          f"{(rel.tipo == 'nomes_ao_longo_dos_anos').sum()} municípios com mais de um nome")
    print(f"Arquivos em: {args.saida.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())