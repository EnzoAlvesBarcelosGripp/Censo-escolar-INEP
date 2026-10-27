from pathlib import Path
import shutil
import re
import zipfile
import os
import pdfplumber
import pandas as pd

try:
    from .utils import (
        DIRETORIO_MICRODADOS, ANO_MIN, ANO_MAX, DIRETORIO_PROCESSADOS, 
        RE_LINHA_1998, RE_LINHA, MARCADOR_FIM, RE_PARAMETROS_SOLTOS,
        RE_VAR_ANCORA, RE_VAR_SOLTA, RE_PARECE_VARIAVEL, RE_CATEGORIA, COLUNAS_DICIONARIO, VARIAVEIS_FALSAS, VARIAVEIS_INJETADAS,
        COLUNAS_REJEITADAS, COLUNAS_REPETIDAS, COLUNAS_CONTINUIDADE, CONTINUIDADE_ACEITA, 
    )
except ImportError:
    from utils import (
        DIRETORIO_MICRODADOS, ANO_MIN, ANO_MAX, DIRETORIO_PROCESSADOS, 
        RE_LINHA_1998, RE_LINHA, MARCADOR_FIM, RE_PARAMETROS_SOLTOS,
        RE_VAR_ANCORA, RE_VAR_SOLTA, RE_PARECE_VARIAVEL, RE_CATEGORIA, COLUNAS_DICIONARIO, VARIAVEIS_FALSAS, VARIAVEIS_INJETADAS,
        COLUNAS_REJEITADAS, COLUNAS_REPETIDAS, COLUNAS_CONTINUIDADE, CONTINUIDADE_ACEITA, 
    )

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

def _linhas_da_pagina(pagina) -> tuple[str, list[str]]:
    """Texto bruto da página + linhas normalizadas (mesmo pré-processamento usado na extração)."""
    texto = pagina.extract_text(x_tolerance=3, y_tolerance=3) or ""
    linhas = [re.sub(r'\s+', ' ', l).strip() for l in texto.split('\n')]
    linhas = [l for l in linhas if l and l != 'Á']
    return texto, linhas

def _eh_pdf_dicionario(nome_do_arquivo: str) -> bool:
    """Verifica se o arquivo é um pdf de dicionário, pelo nome do arquivo 
    (arquivos dicionários tem a palavra 'leia' ou 'dicionario' no nome)."""
    n = nome_do_arquivo.lower()
    return n.endswith('.pdf') and ('leia' in n or 'dicionario' in n)

def _ano_do_zip(arquivo_zip: Path, raiz: Path) -> int | None:
    """Procura o ano no caminho relativo à raiz (evita pegar ano de pastas acima)."""
    try:
        alvo = str(arquivo_zip.relative_to(raiz))
    except ValueError:
        alvo = str(arquivo_zip)
    m = re.search(r'(?<!\d)(19\d\d|20\d\d)(?!\d)', alvo)
    return int(m.group(1)) if m else None

def _limpa_descricao(txt: str) -> str:
    txt = re.sub(r'/\*|\*/|"', '', txt)
    txt = RE_CATEGORIA.sub('', txt)
    return re.sub(r'\s+', ' ', txt).strip()

def extrair_dicionario_variaveis(caminho_pdf: Path, ano: int | None = None,
                                 fonte_zip: str | None = None,
                                 rejeitadas: list | None = None,
                                 repetidas: list | None = None) -> pd.DataFrame:
    """Lê o(s) dicionário(s) de variáveis de um Leia-me em PDF.

    - Detecta cada dicionário pela página-título 'DICIONARIO_XXX' (vira a coluna `arquivo`).
    - Junta descrições quebradas em várias linhas (código na linha do meio).
    - Descarta só a repetição de linha causada pela quebra de página (e REGISTRA cada uma
      em `repetidas`, se a lista for passada).
    - Linhas que parecem variável mas não viraram registro são anexadas em `rejeitadas`
      (dicts com pagina, motivo e linhas vizinhas), para que nada seja descartado em silêncio.
    - Para no marcador do questionário e loga a última página com variáveis.
    """
    caminho_pdf = Path(caminho_pdf)
    rejeitadas = [] if rejeitadas is None else rejeitadas
    repetidas = [] if repetidas is None else repetidas
    registros, vistos = [], set()
    arquivo, ultima_pagina = None, None
    # Correção isolada: a extração de texto do leia-me de 1998 exige regex estrita.
    regex_linha = RE_LINHA_1998 if ano == 1998 else RE_LINHA

    with pdfplumber.open(caminho_pdf) as pdf:
        for num, pagina in enumerate(pdf.pages, start=1):
            texto, linhas = _linhas_da_pagina(pagina)

            if arquivo is not None and MARCADOR_FIM in texto.upper():
                break

            m = re.search(r'DICIONARIO_([A-Z0-9_]+)', texto)
            if m:                       # página-título de um novo dicionário
                arquivo = m.group(1)
            if arquivo is None:         # ainda antes do primeiro dicionário
                continue

            usadas = set()

            def _rejeita(i, motivo):
                rejeitadas.append({
                    "ano": ano, "arquivo": arquivo, "pagina": num, "motivo": motivo,
                    "linha_anterior": linhas[i - 1] if i > 0 else "",
                    "linha": linhas[i],
                    "linha_seguinte": linhas[i + 1] if i + 1 < len(linhas) else "",
                    "fonte_pdf": caminho_pdf.name,
                })

            for i, linha in enumerate(linhas):
                if i in usadas:
                    continue
                
                # 1. Tenta o padrão normal (tudo na mesma linha)
                mm = regex_linha.match(linha)
                if mm:
                    var, desc, ini, tam, tipo, _resto = mm.groups()
                
                # 2. Tenta o padrão quebrado (Parâmetros nesta linha, Variável na linha de baixo)
                else:
                    mm_param = RE_PARAMETROS_SOLTOS.search(linha)
                    if mm_param and i + 1 < len(linhas):
                        mm_var = RE_VAR_SOLTA.match(linhas[i + 1])
                        
                        if mm_var and RE_VAR_ANCORA.match(mm_var.group(1)):
                            ini, tam, tipo = mm_param.groups()
                            var, desc = mm_var.groups()
                            usadas.add(i + 1) # Marca a linha de baixo como usada para o loop pular ela
                        else:
                            if RE_PARECE_VARIAVEL.search(linha):
                                _rejeita(i, "sem_match_regex")
                            continue
                    else:
                        if RE_PARECE_VARIAVEL.search(linha):
                            _rejeita(i, "sem_match_regex")
                        continue

                # ==========================================
                # O restante do código continua EXATAMENTE IGUAL daqui para baixo
                # ==========================================
                if var != var.upper() or not RE_VAR_ANCORA.match(var):
                    _rejeita(i, "variavel_fora_do_padrao")
                    continue

                partes = [desc]

                # descrição que começou nas linhas anteriores sem fechar "*/"
                antes, k = [], i - 1
                while (k >= 0 and i - k <= 3 and not regex_linha.match(linhas[k])
                       and '*/' not in linhas[k] and ('/*' in linhas[k] or antes)):
                    antes.insert(0, linhas[k])
                    k -= 1
                    if antes[0].lstrip().startswith('/*'):
                        break
                if antes and antes[0].lstrip().startswith('/*'):
                    partes = antes + partes
                    usadas.update(range(k + 1, i))

                # continuação nas linhas seguintes até fechar "*/"
                junto, j = ' '.join(partes), i + 1
                while ('/*' in junto and '*/' not in junto and j < len(linhas)
                       and j - i <= 3 and not regex_linha.match(linhas[j])):
                    partes.append(linhas[j])
                    junto = ' '.join(partes)
                    usadas.add(j)
                    j += 1

                inicio, tamanho = int(ini), int(tam)
                chave = (arquivo, var, inicio, tamanho)
                if chave in vistos:     # linha repetida na quebra de página
                    repetidas.append({
                        "ano": ano, "arquivo": arquivo, "variavel": var,
                        "inicio": inicio, "tamanho": tamanho, "pagina": num,
                        "fonte_pdf": caminho_pdf.name,
                    })
                    continue
                vistos.add(chave)

                registros.append({
                    "ano": ano,
                    "arquivo": arquivo,
                    "variavel": var,
                    "descricao": _limpa_descricao(' '.join(partes)),
                    "inicio": inicio,
                    "tamanho": tamanho,
                    "fim": inicio + tamanho - 1,
                    "tipo": "Alfanumérica" if 'alfa' in tipo.lower() else "Numérica",
                    "pagina": num,
                    "fonte_pdf": caminho_pdf.name,
                    "fonte_zip": fonte_zip,
                })
                ultima_pagina = num

    df = pd.DataFrame(registros, columns=COLUNAS_DICIONARIO)
    if not df.empty:
        # CODMUNIC traz a explicação dos dígitos no lugar do nome
        df.loc[df["variavel"] == "CODMUNIC", "descricao"] = "Código do Município"
        
        # 1. Filtra falsos positivos
        if ano in VARIAVEIS_FALSAS:
            falsas = VARIAVEIS_FALSAS[ano]
            df = df[~df["variavel"].isin(falsas)]
            
        # 2. Injeta variáveis corrompidas no PDF
        if ano in VARIAVEIS_INJETADAS:
            inj = pd.DataFrame(VARIAVEIS_INJETADAS[ano])
            inj["ano"] = ano
            inj["fonte_pdf"] = caminho_pdf.name
            inj["fonte_zip"] = fonte_zip
            inj["fim"] = inj["inicio"] + inj["tamanho"] - 1
            # Concatena as forçadas
            df = pd.concat([df, inj], ignore_index=True)
            # Reordena o DataFrame pela posição (inicio) para garantir continuidade correta
            df = df.sort_values(["arquivo", "inicio", "pagina"], kind="stable").reset_index(drop=True)

    print(f"[{caminho_pdf.name}] Última página com variáveis: {ultima_pagina} | total: {len(df)} "
          f"| rejeitadas: {len(rejeitadas)} | repetidas descartadas: {len(repetidas)}")
    return df


def extrair_pdf_dicionario_dados(diretorio: Path | None = None,
                                 anos: set[int] | None = None,
                                 sobrescrever: bool = False):
    """Para cada zip de 1996-2006: extrai o Leia-me em PDF e gera
    `dicionario_<ano>_<nome-do-pdf>.csv` ao lado do zip, mais 3 arquivos de auditoria:
      - rejeitadas_<ano>_<pdf>.csv   -> linhas que parecem variável mas não viraram registro
      - repetidas_<ano>_<pdf>.csv    -> linhas descartadas como repetição de quebra de página
      - continuidade_<ano>_<pdf>.csv -> buracos/sobreposições nas posições (vazio = OK)

    O PDF NÃO é mais apagado (unlink comentado): ele é a única fonte para corrigir
    buracos de continuidade.

    anos:         se informado, processa só esses anos (ex.: {1998, 2000, 2006}).
    sobrescrever: se True, reprocessa mesmo que o CSV do ano já exista.
    """
    raiz = Path(diretorio) if diretorio else DIRETORIO_MICRODADOS

    for arquivo_zip in sorted(raiz.rglob("*.zip")):
        ano = _ano_do_zip(arquivo_zip, raiz)
        if ano is None or not (ANO_MIN <= ano <= ANO_MAX):
            continue
        if anos is not None and ano not in anos:
            continue

        with zipfile.ZipFile(arquivo_zip, 'r') as zip_ref:
            for nome_do_arquivo in zip_ref.namelist():
                if not _eh_pdf_dicionario(nome_do_arquivo):
                    continue

                nome = os.path.basename(nome_do_arquivo)
                if not nome:
                    continue

                destino_pdf = arquivo_zip.parent / nome
                sufixo = f"{ano}_{destino_pdf.stem}.csv"
                
                # CRIAR A PASTA SE NÃO EXISTIR E DEFINIR O NOVO DESTINO
                DIRETORIO_PROCESSADOS.mkdir(parents=True, exist_ok=True)
                destino_csv = DIRETORIO_PROCESSADOS / f"dicionario_{sufixo}"

                # Verificação se o CSV final já foi processado e salvo
                if destino_csv.exists() and not sobrescrever:
                    print(f'[{ano}] [PULADO] Dicionário já existe: {destino_csv.name}')
                    continue

                print(f'[{ano}] Extraindo {nome_do_arquivo}...')
                with zip_ref.open(nome_do_arquivo) as origem, open(destino_pdf, "wb") as destino:
                    shutil.copyfileobj(origem, destino)

                try:
                    rejeitadas, repetidas = [], []
                    df_layout = extrair_dicionario_variaveis(
                        destino_pdf, ano=ano, fonte_zip=arquivo_zip.name,
                        rejeitadas=rejeitadas, repetidas=repetidas,
                    )
                    if df_layout.empty:
                        print(f'[{ano}] Nenhuma variável encontrada em {nome}; PDF mantido.')
                        continue

                    # arquivos de auditoria (sempre gravados, mesmo vazios, para não ficar log velho)
                    pd.DataFrame(rejeitadas, columns=COLUNAS_REJEITADAS).to_csv(
                        DIRETORIO_PROCESSADOS / f"rejeitadas_{sufixo}", index=False, encoding='utf-8-sig')
                    pd.DataFrame(repetidas, columns=COLUNAS_REPETIDAS).to_csv(
                        DIRETORIO_PROCESSADOS / f"repetidas_{sufixo}", index=False, encoding='utf-8-sig')
                    prob = validar_continuidade(df_layout)
                    prob.to_csv(DIRETORIO_PROCESSADOS / f"continuidade_{sufixo}",
                                index=False, encoding='utf-8-sig')
                    _avisa_continuidade(prob, f"{ano} {nome}")

                    df_layout.to_csv(destino_csv, index=False, encoding='utf-8-sig')
                except Exception as erro:
                    print(f'[{ano}] Erro em {nome}: {erro}; PDF mantido.')
                    continue

                # destino_pdf.unlink()  # DESATIVADO: manter o PDF até a continuidade ficar limpa
                print(f'[{ano}] CSV salvo em {destino_csv.name}; PDF mantido.')