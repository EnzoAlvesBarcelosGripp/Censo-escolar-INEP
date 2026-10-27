import os
from pathlib import Path
import re
import pandas as pd

DIRETORIO_ATUAL = Path('C:/dados_inep')

DIRETORIO_MICRODADOS = DIRETORIO_ATUAL / "data_cen" / "Microdados da Educação Básica"
DIRETORIO_PROCESSADOS = DIRETORIO_ATUAL / "dados_processados"
ARQUIVO_DICIONARIO_MESTRE = DIRETORIO_PROCESSADOS / "dicionario_unificado.csv"

ANO_MIN, ANO_MAX = 1996, 2006
MARCADOR_FIM = "QUESTIONÁRIO DO CENSO ESCOLAR"

TIPO = r'(Alfanum[eé]rica|N[uúü]m[eé]rica)'
RE_LINHA_1998 = re.compile(
    r'^([A-Z][A-Z0-9_]*)\s*(.*?)\s*(\d+)\s+(\d+)\s+'
    + r'(?i:' + TIPO + r')\b(.*)$'
)
RE_LINHA = re.compile(r'^([A-Za-z][A-Za-z0-9_]*)\s*(.*?)\s*(\d+)\s+(\d+)\s+' + TIPO + r'\b(.*)$', re.IGNORECASE)
RE_PARAMETROS_SOLTOS = re.compile(
    r'(\d+)\s+(\d+)\s+' + TIPO + r'\b.*$', re.IGNORECASE
)
RE_PARECE_VARIAVEL = re.compile(r'\b' + TIPO + r'\b', re.IGNORECASE)
RE_VAR_SOLTA = re.compile(r'^([A-Z][A-Z0-9_]+)\s+(.*)$')
RE_VAR_ANCORA = re.compile(r'^[A-Z][A-Z0-9_]{1,}$')
RE_CATEGORIA = re.compile(r'\s+[SN]\s+(?:Sim|Não|Nao)\s*$', re.IGNORECASE)

COLUNAS_DICIONARIO = [
    "ano",         # ano do Censo Escolar
    "arquivo",     # base de microdados a que o layout se refere (CENSOESC, EM8, EDUCPROF...)
    "variavel",    # nome da variável no banco de dados
    "descricao",   # descrição normalizada do campo
    "inicio",      # coluna de início (base 1, padrão INEP)
    "tamanho",     # quantidade de caracteres do campo
    "fim",         # inicio + tamanho - 1
    "tipo",        # Numérica ou Alfanumérica
    # colunas de rastreabilidade
    "pagina",      # página do PDF onde a variável foi encontrada
    "fonte_pdf",   # nome do PDF de origem
    "fonte_zip",   # nome do zip de origem
]

VARIAVEIS_FALSAS = {
    2006: ["SUPL", "AUX"]
}


VARIAVEIS_INJETADAS = {
    # No leia-me de 1998, REGULAM aparece como "REGULAM Regulamentação 178 1"
    # sem a coluna Tipo; a variável é categórica e ocupa a posição 178.
    1998: [{"arquivo": "CENSOESC", "variavel": "REGULAM", "descricao": "Regulamentação",
            "inicio": 178, "tamanho": 1, "tipo": "Alfanumérica", "pagina": 7}],
    2004: [{"arquivo": "CENSOESC", "variavel": "CODMUNIC", "descricao": "Código do Município", "inicio": 14, "tamanho": 12, "tipo": "Alfanumérica", "pagina": 8}],
    2005: [{"arquivo": "CENSOESC", "variavel": "CODMUNIC", "descricao": "Código do Município", "inicio": 14, "tamanho": 12, "tipo": "Alfanumérica", "pagina": 8}],
    2006: [
        {"arquivo": "CENSOESC", "variavel": "CODMUNIC", "descricao": "Código do Município", "inicio": 14, "tamanho": 12, "tipo": "Alfanumérica", "pagina": 10},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL1", "descricao": "Matriculas no 1º Ano nascidos após 2000-Tumo Noturno", "inicio": 16546, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL2", "descricao": "Matriculas no 2º Ano nascidos após 2000-Turno Noturno", "inicio": 16558, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL3", "descricao": "Matriculas no 3º Ano nascidos após 2000 - Tumo Noturno", "inicio": 16570, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL4", "descricao": "Matriculas no 4º Ano-nascidos após 2000 - Turno Noturno", "inicio": 16582, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL5", "descricao": "Matriculas no 5º Ano - nascidos após 2000 - Tumo Noturno", "inicio": 16594, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL6", "descricao": "Matriculas no 6º Ano nascidos após 2000-Tumo Noturno", "inicio": 16606, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL7", "descricao": "Matrículas no 7º Ano nascidos após 2000-Turno Noturno", "inicio": 16618, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL8", "descricao": "Matriculas no 8º Ano nascidos após 2000-Turno Noturno", "inicio": 16630, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
        {"arquivo": "CENSOESC", "variavel": "VE9F3NL9", "descricao": "Matriculas no 9º Ano nascidos após 2000 - Tumo Noturno", "inicio": 16642, "tamanho": 12, "tipo": "Numérica", "pagina": 142},
    ],
}

COLUNAS_REJEITADAS = ["ano", "arquivo", "pagina", "motivo",
                      "linha_anterior", "linha", "linha_seguinte", "fonte_pdf"]

COLUNAS_REPETIDAS = ["ano", "arquivo", "variavel", "inicio", "tamanho", "pagina", "fonte_pdf"]

COLUNAS_CONTINUIDADE = ["ano", "arquivo", "tipo_problema", "variavel_anterior", "fim_anterior",
                        "variavel_seguinte", "inicio_seguinte", "delta_bytes",
                        "pagina_anterior", "pagina_seguinte"]

CONTINUIDADE_ACEITA: set[tuple] = {
    (2000, "CENSOESC", "VPE387", "VPE3A4"),
    (2004, "CENSOESC", "VES1412", "VES1421"),
}

CHAVE_PRIMARIA = ["ano", "arquivo", "variavel"]
COLUNAS_MESTRE = COLUNAS_DICIONARIO + ["variavel_original", "observacao"]

_OBS_VEM22 = ("Impressa como VEF22x no PDF do INEP (Total de Matrículas do Ensino Médio); "
              "corrigida para VEM22x por analogia com VEM21x. Confira o cabeçalho do arquivo de dados.")
CORRECOES_ERRATA = {
    (1997, "CENSOESC", f"VEF22{n}", ini): (f"VEM22{n}", _OBS_VEM22)
    for n, ini in zip(range(1, 6), (1843, 1851, 1859, 1867, 1875))
}


def _avisa_continuidade(prob: pd.DataFrame, rotulo: str):
    if prob.empty:
        print(f"[{rotulo}] Continuidade OK (sem buracos nem sobreposições).")
        return
    n_b = int((prob["tipo_problema"] == "buraco").sum())
    n_s = int((prob["tipo_problema"] == "sobreposicao").sum())
    print("!" * 78)
    print(f"[{rotulo}] ATENÇÃO: {len(prob)} problema(s) de continuidade "
          f"({n_b} buraco(s), {n_s} sobreposição(ões)). Possível perda de variáveis.")
    print(prob.to_string(index=False))
    print("!" * 78)


def validar_continuidade(df: pd.DataFrame,
                         tamanhos_esperados: dict | None = None) -> pd.DataFrame:
    """Confere se, dentro de cada (ano, arquivo), os campos são contíguos:
    o 1º começa em 1 e cada `inicio` = `fim` do anterior + 1.

    Retorna um DataFrame (vazio = tudo certo) com uma linha por problema:
      - tipo_problema = 'buraco'         -> delta_bytes > 0 (variáveis provavelmente perdidas)
      - tipo_problema = 'sobreposicao'   -> delta_bytes < 0 (duplicata, errata ou campo agregado)
      - tipo_problema = 'faltando_no_fim'-> só se `tamanhos_esperados` {(ano, arquivo): tamanho_do_registro}
                                             for informado e o maior `fim` ficar abaixo dele.
    Limitação: sem `tamanhos_esperados`, não detecta variáveis faltando no FIM do layout.
    Problemas listados em CONTINUIDADE_ACEITA são omitidos.
    """
    if df.empty:
        return pd.DataFrame(columns=COLUNAS_CONTINUIDADE)

    problemas = []
    ordenado = df.sort_values(["ano", "arquivo", "inicio", "pagina"], kind="stable")
    for (ano, arq), g in ordenado.groupby(["ano", "arquivo"], sort=True, dropna=False):
        g = g.reset_index(drop=True)
        fim_anterior = g["fim"].shift(1).fillna(0).astype(int)
        delta = g["inicio"] - (fim_anterior + 1)

        for i in g.index[delta != 0]:
            ant = g.loc[i - 1] if i > 0 else None
            problemas.append({
                "ano": ano, "arquivo": arq,
                "tipo_problema": "buraco" if delta[i] > 0 else "sobreposicao",
                "variavel_anterior": ant["variavel"] if ant is not None else None,
                "fim_anterior": int(fim_anterior[i]),
                "variavel_seguinte": g.loc[i, "variavel"],
                "inicio_seguinte": int(g.loc[i, "inicio"]),
                "delta_bytes": int(delta[i]),
                "pagina_anterior": int(ant["pagina"]) if ant is not None else None,
                "pagina_seguinte": int(g.loc[i, "pagina"]),
            })

        esperado = (tamanhos_esperados or {}).get((ano, arq))
        if esperado is not None and g["fim"].max() < esperado:
            ult = g.loc[g["fim"].idxmax()]
            problemas.append({
                "ano": ano, "arquivo": arq, "tipo_problema": "faltando_no_fim",
                "variavel_anterior": ult["variavel"], "fim_anterior": int(ult["fim"]),
                "variavel_seguinte": None, "inicio_seguinte": int(ult["fim"]) + 1,
                "delta_bytes": int(esperado - ult["fim"]),
                "pagina_anterior": int(ult["pagina"]), "pagina_seguinte": None,
            })

    prob = pd.DataFrame(problemas, columns=COLUNAS_CONTINUIDADE)
    if CONTINUIDADE_ACEITA and not prob.empty:
        aceitos = prob.apply(lambda r: (r["ano"], r["arquivo"], r["variavel_anterior"],
                                        r["variavel_seguinte"]) in CONTINUIDADE_ACEITA, axis=1)
        prob = prob[~aceitos].reset_index(drop=True)
    return prob

def limpar_arquivos_intermediarios(diretorio: Path | None = None):
    """
    Remove estritamente os arquivos CSV intermediários gerados durante a extração
    (prefixos: dicionario_*, rejeitadas_*, repetidas_*, continuidade_*),
    mantendo intacto o dicionário mestre e arquivos de terceiros.
    """
    raiz = Path(diretorio) if diretorio else DIRETORIO_PROCESSADOS
    
    if not raiz.exists():
        print(f"O diretório {raiz} não existe.")
        return

    # Padrões exatos dos arquivos temporários gerados pelo extracao_csv.py
    padroes_temporarios = [
        "dicionario_*.csv", 
        "rejeitadas_*.csv", 
        "repetidas_*.csv", 
        "continuidade_*.csv"
    ]
    
    apagados = 0
    
    for padrao in padroes_temporarios:
        for arquivo in raiz.glob(padrao):
            # O dicionário mestre também começa com "dicionario_", então precisamos protegê-lo
            if arquivo.name == ARQUIVO_DICIONARIO_MESTRE.name:
                continue
                
            try:
                arquivo.unlink()
                apagados += 1
            except Exception as e:
                print(f"Erro ao apagar {arquivo.name}: {e}")

    print(f"Limpeza concluída: {apagados} arquivos intermediários removidos de {raiz.name}.")