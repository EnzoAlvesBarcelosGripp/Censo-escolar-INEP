"""Funções comuns dos geradores do IDEB (resultados). Sem lógica específica de um arquivo."""
from __future__ import annotations

import csv
import re
import shutil
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

# --------------------------------------------------------------------------- arquivos de entrada
RAIZ_IDEB = Path(r"C:\dados_inep\data_ind\ideb\resultados\2005-2025")


def extrair_xlsx_do_zip(zip_path: Path, nome_base: str, destino: Path) -> Path:
    """Extrai <nome_base>.xlsx do .zip para `destino` (idempotente: não extrai de novo se já está lá)."""
    destino.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        membros = [m for m in z.infolist() if not m.is_dir() and PurePosixPath(m.filename).name == f"{nome_base}.xlsx"]
        if not membros:
            xlsx = [m.filename for m in z.infolist() if m.filename.lower().endswith(".xlsx")]
            raise FileNotFoundError(f"{zip_path}: não tem {nome_base}.xlsx (xlsx dentro do zip: {xlsx})")
        if len({m.CRC for m in membros}) > 1:
            raise ValueError(f"{zip_path}: cópias diferentes de {nome_base}.xlsx: {[m.filename for m in membros]}")
        m = membros[0]
        alvo = destino / f"{nome_base}.xlsx"
        if alvo.is_file() and alvo.stat().st_size == m.file_size:
            return alvo
        parcial = alvo.with_suffix(".parcial")
        with z.open(m) as origem, parcial.open("wb") as saida:
            shutil.copyfileobj(origem, saida)
        parcial.replace(alvo)
    return alvo


def localizar_xlsx(nome_base: str, raiz: Path = RAIZ_IDEB) -> Path:
    r"""Acha <nome_base>.xlsx. Cobre os dois jeitos de existir o '<nome>.zip':
      - arquivo zip de verdade  -> extrai o xlsx para <raiz>\_extraido\<nome>\ (uma vez só) e devolve esse caminho
      - pasta (já extraída)     -> <raiz>\<nome>.zip\<nome>\<nome>.xlsx
    Se nenhum existir, procura <nome>.xlsx em qualquer subpasta; sem achar, levanta erro com o que há na raiz."""
    z = raiz / f"{nome_base}.zip"
    if z.is_file():
        return extrair_xlsx_do_zip(z, nome_base, raiz / "_extraido" / nome_base)
    esperado = z / nome_base / f"{nome_base}.xlsx"
    if esperado.is_file():
        return esperado
    achados = sorted(raiz.rglob(f"{nome_base}.xlsx")) if raiz.is_dir() else []
    if achados:
        if len({a.stat().st_size for a in achados}) > 1:
            raise ValueError(f"várias cópias com tamanhos diferentes: {achados}")
        return achados[0]
    existe = sorted(p.name for p in raiz.iterdir())[:20] if raiz.is_dir() else "(a raiz não existe)"
    raise FileNotFoundError(
        f"{nome_base} não encontrado em {raiz}. Esperado: {z} (arquivo .zip) ou {esperado}. "
        f"Conteúdo da raiz (até 20): {existe}")


# --------------------------------------------------------------------------- domínio
UF_CODIGO: dict[str, int] = {
    "RO": 11, "AC": 12, "AM": 13, "RR": 14, "PA": 15, "AP": 16, "TO": 17,
    "MA": 21, "PI": 22, "CE": 23, "RN": 24, "PB": 25, "PE": 26, "AL": 27, "SE": 28, "BA": 29,
    "MG": 31, "ES": 32, "RJ": 33, "SP": 35,
    "PR": 41, "SC": 42, "RS": 43,
    "MS": 50, "MT": 51, "GO": 52, "DF": 53,
}


@dataclass(frozen=True)
class Familia:
    casas: int       # casas decimais gravadas (<=2: o valor da fonte tem de ser exato nessa precisão)
    minimo: float
    maximo: float


FAMILIAS: dict[str, Familia] = {
    "VL_APROVACAO_SI_4": Familia(1, 0, 100),   # total da etapa (ex.: 6º a 9º ano)
    "VL_APROVACAO_SI": Familia(1, 0, 100),     # só existe em Anos Iniciais
    "VL_APROVACAO_1": Familia(1, 0, 100),
    "VL_APROVACAO_2": Familia(1, 0, 100),
    "VL_APROVACAO_3": Familia(1, 0, 100),
    "VL_APROVACAO_4": Familia(1, 0, 100),
    "VL_INDICADOR_REND": Familia(12, 0, 1),
    "VL_NOTA_MATEMATICA": Familia(2, 0, 500),
    "VL_NOTA_PORTUGUES": Familia(2, 0, 500),
    "VL_NOTA_MEDIA": Familia(12, 0, 10),
    "VL_OBSERVADO": Familia(1, 0, 10),
    "VL_PROJECAO": Familia(1, 0, 10),
}
FAMILIAS_OBS = {"VL_NOTA_MATEMATICA", "VL_NOTA_PORTUGUES"}   # únicas com ND / asterisco
TOKENS_ND = {"ND", "ND*", "ND**", "ND***"}
RE_NUM_TEXTO = re.compile(r"(\d+(?:[.,]\d+)?)(\*+)?")


# --------------------------------------------------------------------------- células
def converter(v: Any, fam: str, ctx: str) -> tuple[str, str]:
    """Devolve (texto para o CSV, observação). Texto vazio = NULL."""
    obs = ""
    if v is None:
        return "", ""
    if isinstance(v, str):
        s = v.strip()
        if s in ("", "-"):
            return "", ""
        if s in TOKENS_ND:
            if fam not in FAMILIAS_OBS:
                raise ValueError(f"{ctx}: {s!r} em {fam}; esperado só nas notas de Matemática/Português")
            return "", s
        m = RE_NUM_TEXTO.fullmatch(s)
        if not m or fam not in FAMILIAS_OBS:
            raise ValueError(f"{ctx}: texto inesperado {s!r} em {fam}")
        x = float(m[1].replace(",", "."))
        obs = m[2] or ""
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        x = float(v)
    else:
        raise ValueError(f"{ctx}: tipo inesperado {type(v).__name__}: {v!r}")
    spec = FAMILIAS[fam]
    if not spec.minimo <= x <= spec.maximo:
        raise ValueError(f"{ctx}: {fam}={x} fora de [{spec.minimo}, {spec.maximo}]")
    r = round(x, spec.casas)
    if spec.casas <= 2:
        if abs(x - r) > 1e-6:
            raise ValueError(f"{ctx}: {fam}={x} tem mais de {spec.casas} casa(s) decimal(is)")
        return f"{r:.{spec.casas}f}", obs
    return f"{r:.12f}".rstrip("0").rstrip("."), obs


def inteiro(v: Any, campo: str, ctx: str, digitos: int | None = None) -> int:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v)
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"{ctx}: {campo} deveria ser inteiro, veio {v!r}")
    if digitos is not None and len(str(v)) != digitos:
        raise ValueError(f"{ctx}: {campo}={v} deveria ter {digitos} dígitos")
    return v


def texto(v: Any, campo: str, ctx: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{ctx}: {campo} vazio ou não-texto: {v!r}")
    return " ".join(v.split())


def normaliza(v: Any) -> str:
    """Rótulo de cabeçalho sem quebras de linha/espaços repetidos."""
    return " ".join(str(v).split()) if v is not None else ""


# --------------------------------------------------------------------------- saída
@contextmanager
def csv_atomico(destino: Path, cabecalho: list[str]) -> Iterator[csv.writer]:
    """Escreve em .parcial e só troca pelo destino se o bloco terminar sem erro."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(".parcial")
    try:
        with parcial.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cabecalho)
            yield w
        parcial.replace(destino)
    except BaseException:
        parcial.unlink(missing_ok=True)
        raise