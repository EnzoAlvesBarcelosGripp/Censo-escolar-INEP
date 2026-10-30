import pandas as pd

BASE = "C:/dados_inep/data_cen/dados_processados"

anos = {1996: f"{BASE}/Microdados_1996"}
anos.update({a: f"{BASE}/Microdados_9706/{a}" for a in range(1997, 2007)})

print(f"{'ano':>4}  {'medio (escola_etapa)':>22}  {'tec_matriculas (escola)':>24}")
for ano, pasta in anos.items():
    etapa = pd.read_csv(f"{pasta}/escola_etapa.csv", usecols=["etapa", "matriculas"])
    escola = pd.read_csv(f"{pasta}/escola.csv", usecols=["tec_matriculas"])
    medio = etapa.loc[etapa.etapa == "medio", "matriculas"].sum()
    tec = escola.tec_matriculas.sum()
    print(f"{ano:>4}  {medio:>22,.0f}  {tec:>24,.0f}")