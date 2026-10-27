from Scraping.conversor_binario import executar_conversor_binario
from Scraping.separado_inep import executar_separador_censo_inep
from Scraping.resultados_finais_DOU import orquestrar_processamento
from Scraping.scrapper import executar_scrapper
from Scraping.microdados import microdados
import time

def main():
    print("Iniciando scrapper")
    # executar_scrapper()
    print("Scrapper finalizado!")
    print("Iniciando separador...")
    # executar_separador_censo_inep()
    print("Separador finalizado!")
    print("Iniciando transformador...")
    # orquestrar_processamento()
    print("Transformador finalizado!")
    print("Iniciando conversão dos PDF para binário...")
    # executar_conversor_binario()
    print("Conversão finalizada!")
    print("Começar a unificar os dicionários dos microdados")
    microdados()
    print("Microdados unificados!")
    

if __name__  == '__main__':
    inicio = time.time()
    main()
    fim = time.time()
    print(f"Tempo total de execução: {fim - inicio} segundos")
