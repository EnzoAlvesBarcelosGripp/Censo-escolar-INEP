"""
Gera as tabelas do bloco 2007-2025 (schema b0725) direto dos arquivos do INEP. É o único script deste bloco
que o orquestrador chama; o modelo (tabelas, colunas, tipos) vem do módulo modelar_b0725.py, na mesma pasta.

Entrada : <raiz>/<ano>/microdados_ed_basica_<ano>.csv           (2007-2024)
          <raiz>/<ano>/suplemento_cursos_tecnicos_<ano>.csv      (2023-2024)
          <raiz>/2025/Tabela_<Escola|Matricula|Turma|Docente|Gestor_Escolar|Curso_Tecnico>_2025_V2.csv
          dicionários .xlsx das mesmas pastas; dim/municipio.csv de gerar_dim.py
Saída   : <saida>/<ano>/<tabela>.csv  (unidade de reprocessamento; o ano entra inteiro ou não entra)
          <saida>/<tabela>.csv        os 19 anos juntos (quando todos existem) + curso, variavel, dominio
          <saida>/auditoria/codigos_fora_do_dicionario.csv   (G9: valores mantidos, só para conferir)
          "ddl 07 25.sql" na pasta deste script (sai do MESMO modelo que gerou os dados)
          Arquivos intermediários ficam numa pasta temporária do sistema, apagada no fim (mesmo com erro).

Regras:
  - G2: coluna renomeada é lida pelo nome antigo nos anos antigos (modelar_b0725.RENOMEACOES);
  - G3: coluna que não existe no ano fica NULL;
  - D3: em 2025 cada variável vem do arquivo do seu tema (Matrícula, Turma...) ou, se não estiver nele, da
        Tabela Escola; a identificação repetida nos outros arquivos é ignorada; as peças juntam por CO_ENTIDADE;
  - G5: linha sem nenhum valor além da chave não é gravada (zeros ficam);
  - G8 (PARA): CO_ENTIDADE vazio/repetido, ano diferente do da pasta, município fora de dim.municipio,
        dependência/situação/localização fora do domínio, valor não numérico, texto maior que o DDL,
        escola de outra tabela ou do suplemento que não está na base do ano (D4);
  - G9 (avisa): código TP_/IN_ fora do dicionário (de TODOS os anos) -> mantido, listado em
    codigos_fora_do_dicionario.csv e acrescentado ao dominio.csv com o rótulo "(código fora do dicionário)".

Exemplos:
  python gerar_tabelas_0725.py
  python gerar_tabelas_0725.py --ano-inicio 2025 --ano-fim 2025
  python gerar_tabelas_0725.py --so-ddl          # só o modelo: DDL, variavel.csv, dominio.csv
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

sys.dont_write_bytecode = True                                 # não cria __pycache__ na pasta do código
sys.path.insert(0, str(Path(__file__).resolve().parent))      # o módulo fica na mesma pasta do gerador
import modelar_b0725 as m                                      # noqa: E402

logger = logging.getLogger("tabelas_0725")

RAIZ_PADRAO = m.RAIZ_PADRAO
MUNICIPIOS_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/dim/municipio.csv")
SAIDA_PADRAO = Path("C:/dados_inep/data_cen/dados_processados/Microdados_0725")
DDL_PADRAO = Path(__file__).resolve().with_name("ddl 07 25.sql")
ANOS = list(range(2007, 2026))
TABELAS = m.ORDEM_TABELAS                                     # escola ... curso_tecnico
CHAVE_CURSO = "CO_CURSO_EDUC_PROFISSIONAL"
DOM_CHAVE = {"TP_DEPENDENCIA": {1, 2, 3, 4}, "TP_SITUACAO_FUNCIONAMENTO": {1, 2, 3}, "TP_LOCALIZACAO": {1, 2}}


# ===========================================================================
# 1. Plano do ano: de que arquivo vem cada coluna de cada tabela
# ===========================================================================
def plano_do_ano(cabs_ano: dict[str, tuple[Path, list[str]]], modelo: pd.DataFrame
                 ) -> dict[str, dict[str, list[tuple[str, str]]]]:
    """{tabela: {arquivo: [(coluna_destino, coluna_no_arquivo)]}}. Tabelas 'curso_tecnico' e 'curso' só vêm do
    arquivo de cursos; as demais seguem a prioridade arquivo do tema -> arquivo base -> outro."""
    base = next(n for n in cabs_ano if n in m.BASE)
    plano: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in modelo.itertuples():
        if r.tabela not in TABELAS and r.tabela != "curso":
            continue
        nomes = [r.variavel] + ([r.nome_anterior] if r.nome_anterior else [])
        tem = {arq: next((n for n in nomes if n in cols), None) for arq, (_, cols) in cabs_ano.items()}
        tem = {a: n for a, n in tem.items() if n}
        if not tem:
            continue                                            # G3: não existe no ano -> NULL
        curso_arq = "curso_tecnico" if "curso_tecnico" in tem else "suplemento_cursos_tecnicos"
        if r.tabela in ("curso_tecnico", "curso"):
            ordem = [curso_arq]
        else:
            ordem = [m.ARQUIVO_DO_TEMA.get(r.tabela), base] + sorted(tem)
            ordem = [a for a in ordem if a and a not in ("curso_tecnico", "suplemento_cursos_tecnicos")]
        arq = next((a for a in ordem if a in tem), None)
        if arq:
            plano[r.tabela][arq].append((r.variavel, tem[arq]))
    return plano


# ===========================================================================
# 2. Conversão e validação (G8/G9)
# ===========================================================================
def converter(s: pd.Series, tipo: str, nome: str, ano: int, arq: str) -> pd.Series:
    s = s.astype("string").str.strip()
    s = s.mask(s == "")
    onde = f"{ano} {arq}.{nome}"
    if re.match(r"(TINY|SMALL|BIG)?INT$", tipo):
        n = pd.to_numeric(s, errors="coerce")
        ruim = s.notna() & (n.isna() | (n != n.round()))
        if ruim.any():
            raise SystemExit(f"{onde}: valor não inteiro {s[ruim].unique()[:5].tolist()} ({int(ruim.sum())} linhas)")
        return n.astype("Int64")
    if tipo.startswith("DECIMAL"):
        n = pd.to_numeric(s.str.replace(",", ".", regex=False), errors="coerce")
        ruim = s.notna() & n.isna()
        if ruim.any():
            raise SystemExit(f"{onde}: valor não numérico {s[ruim].unique()[:5].tolist()} ({int(ruim.sum())} linhas)")
        return n
    if tipo == "CHAR(7)":
        ruim = s.notna() & ~s.str.fullmatch(r"\d{7}").fillna(False)
        if ruim.any():
            raise SystemExit(f"{onde}: código de município inválido {s[ruim].unique()[:5].tolist()}")
        return s
    tam = int(re.search(r"\((\d+)\)", tipo).group(1))
    grande = s.str.len() > tam
    if grande.fillna(False).any():
        raise SystemExit(f"{onde}: texto maior que {tipo} (máx. {int(s.str.len().max())}): "
                         f"{s[grande.fillna(False)].head(3).tolist()}. Aumente o tamanho em modelar_b0725.tipo_sql.")
    return s


def ler_blocos(caminho: Path, colunas: set[str], chunk: int):
    enc, sep = m.detectar_formato(caminho)
    for ch in pd.read_csv(caminho, sep=sep, encoding=enc, encoding_errors="replace", dtype=str, chunksize=chunk,
                          usecols=lambda c: m.limpar(c) in colunas, low_memory=False, keep_default_na=False):
        ch.columns = [m.limpar(c) for c in ch.columns]
        yield ch


# ===========================================================================
# 3. Um ano
# ===========================================================================
class Escritor:
    """CSV gravado em .tmp; só vira definitivo em confirmar() (o ano inteiro de uma vez)."""

    def __init__(self, final: Path, colunas: list[str]):
        self.final, self.tmp, self.colunas, self.linhas = final, final.with_name(final.name + ".tmp"), colunas, 0
        self.tmp.unlink(missing_ok=True)
        pd.DataFrame(columns=colunas).to_csv(self.tmp, index=False, encoding="utf-8")

    def escrever(self, df: pd.DataFrame) -> None:
        if len(df):
            df[self.colunas].to_csv(self.tmp, mode="a", header=False, index=False, encoding="utf-8",
                                    float_format="%.15g")
            self.linhas += len(df)

    def confirmar(self) -> None:
        os.replace(self.tmp, self.final)

    def descartar(self) -> None:
        self.tmp.unlink(missing_ok=True)


def colunas_finais(modelo: pd.DataFrame, tabela: str) -> list[str]:
    chave = ["ano", "co_entidade"] + (["co_curso_educ_profissional"] if tabela == "curso_tecnico" else [])
    cols = modelo[modelo.tabela == tabela].variavel.str.lower().tolist()
    if tabela == "curso":
        return ["ano", "co_curso_educ_profissional"] + cols
    return chave + [c for c in cols if c not in chave]


def processar_ano(ano: int, cabs_ano: dict, modelo: pd.DataFrame, dom: dict[str, set[int]], municipios: set[str],
                  args, tmp: Path, fora: Counter) -> None:
    t0 = time.perf_counter()
    plano = plano_do_ano(cabs_ano, modelo)
    tipos = dict(zip(modelo.variavel, modelo.tipo_sql))
    base = next(n for n in cabs_ano if n in m.BASE)
    pasta = args.saida / str(ano)
    pasta.mkdir(parents=True, exist_ok=True)
    pecas: dict[str, list[Path]] = defaultdict(list)
    chaves_base: set[int] = set()

    # ---------- Fase A: cada arquivo do ano uma vez, em blocos ----------
    por_arquivo = defaultdict(dict)
    for tab, arqs in plano.items():
        for arq, cols in arqs.items():
            por_arquivo[arq][tab] = cols
    for arq in sorted(por_arquivo, key=lambda a: a not in m.BASE):        # o arquivo base primeiro
        caminho = cabs_ano[arq][0]
        e_curso = arq in ("curso_tecnico", "suplemento_cursos_tecnicos")
        chave = ["CO_ENTIDADE"] + ([CHAVE_CURSO] if e_curso else [])
        usar = set(chave) | {"NU_ANO_CENSO"} | {o for cols in por_arquivo[arq].values() for _, o in cols}
        vistos: set = set()
        for ch in ler_blocos(caminho, usar, args.chunk):
            ce = converter(ch["CO_ENTIDADE"], "INT", "CO_ENTIDADE", ano, arq)
            if ce.isna().any():
                raise SystemExit(f"{ano} {arq}: {int(ce.isna().sum())} linhas sem CO_ENTIDADE")
            if "NU_ANO_CENSO" in ch and (pd.to_numeric(ch["NU_ANO_CENSO"], errors="coerce") != ano).any():
                raise SystemExit(f"{ano} {arq}: NU_ANO_CENSO diferente de {ano} no arquivo {caminho.name}")
            k = list(zip(ce, converter(ch[CHAVE_CURSO], "INT", CHAVE_CURSO, ano, arq))) if e_curso else list(ce)
            rep = pd.Series(k).duplicated() | pd.Series(k).isin(vistos)
            if rep.any():
                raise SystemExit(f"{ano} {arq}: chave repetida {pd.Series(k)[rep].head(5).tolist()}")
            vistos.update(k)
            if arq == base:
                chaves_base.update(ce)
            for tab, cols in por_arquivo[arq].items():
                df = pd.DataFrame({"ano": ano, "co_entidade": ce})
                if tab in ("curso_tecnico", "curso"):
                    df["co_curso_educ_profissional"] = [c for _, c in k]
                for destino, origem in cols:
                    if destino in ("CO_ENTIDADE", CHAVE_CURSO):
                        continue
                    v = converter(ch[origem], tipos[destino], origem, ano, arq)
                    if destino in DOM_CHAVE and v.notna().any() and not set(v.dropna()) <= DOM_CHAVE[destino]:
                        raise SystemExit(f"{ano} {arq}.{origem}: código fora do domínio "
                                         f"{sorted(set(v.dropna()) - DOM_CHAVE[destino])} (dim.* do 01_dim.sql)")
                    if destino == "CO_MUNICIPIO" and not set(v.dropna()) <= municipios:
                        raise SystemExit(f"{ano} {arq}: CO_MUNICIPIO fora de dim.municipio "
                                         f"{sorted(set(v.dropna()) - municipios)[:10]}")
                    if destino in dom and destino.startswith(("TP_", "IN_")) and v.notna().any():
                        for cod, n in v[v.notna() & ~v.isin(dom[destino])].value_counts().items():
                            fora[(ano, destino, int(cod))] += int(n)
                    df[destino.lower()] = v
                peca = tmp / f"{ano}__{tab}__{arq}.csv"
                df.to_csv(peca, mode="a", header=not peca.exists(), index=False, encoding="utf-8",
                          float_format="%.15g")
                if peca not in pecas[tab]:
                    pecas[tab].append(peca)
        logger.info("%d %s: %d linhas lidas", ano, caminho.name, len(vistos))

    # ---------- Fase B: montar cada tabela ----------
    escritores: dict[str, Escritor] = {}
    try:
        for tab in TABELAS + ["curso"]:
            cols = colunas_finais(modelo, tab)
            esc = Escritor(pasta / f"{tab}.csv", cols)
            escritores[tab] = esc
            if tab not in pecas:
                continue
            chave = ["ano", "co_entidade"] + (["co_curso_educ_profissional"] if tab in ("curso_tecnico", "curso") else [])
            if len(pecas[tab]) == 1:
                partes = pd.read_csv(pecas[tab][0], dtype=str, chunksize=args.chunk)
            else:                                              # 2025: junta as peças de arquivos diferentes
                junto = None
                for pc in pecas[tab]:
                    d = pd.read_csv(pc, dtype=str)
                    junto = d if junto is None else junto.merge(d, on=chave, how="outer")
                partes = [junto]
            for d in partes:
                if tab == "curso":
                    d = d.drop(columns="co_entidade").drop_duplicates("co_curso_educ_profissional")
                    esc.escrever(d.reindex(columns=cols))
                    continue
                orfas = ~d.co_entidade.astype(int).isin(chaves_base)
                if orfas.any():
                    raise SystemExit(f"{ano} {tab}: {int(orfas.sum())} escolas que não estão no arquivo base "
                                     f"(ex.: {d.co_entidade[orfas].head(5).tolist()})")
                d = d.reindex(columns=cols)
                valores = d.drop(columns=chave)
                if tab != "escola":
                    d = d[valores.notna().any(axis=1)]          # G5
                esc.escrever(d)
            if tab == "curso" and esc.linhas:                  # um curso pode aparecer em várias escolas
                c = pd.read_csv(esc.tmp, dtype=str).drop_duplicates("co_curso_educ_profissional")
                c.to_csv(esc.tmp, index=False, encoding="utf-8")
                esc.linhas = len(c)
    except BaseException:
        for e in escritores.values():
            e.descartar()
        raise
    # ---------- Fase C: o ano inteiro de uma vez ----------
    for e in escritores.values():
        e.confirmar()
    for pc in [p for ps in pecas.values() for p in ps]:
        pc.unlink(missing_ok=True)
    print(f"\n=== {ano} === escolas: {len(chaves_base)}  ({time.perf_counter() - t0:.0f} s)\n  " +
          "  ".join(f"{t}: {e.linhas}" for t, e in escritores.items()))


# ===========================================================================
# 4. Consolidação e metadados
# ===========================================================================
def consolidar(saida: Path) -> None:
    faltam = [f"{a}/{t}.csv" for a in ANOS for t in TABELAS + ["curso"] if not (saida / str(a) / f"{t}.csv").is_file()]
    if faltam:
        logger.warning("consolidação não feita: faltam %d arquivos (ex.: %s). Gere todos os anos.", len(faltam),
                       faltam[:4])
        return
    for tab in TABELAS:
        final, tmp = saida / f"{tab}.csv", saida / f"{tab}.csv.tmp"
        cab_ref, n = None, 0
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as out:
                for ano in ANOS:
                    with open(saida / str(ano) / f"{tab}.csv", encoding="utf-8", newline="") as f:
                        cab = f.readline()
                        if cab_ref is None:
                            cab_ref = cab
                            out.write(cab)
                        elif cab != cab_ref:
                            raise SystemExit(f"{tab}: colunas de {ano} diferentes das de 2007; gere o ano de novo")
                        for linha in f:
                            out.write(linha)
                            n += 1
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, final)
        logger.info("consolidado: %s (%d linhas)", final.name, n)
    # curso: o nome e a área do ano mais recente em que o curso aparece (SCD tipo 1)
    c = pd.concat([pd.read_csv(saida / str(a) / "curso.csv", dtype=str) for a in ANOS], ignore_index=True)
    c = c.sort_values("ano").drop_duplicates("co_curso_educ_profissional", keep="last").drop(columns="ano")
    c.to_csv(saida / "curso.csv.tmp", index=False, encoding="utf-8")
    os.replace(saida / "curso.csv.tmp", saida / "curso.csv")
    logger.info("consolidado: curso.csv (%d cursos)", len(c))


ROTULO_FORA = "(código fora do dicionário)"


def completar_dominio(saida: Path) -> None:
    """Acrescenta ao dominio.csv os códigos que os DADOS usam e nenhum dicionário (de nenhum ano) descreve.

    O rótulo só diz que o INEP usou o valor sem descrevê-lo; não afirma o que ele significa. Os dados das
    escolas não mudam. Lê auditoria/codigos_fora_do_dicionario.csv e o regrava só com o que continua sem rótulo."""
    aud_csv, dom_csv = saida / "auditoria" / "codigos_fora_do_dicionario.csv", saida / "dominio.csv"
    if not aud_csv.is_file():
        logger.info("sem auditoria de códigos fora do dicionário: dominio.csv fica só com o que os dicionários descrevem")
        return
    dom = pd.read_csv(dom_csv, dtype={"variavel": str, "rotulo": str})
    dom = dom[dom.rotulo != ROTULO_FORA]
    aud = pd.read_csv(aud_csv, dtype={"variavel": str})
    conhecidos = set(zip(dom.variavel, dom.codigo))
    aud = aud[[(v, c) not in conhecidos for v, c in zip(aud.variavel, aud.codigo)]]
    aud.to_csv(aud_csv.with_name(aud_csv.name + ".tmp"), index=False, encoding="utf-8-sig")
    os.replace(aud_csv.with_name(aud_csv.name + ".tmp"), aud_csv)
    variaveis = set(pd.read_csv(saida / "variavel.csv", dtype=str).variavel)
    novos = aud[aud.variavel.isin(variaveis)][["variavel", "codigo"]].drop_duplicates().assign(rotulo=ROTULO_FORA)
    dom = pd.concat([dom, novos], ignore_index=True).sort_values(["variavel", "codigo"])
    dom.to_csv(dom_csv.with_name(dom_csv.name + ".tmp"), index=False, encoding="utf-8")
    os.replace(dom_csv.with_name(dom_csv.name + ".tmp"), dom_csv)
    logger.info("dominio.csv: %d códigos sem descrição em nenhum dicionário (rótulo '%s'); %d linhas no total",
                len(novos), ROTULO_FORA, len(dom))


def gravar_modelo(modelo: pd.DataFrame, dom: pd.DataFrame, saida: Path, ddl_caminho: Path) -> None:
    saida.mkdir(parents=True, exist_ok=True)
    v = modelo[modelo.tabela.isin(TABELAS + ["curso"])]
    v = v[~((v.tabela == "curso_tecnico") & (v.variavel == CHAVE_CURSO))]
    v[["variavel", "tabela", "descricao", "ano_inicio", "ano_fim", "anos", "nome_anterior"]] \
        .to_csv(saida / "variavel.csv", index=False, encoding="utf-8")
    dom[dom.variavel.isin(set(v.variavel))].to_csv(saida / "dominio.csv", index=False, encoding="utf-8")
    ddl_caminho.write_text(m.ddl(modelo), encoding="utf-8")
    logger.info("modelo: %d colunas em %d tabelas -> %s", len(v), v.tabela.nunique(), ddl_caminho)


# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO)
    ap.add_argument("--municipios", type=Path, default=MUNICIPIOS_PADRAO)
    ap.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    ap.add_argument("--ddl", type=Path, default=DDL_PADRAO, help="onde gravar o DDL (padrão: ao lado do script)")
    ap.add_argument("--ano-inicio", type=int, default=2007)
    ap.add_argument("--ano-fim", type=int, default=2025)
    ap.add_argument("--chunk", type=int, default=50_000)
    ap.add_argument("--so-ddl", action="store_true", help="só o modelo: DDL, variavel.csv e dominio.csv")
    args = ap.parse_args()
    if not (2007 <= args.ano_inicio <= args.ano_fim <= 2025):
        ap.error("o bloco vai de 2007 a 2025")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cabs = m.ler_cabecalhos(args.raiz)                     # todos os anos: o período de cada coluna depende deles
    modelo, dom = m.montar_modelo(args.raiz, cabs)
    gravar_modelo(modelo, dom, args.saida, args.ddl)
    print("\n=== colunas por tabela e período ===\n" + m.resumo(modelo))
    if args.so_ddl:
        completar_dominio(args.saida)          # usa a auditoria da última execução completa, se existir
        return 0

    if not args.municipios.is_file():
        raise SystemExit(f"{args.municipios} não encontrado. Rode antes: python gerar_dim.py")
    municipios = set(pd.read_csv(args.municipios, dtype=str).cod_municipio)
    dom_cod = dom.groupby("variavel").codigo.apply(set).to_dict()
    fora: Counter = Counter()
    with tempfile.TemporaryDirectory(prefix="b0725_") as tmp:      # apagada no fim, mesmo com erro
        for ano in range(args.ano_inicio, args.ano_fim + 1):
            processar_ano(ano, cabs[ano], modelo, dom_cod, municipios, args, Path(tmp), fora)
    if fora:
        aud = args.saida / "auditoria"
        aud.mkdir(exist_ok=True)
        f = pd.DataFrame([{"ano": a, "variavel": v, "codigo": c, "linhas": n} for (a, v, c), n in sorted(fora.items())])
        f.to_csv(aud / "codigos_fora_do_dicionario.csv", index=False, encoding="utf-8-sig")
        logger.warning("G9: %d combinações (ano, variável, código) fora do dicionário -> %s (valores mantidos)",
                       len(f), aud / "codigos_fora_do_dicionario.csv")
    completar_dominio(args.saida)
    if (args.ano_inicio, args.ano_fim) != (2007, 2025):
        logger.warning("execução parcial: dominio.csv só inclui códigos sem descrição dos anos processados")
    consolidar(args.saida)
    return 0


if __name__ == "__main__":
    sys.exit(main())