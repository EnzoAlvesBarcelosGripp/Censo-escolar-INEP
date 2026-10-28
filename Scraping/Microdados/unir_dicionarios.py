from pathlib import Path
import pandas as pd

try:
    from .utils import (
        DIRETORIO_PROCESSADOS,
        ARQUIVO_DICIONARIO_MESTRE,
        CORRECOES_ERRATA,
        COLUNAS_MESTRE,
        CHAVE_PRIMARIA,
        _avisa_continuidade,
        validar_continuidade,
    )
except ImportError:
    from utils import (
        DIRETORIO_PROCESSADOS,
        ARQUIVO_DICIONARIO_MESTRE,
        CORRECOES_ERRATA,
        COLUNAS_MESTRE,
        CHAVE_PRIMARIA,
        _avisa_continuidade,
        validar_continuidade,
    )

def consolidar_dicionario_mestre(diretorio: Path | None = None,
                                 saida: Path | None = None,
                                 aplicar_errata: bool = True,
                                 estrito: bool = False) -> pd.DataFrame:
    """Junta todos os `dicionario_*.csv` num único CSV longo.

    Chave primária: (ano, arquivo, variavel). Duplicatas na chave NÃO são
    removidas em silêncio: são listadas na tela para você decidir.
    Também roda a validação de continuidade e grava `validacao_continuidade.csv`
    ao lado do mestre; com estrito=True, levanta ValueError se houver problemas.
    """
    raiz = Path(diretorio) if diretorio else DIRETORIO_PROCESSADOS
    destino = Path(saida) if saida else ARQUIVO_DICIONARIO_MESTRE

    csvs = [c for c in sorted(raiz.rglob("dicionario_*.csv"))
            if c.resolve() != destino.resolve()]
    if not csvs:
        raise FileNotFoundError(f"Nenhum dicionario_*.csv encontrado em {raiz}")

    partes = [pd.read_csv(c, encoding='utf-8-sig', dtype={"variavel": str, "arquivo": str})
              for c in csvs]
    mestre = pd.concat(partes, ignore_index=True)

    mestre["fim"] = mestre["inicio"] + mestre["tamanho"] - 1

    # auditoria + correção de erratas conhecidas (nome original sempre preservado)
    mestre["variavel_original"] = mestre["variavel"]
    mestre["observacao"] = ""
    if aplicar_errata:
        chaves = list(zip(mestre["ano"], mestre["arquivo"], mestre["variavel"], mestre["inicio"]))
        for pos, ch in enumerate(chaves):
            if ch in CORRECOES_ERRATA:
                novo, obs = CORRECOES_ERRATA[ch]
                mestre.iloc[pos, mestre.columns.get_loc("variavel")] = novo
                mestre.iloc[pos, mestre.columns.get_loc("observacao")] = obs
        print(f"Erratas aplicadas: {(mestre['observacao'] != '').sum()} linhas")

    mestre = mestre[COLUNAS_MESTRE].sort_values(
        ["ano", "arquivo", "inicio", "pagina"], kind="stable"
    ).reset_index(drop=True)

    dup = mestre.duplicated(CHAVE_PRIMARIA, keep=False)
    if dup.any():
        print(f"ATENÇÃO: {dup.sum()} linhas com chave {tuple(CHAVE_PRIMARIA)} repetida:")
        print(mestre[dup].to_string())

    destino.parent.mkdir(parents=True, exist_ok=True)
    mestre.to_csv(destino, index=False, encoding='utf-8-sig')
    print(f"Dicionário mestre: {len(mestre)} linhas, {mestre['ano'].nunique()} anos, "
          f"{len(csvs)} arquivos -> {destino}")

    # validação de continuidade do mestre inteiro
    prob = validar_continuidade(mestre)
    prob.to_csv(destino.parent / "validacao_continuidade.csv", index=False, encoding='utf-8-sig')
    _avisa_continuidade(prob, "MESTRE")
    if estrito and not prob.empty:
        raise ValueError(f"{len(prob)} problema(s) de continuidade; veja validacao_continuidade.csv")
    return mestre