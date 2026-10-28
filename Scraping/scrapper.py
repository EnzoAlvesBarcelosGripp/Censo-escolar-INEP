import json
import os
import time
import logging
from pathlib import Path
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import requests
import sys
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DIRETORIO_ATUAL = Path('C:/dados_inep')

URL_CENSUS = "https://www.gov.br/inep/pt-br/areas-de-atuacao/pesquisas-estatisticas-e-indicadores/censo-escolar/resultados"
URL_INDICATORS = "https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/indicadores-educacionais"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
timeout = (5,30)

retry = Retry(
    total=2,                                  # no máximo 2 tentativas extras (3 total)
    backoff_factor=1,                         # esperas de ~1s, 2s, 4s...
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",),                 # só repete métodos seguros
)



def _extension(url):
    """Extensão real do arquivo (ignora domínio e barra final). Ex.: 'zip', 'pdf', ''."""
    return os.path.splitext(urlparse(url).path)[1].lstrip(".").lower()


def _links_da_pagina(soup):
    """Pega todos os links http dentro do conteúdo principal da página."""
    container = soup.find(id="parent-fieldname-text") or soup
    links = []
    for a in container.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http"):
            links.append({
                "titulo": a.get_text(" ", strip=True),
                "url_download": href,
            })
    return links


def _ideb(session,url, prefix="indicador_ideb"):
    soup = search_soup(session,url)
    base = url.rstrip("/") + "/"

    candidatos = []
    for a in soup.select("div.cover-banner-tile a[href]"):
        candidatos.append(a["href"].strip())
    for a in soup.select("div.card.little-cards a[href]"):
        candidatos.append(a["href"].strip())

    # mantém só os links dentro de /ideb/ (descarta Legislação) e remove duplicados
    urls = []
    for u in candidatos:
        if u.startswith(base) and u not in urls:
            urls.append(u)

    resultado = {}
    for sub_url in urls:
        sub_nome = sub_url.strip("/").split("/")[-1]
        sub_prefix = f"{prefix}_{sub_nome}"
        sub_soup = search_soup(session,sub_url)

        abas = sub_soup.find_all("div", class_="tab-content")
        if abas:
            # páginas com abas (resultados)
            for aba in abas:
                data_url = aba.get("data-url")
                if not data_url:
                    continue
                slug = data_url.strip("/").split("/")[-1]  # ex.: "2005-2025"
                aba_soup = search_soup(session,data_url)
                resultado[f"{sub_prefix}_{slug}"] = _links_da_pagina(aba_soup)
        else:
            # páginas sem abas (outros-documentos)
            resultado[sub_prefix] = _links_da_pagina(sub_soup)

    return resultado


def _ife(session,url, prefix="indicador_indicadores-financeiros-educacionais"):
    soup = search_soup(session, url)
    container = soup.find(id="parent-fieldname-text") or soup
    links = []

    for a in container.find_all("a", class_="external-link", href=True):
        links.append({
            "titulo": a.get_text(" ", strip=True),
            "url_download": a["href"].strip(),
        })

    return {prefix: links}


def _detect_extensions(data):
    extensions = set()
    for links in data.values():
        for item in links:
            url = item.get("url_download")
            if url:
                extension = _extension(url)
                if extension:
                    extensions.add(extension)
    return list(extensions)


def extract_hyperlinks(session,url=URL_INDICATORS):
    soup = search_soup(session, url)
    hyperlinks = []
    
    for card in soup.find_all("div", class_="card little-cards"):
        tag_a = card.find("a", class_="govbr-card-content")
        if tag_a:
            link = tag_a.get("href")
            if link:
                hyperlinks.append(link)
    return hyperlinks


def extract_extension_links(session,url=URL_CENSUS, prefix="censo"):
    soup = search_soup(session,url)
    tabs = {}

    for tab in soup.find_all("div", class_="tab-content"):
        data_id = tab.get("data-id")
        data_url = tab.get("data-url")
        if data_id and data_url and data_id.split('-')[0].isdigit():
            tabs[data_id] = data_url

    valid_links = {}
    for key, value in tabs.items():
        unique_key = f"{prefix}_{key}"
        valid_links[unique_key] = []
        tab_soup = search_soup(session,value)
        elements_a = tab_soup.find_all("a", class_="external-link")
        for tag in elements_a:
            download_link = tag.get("href", "").strip()
            text = tag.get_text().strip()
            file_info = {
                "titulo": text,
                "url_download": download_link,
            }
            valid_links[unique_key].append(file_info)

    return valid_links


def search_soup(session: requests.Session, url: str) -> BeautifulSoup:
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()           # passo 2: 404/500 viram exceção
    except requests.RequestException:         # passo 3: só erros de rede/HTTP
        logger.exception("Falha ao buscar %s", url)
        raise                                 # passo 1: relança, a falha continua visível
    return BeautifulSoup(response.content, "html.parser")


def session()  -> requests.Session:
    session = requests.session()
    session.headers.update(HEADERS)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://",adapter)
    session.mount("https://",adapter)
    return session


def executar_scrapper(session):
    start_time = time.perf_counter()

    all_links = {}

    indicator_links = extract_hyperlinks(session)
    for indicator_url in indicator_links:
        indicator_name = indicator_url.strip("/").split("/")[-1].lower()
        dynamic_prefix = f"indicador_{indicator_name}"

        if indicator_name == "ideb":
            indicator_data = _ideb(session,indicator_url, dynamic_prefix)
        elif indicator_name == "indicadores-financeiros-educacionais":
            indicator_data = _ife(session,indicator_url, dynamic_prefix)
        else:
            indicator_data = extract_extension_links(session,indicator_url, dynamic_prefix)

        if indicator_data:
            all_links.update(indicator_data)

    census_data = extract_extension_links(session)
    if census_data:
        all_links.update(census_data)

    valid_extensions = set(_detect_extensions(all_links))
    filtered_links = {}
    for key, items in all_links.items():
        valid_items = []
        for item in items:
            url = item.get("url_download")
            if not url:
                continue
            item["tipo"] = "arquivo" if _extension(url) in valid_extensions else "pagina_externa"
            valid_items.append(item)
        if valid_items:
            filtered_links[key] = valid_items

    DIRETORIO_ATUAL.mkdir(parents=True, exist_ok=True)

    # salva na mesma pasta do script
    with open(DIRETORIO_ATUAL / "links.json", "w", encoding="utf-8") as file:
        json.dump(filtered_links, file, indent=4, ensure_ascii=False)

    end_time = time.perf_counter()
    print(f"O algoritmo rodou em: {end_time - start_time:.6f} segundos")


def main_scrapper():
    try:
        with session() as sessao:
            executar_scrapper(sessao)
    except requests.RequestException:
        logger.error("Scraping abortado; links.json anterior preservado.")
        sys.exit(1)

if __name__ == "__main__":
    main_scrapper()