import os
from pathlib import Path


# Diretórios
PASTA_DATA_CEN = Path('C:/dados_inep/data_cen')
PASTA_DATA_IND = Path('C:/dados_inep/data_ind')

def executar_conversor_binario():
    convertor_pdf_bin()
    
def convertor_pdf_bin():
    
    for arquivo_pdf in PASTA_DATA_CEN.rglob('*.pdf'):
        arquivo_bin = arquivo_pdf.with_suffix('.bin')
        if arquivo_bin.exists():
            print(f'Pulado (já existe): {arquivo_bin.name}')
            continue
        arquivo_bin.write_bytes(arquivo_pdf.read_bytes())
        arquivo_pdf.unlink() 
        print(f'Processado: {arquivo_pdf.name} -> {arquivo_bin.name}')
    
    for arquivo_pdf in PASTA_DATA_IND.rglob('*.pdf'):
        arquivo_bin = arquivo_pdf.with_suffix('.bin')
        if arquivo_bin.exists():
            print(f'Pulado (já existe): {arquivo_bin.name}')
            continue
        arquivo_bin.write_bytes(arquivo_pdf.read_bytes())
        arquivo_pdf.unlink() 
        print(f'Processado: {arquivo_pdf.name} -> {arquivo_bin.name}')   

if __name__ == "__main__":
    executar_conversor_binario()