"""Funções comuns às famílias de indicadores (xlsx do INEP -> CSV)."""
from __future__ import annotations
import openpyxl
import csv
import shutil
import unicodedata
import zipfile
from pathlib import Path

# códigos IBGE (UF: 2 dígitos; região: 1 dígito)
UFS = {
    "rondonia": 11, "acre": 12, "amazonas": 13, "roraima": 14, "para": 15, "amapa": 16,
    "tocantins": 17, "maranhao": 21, "piaui": 22, "ceara": 23, "rio grande do norte": 24,
    "paraiba": 25, "pernambuco": 26, "alagoas": 27, "sergipe": 28, "bahia": 29,
    "minas gerais": 31, "espirito santo": 32, "rio de janeiro": 33, "sao paulo": 35,
    "parana": 41, "santa catarina": 42, "rio grande do sul": 43,
    "mato grosso do sul": 50, "mato grosso": 51, "goias": 52, "distrito federal": 53,
}
REGIOES = {"norte": 1, "nordeste": 2, "sudeste": 3, "sul": 4, "centro-oeste": 5}
NULOS = {"", "-", "–", "—", "--"}


def linhas_dados(xlsx: Path, ano: int, esperado: tuple[str, ...]):
    """Gera as linhas de dados do xlsx do INEP (tuplas completas, sem rodapé).
    Para se: aba != 1, âncora ausente, cabeçalho diferente do esperado, ano divergente,
    dados depois do rodapé ou arquivo sem dados."""
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    try:
        if len(wb.sheetnames) != 1:
            raise ValueError(f"{xlsx.name}: esperada 1 aba, achei {wb.sheetnames}")
        linhas = wb[wb.sheetnames[0]].iter_rows(values_only=True)
        cab = None
        for row in linhas:
            if row and row[0] == "NU_ANO_CENSO":
                cab = tuple(row)
                break
        if cab is None:
            raise ValueError(f"{xlsx.name}: âncora NU_ANO_CENSO não encontrada")
        while cab and cab[-1] is None:
            cab = cab[:-1]
        if cab != esperado:
            dif = sorted(map(str, set(cab) ^ set(esperado)))
            raise ValueError(f"{xlsx.name}: cabeçalho diferente do esperado ({len(cab)} colunas).\n"
                             f"  encontrado: {cab}\n  diferenças: {dif}")
        n, fim = 0, False
        for row in linhas:
            ini = row[0] if row else None
            eh_ano = isinstance(ini, (int, float)) and not isinstance(ini, bool)
            if not eh_ano:
                if n:
                    fim = True          # rodapé começou
                continue
            if fim:
                raise ValueError(f"{xlsx.name}: linha de dados depois do rodapé")
            if int(ini) != ano:
                raise ValueError(f"{xlsx.name}: linha com ano {ini}, esperado {ano}")
            n += 1
            yield tuple(row) + (None,) * (len(esperado) - len(row))
        if n == 0:
            raise ValueError(f"{xlsx.name}: nenhuma linha de dados")
    finally:
        wb.close()

def normalizar(txt: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def classificar(unidade: str) -> tuple[str, int | None]:
    """(nivel, co_uf). Para se o nome não for reconhecido."""
    n = normalizar(unidade)
    if n == "brasil":
        return "Brasil", None
    if n in REGIOES:
        return "Região", REGIOES[n]
    if n in UFS:
        return "UF", UFS[n]
    raise ValueError(f"unidade geográfica não reconhecida: {unidade!r}")


def numero(v: object, ctx: str) -> str:
    """Célula -> texto para o CSV ('' = NULL). Percentual 0..100 com 1 casa decimal. Para se algo for estranho."""
    if v is None or (isinstance(v, str) and v.strip() in NULOS):
        return ""
    if isinstance(v, str):
        try:
            v = float(v.strip().replace(",", "."))
        except ValueError:
            raise ValueError(f"valor não numérico {v!r} em {ctx}") from None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"tipo inesperado {type(v).__name__} em {ctx}")
    if not 0 <= v <= 100:
        raise ValueError(f"percentual fora de 0..100 ({v}) em {ctx}")
    if abs(v - round(v, 1)) > 1e-9:
        raise ValueError(f"mais de 1 casa decimal ({v}) em {ctx}")
    return f"{v:.1f}"


def texto(v: object, campo: str, ctx: str) -> str:
    t = "" if v is None else str(v).strip()
    if not t:
        raise ValueError(f"{campo} vazio em {ctx}")
    return t


def extrair_xlsx(zip_path: Path, pasta: str) -> Path:
    """Extrai o único .xlsx do zip para <pasta do zip>/<pasta>/. Idempotente, com .parcial."""
    with zipfile.ZipFile(zip_path) as zf:
        membros = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".xlsx")]
        if len(membros) != 1:
            raise ValueError(f"{zip_path.name}: esperado 1 xlsx, achei {len(membros)}")
        info = membros[0]
        destino = zip_path.parent / pasta / Path(info.filename).name
        if destino.exists():
            return destino
        destino.parent.mkdir(exist_ok=True)
        parcial = destino.with_name(destino.name + ".parcial")
        with zf.open(info) as o, open(parcial, "wb") as s:
            shutil.copyfileobj(o, s)
        parcial.replace(destino)
    return destino

def inteiro(v: object, campo: str, ctx: str, minimo: int, maximo: int) -> int:
    """Código numérico (CO_ENTIDADE, CO_MUNICIPIO...). Para se não for inteiro ou sair da faixa."""
    try:
        n = int(v.strip() if isinstance(v, str) else v)
    except (TypeError, ValueError):
        raise ValueError(f"{campo} inválido {v!r} em {ctx}") from None
    if isinstance(v, float) and v != n:
        raise ValueError(f"{campo} não inteiro {v!r} em {ctx}")
    if not minimo <= n <= maximo:
        raise ValueError(f"{campo} fora da faixa ({n}) em {ctx}")
    return n


def gravar_csv(destino: Path, cabecalho: list[str], linhas: list[list]) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".parcial")
    with open(parcial, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cabecalho)
        w.writerows(linhas)
    parcial.replace(destino)  # só aqui o CSV "existe"

    