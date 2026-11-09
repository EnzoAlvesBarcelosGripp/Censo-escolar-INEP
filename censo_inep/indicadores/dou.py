import os
import csv
import unicodedata
from pathlib import Path
from openpyxl import load_workbook

# Diretórios
PASTA_DATA = Path('C:/dados_inep/data_cen')
PASTA_OUTPUT = Path('C:/dados_inep/data_cen/dados_processados')
TAMANHO_CHUNK = 10000

UFS_BRASIL = [
    "ACRE", "ALAGOAS", "AMAPA", "AMAZONAS", "BAHIA", "CEARA", "DISTRITO FEDERAL", 
    "ESPIRITO SANTO", "GOIAS", "MARANHAO", "MATO GROSSO", "MATO GROSSO DO SUL", 
    "MINAS GERAIS", "PARA", "PARAIBA", "PARANA", "PERNAMBUCO", "PIAUI", 
    "RIO DE JANEIRO", "RIO GRANDE DO NORTE", "RIO GRANDE DO SUL", "RONDONIA", 
    "RORAIMA", "SANTA CATARINA", "SAO PAULO", "SERGIPE", "TOCANTINS"
]

# ==========================================
# ESTRUTURA DA TABELA DIMENSÃO (dim_localidade)
# ==========================================
dicionario_localidades = {}
contador_id_localidade = 1

def carregar_dim_localidade():
    """Carrega os IDs já existentes para a memória, evitando perder dados ao rodar o script novamente."""
    global contador_id_localidade
    caminho_dim = PASTA_DATA / "dim_localidade.csv"
    
    if caminho_dim.exists():
        with open(caminho_dim, mode='r', encoding='utf-8') as f_in:
            leitor = csv.reader(f_in, delimiter=';')
            next(leitor, None) # Pula o cabeçalho
            for linha in leitor:
                if len(linha) == 4:
                    id_loc, nome, uf, nivel = linha
                    id_loc = int(id_loc)
                    dicionario_localidades[(nome, uf, nivel)] = id_loc
                    
                    # Atualiza o contador para o próximo ID disponível
                    if id_loc >= contador_id_localidade:
                        contador_id_localidade = id_loc + 1
        print(f"[MEMÓRIA] Carregados {len(dicionario_localidades)} registros da dim_localidade.csv existente.")

def obter_id_localidade(nome, uf, nivel):
    global contador_id_localidade
    chave = (nome, uf, nivel)
    if chave not in dicionario_localidades:
        dicionario_localidades[chave] = contador_id_localidade
        contador_id_localidade += 1
    return dicionario_localidades[chave]

def exportar_dim_localidade():
    caminho_dim = PASTA_OUTPUT / "dim_localidade.csv"
    with open(caminho_dim, mode='w', newline='', encoding='utf-8') as f_out:
        escritor_csv = csv.writer(f_out, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        escritor_csv.writerow(["id_localidade", "nome_localidade", "sigla_uf", "nivel_geografico"])
        
        for (nome, uf, nivel), id_loc in sorted(dicionario_localidades.items(), key=lambda x: x[1]):
            escritor_csv.writerow([id_loc, nome, uf, nivel])
            
    print(f"\n[OK] Tabela dimensão 'dim_localidade.csv' exportada com {len(dicionario_localidades)} registros únicos.")


# ==========================================
# VERIFICAÇÃO DE DUPLICIDADE (Tabela Fato)
# ==========================================
def obter_anos_processados(arquivo_csv):
    """Lê o arquivo Fato existente e retorna um conjunto (set) com os anos que já foram salvos lá."""
    anos_encontrados = set()
    if arquivo_csv.exists():
        with open(arquivo_csv, mode='r', encoding='utf-8') as f_in:
            leitor = csv.reader(f_in, delimiter=';')
            next(leitor, None) # Pula o cabeçalho
            for linha in leitor:
                if linha and linha[0]:
                    anos_encontrados.add(str(linha[0]))
    return anos_encontrados


# ==========================================
# FUNÇÕES DE LIMPEZA
# ==========================================
def remover_acentos(texto):
    if not texto: return ""
    nfkd = unicodedata.normalize('NFKD', str(texto))
    return u"".join([c for c in nfkd if not unicodedata.combining(c)]).strip().upper()

def limpar_nome_coluna(nome):
    if not nome: return ""
    return str(nome).replace('\n', ' ').replace('\r', '').strip()


# ==========================================
# PROCESSAMENTO DE DADOS (Tabela Fato)
# ==========================================
def processar_arquivo_excel(caminho_arquivo, ano, arquivo_csv_saida):
    print(f"\n  └─ Lendo: {caminho_arquivo.name} (Ano: {ano})")
    
    wb = load_workbook(caminho_arquivo, read_only=True, data_only=True)
    ws = wb.active
    iter_linhas = ws.iter_rows(values_only=True)
    
    cabecalhos_temporarios = []
    linha_brasil_encontrada = False
    col_base = 0 
    
    ultimo_valor_coluna = {} 
    linhas_cabecalho_brutas = []
    
    id_local_atual = None
    uf_atual = ""

    for linha in iter_linhas:
        idx_primeira = -1
        primeira_col_val = ""
        for i, val in enumerate(linha):
            if val is not None and str(val).strip() != "":
                idx_primeira = i
                primeira_col_val = str(val).strip()
                break
                
        if idx_primeira == -1: 
            continue 

        if primeira_col_val.upper() == "BRASIL":
            col_base = idx_primeira
            linha_brasil_encontrada = True
            
            headers_reais = [r for r in linhas_cabecalho_brutas if len(set(r)) > 2]
            num_colunas = len(linhas_cabecalho_brutas[0]) if linhas_cabecalho_brutas else 0
            
            for col_idx in range(col_base, num_colunas):
                partes_cabecalho = []
                for linha_cab in headers_reais:
                    val = limpar_nome_coluna(linha_cab[col_idx])
                    if val and val != "None" and val not in partes_cabecalho:
                        partes_cabecalho.append(val)
                
                nome_final = "_".join(partes_cabecalho)
                nome_final = nome_final.replace("_Médio_Anos Finais", "_Médio")
                
                # Previne colunas fantasmas geradas por formatação vazia no final da planilha
                if not nome_final or nome_final.strip() == "" or nome_final == "None":
                    break
                    
                if col_idx == col_base and not nome_final:
                    nome_final = "Dependencia_Administrativa"
                cabecalhos_temporarios.append(nome_final)

            # ========================================================
            # MESCLAGEM INTELIGENTE: Consolida Parcial e Integral do EJA
            # ========================================================
            cabecalhos_finais = []
            mapa_mesclagem = {} 
            
            for i, cabecalho in enumerate(cabecalhos_temporarios):
                if 'EJA' in cabecalho and ('_Parcial' in cabecalho or '_Integral' in cabecalho):
                    nome_base = cabecalho.replace('_Parcial', '').replace('_Integral', '')
                    if nome_base not in cabecalhos_finais:
                        cabecalhos_finais.append(nome_base)
                    novo_indice = cabecalhos_finais.index(nome_base)
                    mapa_mesclagem[i] = novo_indice
                else:
                    cabecalhos_finais.append(cabecalho)
                    mapa_mesclagem[i] = len(cabecalhos_finais) - 1

            # Injeta colunas relacionais nas primeiras posições
            cabecalhos_finais[0] = "Dependencia_Administrativa"
            cabecalhos_finais = ["Ano", "id_localidade"] + cabecalhos_finais
            
            uf_atual = ""
            id_local_atual = obter_id_localidade("BRASIL", uf_atual, "País")
            break
            
        else:
            linha_preenchida = []
            ultimo_valor_horizontal = ""
            
            for i, celula in enumerate(linha):
                val_atual = str(celula).strip() if celula is not None else ""
                
                if not val_atual:
                    # Evita o vazamento horizontal em nós folha finais
                    if ultimo_valor_horizontal.upper() not in ["PARCIAL", "INTEGRAL"]:
                        val_atual = ultimo_valor_horizontal
                else:
                    ultimo_valor_horizontal = val_atual
                    
                if val_atual:
                    ultimo_valor_coluna[i] = val_atual
                    linha_preenchida.append(val_atual)
                else:
                    linha_preenchida.append(ultimo_valor_coluna.get(i, ""))
                    
            linhas_cabecalho_brutas.append(linha_preenchida)

    if not linha_brasil_encontrada:
        print(f"    ⚠️ 'BRASIL' não localizado. Arquivo ignorado.")
        wb.close()
        return

    arquivo_existe = arquivo_csv_saida.exists()
    
    with open(arquivo_csv_saida, mode='a', newline='', encoding='utf-8') as f_out:
        escritor_csv = csv.writer(f_out, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        
        if not arquivo_existe:
            escritor_csv.writerow(cabecalhos_finais)

        chunk = []
        total_inserido = 0
        qtd_colunas_mescladas = len(cabecalhos_finais) - 2
        
        for linha in iter_linhas:
            primeira_coluna = str(linha[col_base]).strip() if col_base < len(linha) and linha[col_base] is not None else ""
            
            if not primeira_coluna:
                continue

            primeira_upper = primeira_coluna.upper()
            if primeira_upper.startswith(("FONTE", "NOTA", "O RESULTADO", "OS RESULTADOS")):
                continue

            has_data = False
            for val in linha[col_base+1:]:
                if val is not None and str(val).strip() != "":
                    has_data = True
                    break

            if not has_data:
                nome_limpo = remover_acentos(primeira_coluna)
                
                if nome_limpo == "BRASIL":
                    uf_atual = ""
                    id_local_atual = obter_id_localidade("BRASIL", uf_atual, "País")
                elif nome_limpo in UFS_BRASIL:
                    uf_atual = primeira_coluna
                    id_local_atual = obter_id_localidade(primeira_coluna, uf_atual, "Estado")
                else:
                    id_local_atual = obter_id_localidade(primeira_coluna, uf_atual, "Município")
                continue
            
            # Processa e consolida as colunas mapeadas (somando o EJA se necessário)
            linha_dados_mesclada = [""] * qtd_colunas_mescladas
            
            for c_idx, val in enumerate(linha[col_base:]):
                if c_idx not in mapa_mesclagem:
                    continue
                    
                novo_idx = mapa_mesclagem[c_idx]
                
                if val is not None and str(val).strip() != "" and str(val).strip() != "-":
                    valor_limpo = str(val).replace('\n', ' ').strip()
                    
                    if novo_idx == 0:
                        linha_dados_mesclada[novo_idx] = valor_limpo
                    else:
                        try:
                            num_val = int(valor_limpo)
                            if linha_dados_mesclada[novo_idx] == "":
                                linha_dados_mesclada[novo_idx] = num_val
                            else:
                                linha_dados_mesclada[novo_idx] += num_val
                        except ValueError:
                            linha_dados_mesclada[novo_idx] = valor_limpo
            
            linha_final = [ano, id_local_atual] + linha_dados_mesclada
            
            chunk.append(linha_final)
            total_inserido += 1

            if len(chunk) >= TAMANHO_CHUNK:
                escritor_csv.writerows(chunk)
                print(f"      ├─ {total_inserido} linhas transferidas...")
                chunk = []

        if chunk:
            escritor_csv.writerows(chunk)
            
    wb.close()
    print(f"    └─ [OK] {total_inserido} registros processados.")

# ==========================================
# ORQUESTRAÇÃO
# ==========================================
def orquestrar_processamento():
    if not PASTA_DATA.exists():
        print("Erro: A pasta 'data' não foi encontrada.")
        return

    PASTA_OUTPUT.mkdir(exist_ok=True)
    
    # Restaura a memória do Dicionário para não quebrar os IDs em execuções múltiplas
    carregar_dim_localidade()

    for pasta_titulo in PASTA_DATA.iterdir():
        if pasta_titulo.is_dir():
            print(f"\n{'='*50}")
            print(f"PROCESSANDO CATEGORIA: {pasta_titulo.name}")
            print(f"{'='*50}")
            
            arquivo_csv_saida = PASTA_OUTPUT / f"fct_{pasta_titulo.name}.csv"
            
            # Descobre quais anos já foram salvos neste CSV específico
            anos_ja_processados = obter_anos_processados(arquivo_csv_saida)
            if anos_ja_processados:
                print(f"[*] Anos já existentes na base e que serão pulados: {', '.join(sorted(anos_ja_processados))}")
            
            for pasta_ano in sorted(pasta_titulo.iterdir()):
                if pasta_ano.is_dir():
                    ano = pasta_ano.name
                    
                    # Trava de segurança contra duplicidade
                    if str(ano) in anos_ja_processados:
                        print(f"  └─ [PULADO] Ano {ano} já consta no arquivo Fato.")
                        continue
                        
                    for arquivo in pasta_ano.glob("*.xlsx"):
                        processar_arquivo_excel(arquivo, ano, arquivo_csv_saida)

if __name__ == "__main__":
    orquestrar_processamento()
    exportar_dim_localidade()
