import argparse
import json
import logging
import os
import re
import ssl
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

DIRETORIO_ATUAL = Path('C:/dados_inep')

# Pastas de destino
PASTA_CENSO = "data_cen"
PASTA_INDICADORES = "data_ind"

# Número de downloads simultâneos (sobrescrevível por --workers ou INEP_WORKERS)
WORKERS_PADRAO = 3
MAX_TENTATIVAS = 3
TIMEOUT_SEGUNDOS = 30
TAMANHO_BLOCO = 1024 * 256  # 256 KB: menos overhead que 8 KB em arquivos grandes

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

if hasattr(ssl, 'OP_LEGACY_SERVER_CONNECT'):
    ssl_context.options |= ssl.OP_LEGACY_SERVER_CONNECT

try:
    ssl_context.set_ciphers('DEFAULT@SECLEVEL=1')
except ssl.SSLError:
    pass

# Força TLS 1.2 (o servidor do INEP falha no handshake com TLS 1.3)
ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2

# Sinaliza às threads que devem parar (Ctrl+C)
parar = threading.Event()

# Chaves do links.json:
#   censo_2003
#   indicador_adequacao-da-formacao-docente_2025
#   indicador_taxas-de-transicao_2021-2022
#   indicador_ideb_resultados_2005-2025
#   indicador_ideb_outros-documentos                (sem período)
#   indicador_indicadores-financeiros-educacionais  (sem período)
RE_CENSO = re.compile(r'^censo_(\d{4})$')
RE_INDICADOR = re.compile(r'^indicador_(?P<nome>.+?)(?:_(?P<periodo>\d{4}(?:-\d{4})?))?$')


# ----------------------------------------------------------------------------
# Tipos
# ----------------------------------------------------------------------------
class Tarefa(NamedTuple):
    """Descreve UM download: o que baixar, para onde, e como rotular no log."""
    url: str
    caminho_destino: Path
    rotulo: str


class Status(Enum):
    BAIXADO = "baixado"
    PULADO = "pulado"
    FALHOU = "falhou"
    CANCELADO = "cancelado"


class Resultado(NamedTuple):
    tarefa: Tarefa
    status: Status
    bytes_baixados: int = 0
    erro: str | None = None


# ----------------------------------------------------------------------------
# Utilitários
# ----------------------------------------------------------------------------
def interpretar_chave(chave: str):
    """
    Devolve (categoria, nome, periodo) a partir da chave do JSON,
    ou None se a chave não for reconhecida.
    """
    m = RE_CENSO.match(chave)
    if m:
        return ("censo", None, m.group(1))

    m = RE_INDICADOR.match(chave)
    if m:
        return ("indicador", m.group("nome"), m.group("periodo"))

    return None


def padronizar_titulo(titulo: str) -> str:
    """Padroniza os nomes usando Regex."""
    titulo = re.sub(r'\s+', ' ', titulo)
    titulo_lower = titulo.lower()

    if "covid-19" in titulo_lower:
        match = re.search(r'(.*covid-19)', titulo, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()

    if "dou anexo i" in titulo_lower and "ii" not in titulo_lower:
        return "Resultados Finais - DOU Anexo I"
    if "dou anexo ii" in titulo_lower:
        return "Resultados Finais - DOU Anexo II"

    if "sinopse estatística da educação básica" in titulo_lower and "parte" in titulo_lower:
        return "Sinopse Estatística da Educação Básica"

    if "infográfico" in titulo_lower:
        return titulo.strip()

    if "resultados preliminares do censo escolar" in titulo_lower and "extrato simplificado" in titulo_lower:
        return "Resultados Preliminares - Extrato Simplificado"

    novo_titulo = titulo
    novo_titulo = re.sub(r'[\|\_]?\s*Censo Escolar\s*\d{4}', '', novo_titulo, flags=re.IGNORECASE)
    novo_titulo = re.sub(r'\s+\d{4}\s*$', '', novo_titulo)
    novo_titulo = re.sub(r'^Resultados finais', 'Resultados Finais', novo_titulo, flags=re.IGNORECASE)
    novo_titulo = re.sub(r'^Resumo técnico', 'Resumo Técnico', novo_titulo, flags=re.IGNORECASE)
    novo_titulo = re.sub(r'^Notas estatísticas', 'Notas Estatísticas', novo_titulo, flags=re.IGNORECASE)
    novo_titulo = re.sub(r'[\s_\-\|]+$', '', novo_titulo)

    return novo_titulo.strip()


def sanitizar_nome(nome: str) -> str:
    """Remove caracteres inválidos, exceto a barra (/) para criação de subpastas."""
    caracteres_invalidos = ['<', '>', ':', '"', '\\', '|', '?', '*']
    for char in caracteres_invalidos:
        nome = nome.replace(char, '-')
    return nome.strip()


def formatar_tamanho(bytes_tamanho: float) -> float:
    """Converte bytes para Megabytes (MB)."""
    return bytes_tamanho / (1024 * 1024)


def nome_do_arquivo(url: str) -> str:
    return url.split('/')[-1].split('?')[0]


def carregar_dados(caminho_json: Path) -> dict:
    with open(caminho_json, 'r', encoding='utf-8') as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# FASE 1: PLANEJAMENTO (sem rede, sem escrita em disco)
# ----------------------------------------------------------------------------
def _tarefas_indicador(chave: str, links: list[dict], info: tuple) -> list[Tarefa]:
    """data_ind/<indicador>/<período>/arquivo  (indicador com '_' vira subpasta)."""
    _, nome, periodo = info
    pasta_indicador = sanitizar_nome(nome.replace("_", "/"))

    pasta_destino = DIRETORIO_ATUAL / PASTA_INDICADORES / pasta_indicador
    if periodo:
        pasta_destino = pasta_destino / periodo

    resultado: list[Tarefa] = []
    for item in links:
        if item.get("tipo") != "arquivo":
            continue
        url = item.get("url_download")
        if not url:
            continue
        resultado.append(Tarefa(url, pasta_destino / nome_do_arquivo(url), periodo or nome))
    return resultado


def _tarefas_censo(chave: str, links: list[dict], info: tuple) -> list[Tarefa]:
    """data_cen/<título padronizado>/<ano>/arquivo."""
    ano = info[2]
    resultado: list[Tarefa] = []

    for item in links:
        if item.get("tipo") != "arquivo":
            continue
        url = item.get("url_download")
        titulo_original = item.get("titulo")
        if not url or not titulo_original:
            continue

        titulo_padronizado = padronizar_titulo(titulo_original)
        titulo_pasta = sanitizar_nome(titulo_padronizado)
        pasta_destino = DIRETORIO_ATUAL / PASTA_CENSO / titulo_pasta / ano

        # Caso especial: sinopses de 2003/2004 vêm em partes (Parte 1, 2, 3)
        if ano in ("2003", "2004") and "Sinopse Estatística da Educação Básica" in titulo_padronizado:
            match_parte = re.search(r'Parte\s*(\d+)', titulo_original, flags=re.IGNORECASE)
            if match_parte:
                pasta_destino = pasta_destino / f"{ano}_{match_parte.group(1)}"

        resultado.append(Tarefa(url, pasta_destino / nome_do_arquivo(url), ano))
    return resultado


def _deduplicar(tarefas: list[Tarefa]) -> list[Tarefa]:
    """Mantém a primeira tarefa de cada caminho_destino (evita duas threads no mesmo arquivo)."""
    vistas: dict[Path, Tarefa] = {}
    unicas: list[Tarefa] = []

    for t in tarefas:
        anterior = vistas.get(t.caminho_destino)
        if anterior is None:
            vistas[t.caminho_destino] = t
            unicas.append(t)
        elif anterior.url != t.url:
            logger.warning(
                "Conflito de destino %s: mantida %s, descartada %s",
                t.caminho_destino, anterior.url, t.url,
            )

    logger.info("Planejamento: %d tarefas, %d duplicatas descartadas",
                len(unicas), len(tarefas) - len(unicas))
    return unicas


def planejar_downloads(dados: dict) -> list[Tarefa]:
    tarefas: list[Tarefa] = []

    for chave, links in dados.items():
        info = interpretar_chave(chave)
        if info is None:
            continue

        if info[0] == "censo":
            tarefas.extend(_tarefas_censo(chave, links, info))
        else:
            tarefas.extend(_tarefas_indicador(chave, links, info))

    return _deduplicar(tarefas)


def contar_arquivos_no_json(dados: dict) -> int:
    return sum(
        1
        for links in dados.values()
        for item in links
        if item.get("tipo") == "arquivo" and item.get("url_download")
    )


# ----------------------------------------------------------------------------
# FASE 2: EXECUÇÃO
# ----------------------------------------------------------------------------
def _renomear_com_retentativa(origem: Path, destino: Path) -> None:
    """Evita o WinError 32 (antivírus/indexador segurando o arquivo recém-fechado)."""
    for _ in range(5):
        try:
            origem.replace(destino)
            return
        except PermissionError:
            time.sleep(0.8)
    raise PermissionError(f"Não foi possível renomear {origem.name} para {destino.name}")


def baixar_arquivo(tarefa: Tarefa, max_tentativas: int = MAX_TENTATIVAS) -> Resultado:
    """
    Baixa um arquivo para .part, valida o tamanho e renomeia.
    Tenta até max_tentativas vezes (espera crescente) e SEMPRE devolve um Resultado.
    """
    destino = tarefa.caminho_destino
    nome = destino.name

    if destino.exists():
        return Resultado(tarefa, Status.PULADO)

    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(nome + ".part")
    ultimo_erro = "desconhecido"

    for tentativa in range(1, max_tentativas + 1):
        if parar.is_set():
            return Resultado(tarefa, Status.CANCELADO)

        try:
            req = urllib.request.Request(tarefa.url, headers={'User-Agent': 'Mozilla/5.0'})
            baixado = 0
            with urllib.request.urlopen(req, context=ssl_context, timeout=TIMEOUT_SEGUNDOS) as resp, \
                    open(parcial, 'wb') as saida:
                tamanho_total = int(resp.info().get('Content-Length', -1))

                while True:
                    if parar.is_set():
                        raise InterruptedError("cancelado pelo usuário")
                    bloco = resp.read(TAMANHO_BLOCO)
                    if not bloco:
                        break
                    saida.write(bloco)
                    baixado += len(bloco)

            # conexão pode fechar cedo sem erro: valida contra o Content-Length
            if tamanho_total > 0 and baixado != tamanho_total:
                raise IOError(f"download incompleto: {baixado} de {tamanho_total} bytes")

            time.sleep(0.5)  # deixa o antivírus liberar o arquivo
            _renomear_com_retentativa(parcial, destino)
            return Resultado(tarefa, Status.BAIXADO, baixado)

        except InterruptedError:
            _remover_parcial(parcial)
            return Resultado(tarefa, Status.CANCELADO)

        except Exception as e:  # noqa: BLE001 - qualquer falha de rede/disco vira retentativa
            ultimo_erro = f"{type(e).__name__}: {e}"
            _remover_parcial(parcial)
            if tentativa < max_tentativas:
                espera = 2 * tentativa
                logger.warning("Tentativa %d/%d falhou em %s (%s). Nova tentativa em %ds",
                               tentativa, max_tentativas, nome, ultimo_erro, espera)
                time.sleep(espera)

    return Resultado(tarefa, Status.FALHOU, erro=ultimo_erro)


def _remover_parcial(parcial: Path) -> None:
    time.sleep(0.3)
    try:
        if parcial.exists():
            parcial.unlink()
    except OSError:
        pass


def executar_downloads(tarefas: list[Tarefa], max_workers: int) -> list[Resultado]:
    """Baixa as tarefas em paralelo e devolve todos os resultados."""
    total = len(tarefas)
    resultados: list[Resultado] = []
    inicio = time.perf_counter()
    logger.info("Iniciando %d downloads com %d workers", total, max_workers)

    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dl")
    futuros = [executor.submit(baixar_arquivo, t) for t in tarefas]

    try:
        for i, futuro in enumerate(as_completed(futuros), start=1):
            r = futuro.result()
            resultados.append(r)
            nome = r.tarefa.caminho_destino.name
            if r.status is Status.BAIXADO:
                logger.info("[%d/%d] BAIXADO  %s (%.1f MB) [%s]",
                            i, total, nome, formatar_tamanho(r.bytes_baixados), r.tarefa.rotulo)
            elif r.status is Status.PULADO:
                logger.info("[%d/%d] PULADO   %s (já existe)", i, total, nome)
            elif r.status is Status.FALHOU:
                logger.error("[%d/%d] FALHOU   %s: %s", i, total, nome, r.erro)
            else:
                logger.warning("[%d/%d] CANCELADO %s", i, total, nome)
    except KeyboardInterrupt:
        logger.warning("Ctrl+C recebido: cancelando downloads pendentes...")
        parar.set()
        executor.shutdown(wait=True, cancel_futures=True)
        for f in futuros:
            if f.done() and not f.cancelled():
                r = f.result()
                if r not in resultados:
                    resultados.append(r)
    else:
        executor.shutdown(wait=True)

    logger.info("Tempo total de download: %.1f s", time.perf_counter() - inicio)
    return resultados


def resumir(resultados: list[Resultado], total_planejado: int) -> bool:
    """Loga o resumo final. Devolve True se tudo deu certo."""
    por_status = {s: [r for r in resultados if r.status is s] for s in Status}
    baixados = por_status[Status.BAIXADO]
    mb = formatar_tamanho(sum(r.bytes_baixados for r in baixados))
    nao_processados = total_planejado - len(resultados)

    logger.info("=" * 60)
    logger.info("RESUMO: %d baixados (%.1f MB) | %d pulados | %d falhas | %d cancelados/não iniciados",
                len(baixados), mb, len(por_status[Status.PULADO]),
                len(por_status[Status.FALHOU]),
                len(por_status[Status.CANCELADO]) + nao_processados)

    falhas = por_status[Status.FALHOU]
    if falhas:
        logger.error("Arquivos que falharam (rode novamente para tentar só esses):")
        for r in falhas:
            logger.error("  - %s\n      %s", r.tarefa.url, r.erro)

    return not falhas and not por_status[Status.CANCELADO] and nao_processados == 0


def baixar_todos(caminho_json: Path, max_workers: int) -> bool:
    dados = carregar_dados(caminho_json)

    chaves_desconhecidas = [c for c in dados if interpretar_chave(c) is None]
    if chaves_desconhecidas:
        logger.warning("Chaves ignoradas (formato não reconhecido): %s", chaves_desconhecidas)

    tarefas = planejar_downloads(dados)
    logger.info("Arquivos no JSON: %d | Tarefas planejadas: %d",
                contar_arquivos_no_json(dados), len(tarefas))

    resultados = executar_downloads(tarefas, max_workers)
    return resumir(resultados, len(tarefas))


def executar_separador_censo_inep(caminho_json: Path | None = None,
                                  max_workers: int | None = None) -> bool:
    """Ponto de entrada para o orquestrador. Devolve True se tudo foi baixado."""
    if caminho_json is None:
        caminho_json = DIRETORIO_ATUAL / "links.json"
    if max_workers is None:
        max_workers = int(os.environ.get("INEP_WORKERS", WORKERS_PADRAO))

    if not caminho_json.exists():
        logger.error("JSON não encontrado em: %s", caminho_json)
        return False

    return baixar_todos(caminho_json, max_workers)


def validar_planejamento(caminho_json: Path | None = None) -> None:
    """Roda só a Fase 1 (nada é baixado) e imprime as contagens."""
    caminho_json = caminho_json or DIRETORIO_ATUAL / "links.json"
    dados = carregar_dados(caminho_json)
    tarefas = planejar_downloads(dados)
    print(f"Arquivos no JSON : {contar_arquivos_no_json(dados)}")
    print(f"Tarefas (únicas) : {len(tarefas)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")

    parser = argparse.ArgumentParser(description="Baixa os arquivos do links.json do INEP")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"downloads simultâneos (padrão: INEP_WORKERS ou {WORKERS_PADRAO})")
    parser.add_argument("--validar", action="store_true",
                        help="apenas planeja e mostra as contagens, sem baixar")
    args = parser.parse_args()

    if args.validar:
        validar_planejamento()
        sys.exit(0)

    ok = executar_separador_censo_inep(max_workers=args.workers)
    sys.exit(0 if ok else 1)