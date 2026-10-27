from .Microdados import extracao_csv, extracao_zip, unir_dicionários,utils

def microdados():
    extracao_zip.unzip_microdados()
    extracao_csv.extrair_pdf_dicionario_dados()
    unir_dicionários.consolidar_dicionario_mestre()
    utils.limpar_arquivos_intermediarios()

if __name__ == "__main__":
    microdados()