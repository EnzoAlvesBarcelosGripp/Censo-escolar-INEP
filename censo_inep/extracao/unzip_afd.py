from pathlib import Path
import shutil
import zipfile
import re

RAIZ = Path(r'C:\dados_inep\data_ind\adequacao-da-formacao-docente')
DESTINO = Path(r'C:\dados_inep\data_ind\adequacao-da-formacao-docente')

def find_zips(raiz: Path) -> list[Path]:
    """Procurar arquivos BRASIL_REGIOES_UF.zip recursivamente na raiz"""
    lista = sorted(raiz.rglob('*BRASIL_REGIOES_UF.zip'))
    return lista
    
def unzip(zip_path: Path, destino_raiz: Path) -> Path:
    ano = re.search(r"_(\d{4})_", zip_path.name).group(1)                      # regex com 4 dígitos entre "_"
    nome_xlsx = f"AFD_BRASIL_REGIOES_UFS_{ano}.xlsx"
    membro = f"AFD_{ano}_BRASIL_REGIOES_UF/{nome_xlsx}"
    destino = destino_raiz / ano / nome_xlsx

    if destino.exists():
        return destino             # idempotência

    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")                  # destino com sufixo .parcial

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(membro) as origem, open(parcial, "wb") as saida:
            shutil.copyfileobj(origem, saida)                         # copiar
    parcial.rename(destino)
    return destino

if __name__ == "__main__":
    for z in find_zips(RAIZ):
        print(unzip(z, DESTINO))