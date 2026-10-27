import os
import shutil
import zipfile
try:
    from .utils import DIRETORIO_MICRODADOS
except ImportError:
    from utils import DIRETORIO_MICRODADOS

def unzip_microdados():
    for arquivo_microdados in DIRETORIO_MICRODADOS.rglob("*.zip"):
        with zipfile.ZipFile(arquivo_microdados, 'r') as zip_ref:
            for nome_do_arquivo in zip_ref.namelist():
                if nome_do_arquivo.lower().endswith(('.csv', '.xlsx', '.xls', '.xlsm')):
                    nome = os.path.basename(nome_do_arquivo)
                    if nome:
                        destino_path = arquivo_microdados.parent / nome
                        
                        # Verificação se o arquivo já foi extraído
                        if destino_path.exists():
                            print(f'[PULADO] Já existe: {nome}')
                            continue
                            
                        with zip_ref.open(nome_do_arquivo) as origem, open(destino_path, "wb") as destino:
                            print(f'Arquivo {nome_do_arquivo} extraído')
                            shutil.copyfileobj(origem, destino)