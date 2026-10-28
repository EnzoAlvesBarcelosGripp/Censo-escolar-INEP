import logging
import shutil
import zipfile
from pathlib import Path

try:
    from .utils import DIRETORIO_MICRODADOS
except ImportError:
    from utils import DIRETORIO_MICRODADOS

logger = logging.getLogger(__name__)
EXTENSOES_DADOS = ('.csv', '.xlsx', '.xls', '.xlsm')


def _extrair_membro(zf: zipfile.ZipFile, info: zipfile.ZipInfo, destino: Path) -> None:
    """Extrai para .part e só renomeia se terminou (e o CRC bateu)."""
    parcial = destino.with_name(destino.name + ".part")
    try:
        with zf.open(info) as origem, open(parcial, "wb") as saida:
            shutil.copyfileobj(origem, saida)
        parcial.replace(destino)
    except BaseException:
        parcial.unlink(missing_ok=True)
        raise


def unzip_microdados(diretorio: Path | None = None) -> list[str]:
    """Extrai os arquivos de dados dos zips. Devolve a lista de falhas (vazia = tudo OK)."""
    raiz = Path(diretorio) if diretorio else DIRETORIO_MICRODADOS
    falhas: list[str] = []

    for arquivo_zip in sorted(raiz.rglob("*.zip")):
        try:
            zf = zipfile.ZipFile(arquivo_zip, "r")
        except zipfile.BadZipFile as e:
            logger.error("Zip corrompido, ignorado: %s (%s)", arquivo_zip, e)
            falhas.append(f"{arquivo_zip}: zip corrompido")
            continue

        with zf:
            vistos: set[str] = set()
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(EXTENSOES_DADOS):
                    continue

                nome = Path(info.filename).name
                if nome in vistos:
                    logger.error("Colisão de nome em %s: '%s' (%s) não extraído",
                                 arquivo_zip.name, nome, info.filename)
                    falhas.append(f"{arquivo_zip.name}: colisão em {info.filename}")
                    continue
                vistos.add(nome)

                destino = arquivo_zip.parent / nome
                if destino.exists() and destino.stat().st_size == info.file_size:
                    logger.info("[PULADO] %s", nome)
                    continue
                if destino.exists():
                    logger.warning("%s existe com tamanho diferente (%d != %d); reextraindo",
                                   nome, destino.stat().st_size, info.file_size)

                try:
                    _extrair_membro(zf, info, destino)
                    logger.info("Extraído: %s", info.filename)
                except (zipfile.BadZipFile, OSError) as e:
                    logger.error("Falha ao extrair %s de %s: %s", info.filename, arquivo_zip.name, e)
                    falhas.append(f"{arquivo_zip.name}: {info.filename}: {e}")

    return falhas