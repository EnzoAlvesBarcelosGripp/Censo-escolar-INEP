"""
Gera o catálogo de itens de infraestrutura do núcleo (dim.item_infraestrutura) e o de-para de colunas de
origem (dim.item_infraestrutura_origem).

Por que existe: o mesmo conceito tem nomes diferentes em cada bloco ("tem biblioteca" é `biblioteca CHAR(1)`
's'/'n' em 1997-2006 e `in_biblioteca TINYINT` 0/1 em 2007-2025). O catálogo dá UM nome a cada conceito; o
de-para diz de quais colunas ele sai em cada bloco. O montador do núcleo (montar_nucleo_infra.py) lê o
de-para do banco e escreve o SQL sozinho: nenhuma coluna é digitada duas vezes.

Regras (decididas em 04/11):
  1. Item comum a dois blocos só quando o conceito é o mesmo. O que só existe num bloco vira item próprio,
     e os anos em que não foi coletado simplesmente não têm linha no núcleo (nunca zero).
  2. Todas as colunas IN_/QT_ de b0725.infraestrutura e b0725.equipamento entram (as que não estão no de-para
     manual viram item próprio, com a descrição do dicionário). TP_ (códigos, não sim/não) ficam fora.
  3. Existência (flag) e quantidade são itens separados, sem conversão (ex.: `equip_tv` e `qt_equip_tv`).
  4. Mais de uma coluna no mesmo bloco = OU (flags) ou SOMA (quantidades): só agregação, nunca derivação.

Entrada : <raiz>/Microdados_0725/variavel.csv  (só para listar as colunas de 2007-2025 e suas descrições)
Saída   : <raiz>/dim/item_infraestrutura.csv e <raiz>/dim/item_infraestrutura_origem.csv

ATENÇÃO: o id_item é a posição na lista. Item novo entra sempre NO FIM de ITENS_COMUNS / ITENS_SO_ANTIGOS,
e os itens automáticos são numerados depois deles, em ordem alfabética.

Uso:
  python gerar_catalogo_infra.py
  python gerar_catalogo_infra.py --raiz "D:/outro/dados_processados"
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ_PADRAO = Path("C:/dados_inep/data_cen/dados_processados")

# (bloco, tabela) -> valores de sim / não das flags
SIM_NAO = {"9706": ("s", "n"), "0725": ("1", "0")}   # 0725: o 9 não é sim nem não -> conta como vazio


@dataclass(frozen=True)
class Item:
    codigo: str
    tipo: str                                    # "existencia" | "quantidade"
    descricao: str
    origens: dict[str, tuple[str, ...]]          # bloco -> ("tabela.coluna", ...)
    observacao: str = ""


def E(codigo: str, descricao: str, obs: str = "", **origens: str | tuple[str, ...]) -> Item:
    return Item(codigo, "existencia", descricao, _orig(origens), obs)


def Q(codigo: str, descricao: str, obs: str = "", **origens: str | tuple[str, ...]) -> Item:
    return Item(codigo, "quantidade", descricao, _orig(origens), obs)


def _orig(o: dict) -> dict[str, tuple[str, ...]]:
    return {k.lstrip("b"): (v,) if isinstance(v, str) else tuple(v) for k, v in o.items()}


# ---------------------------------------------------------------------------------------------------------
# 1. Itens com equivalente em mais de um bloco (de-para manual). b9706 / b1996 / b0725 = blocos.
#    O nome segue o de 2007-2025 (é o nome atual do INEP), sem o prefixo IN_.
# ---------------------------------------------------------------------------------------------------------
I, EQ, ES = "infraestrutura", "equipamento", "escola"
ITENS_COMUNS: list[Item] = [
    # água, energia, esgoto, lixo
    E("agua_rede_publica", "Abastecimento de água - rede pública",
      b9706=f"{I}.agua_rede_publica", b0725=f"{I}.in_agua_rede_publica"),
    E("agua_poco_cacimba_fonte", "Abastecimento de água - poço artesiano, cacimba/cisterna ou fonte/rio (qualquer um)",
      "1997-2006 já vem como OU no bloco; 2007+ é o OU das três colunas, que também existem como itens próprios",
      b9706=f"{I}.agua_poco_cacimba_fonte",
      b0725=(f"{I}.in_agua_poco_artesiano", f"{I}.in_agua_cacimba", f"{I}.in_agua_fonte_rio")),
    E("agua_inexistente", "Não há abastecimento de água",
      b9706=f"{I}.agua_inexistente", b0725=f"{I}.in_agua_inexistente"),
    E("energia_rede_publica", "Energia elétrica - rede pública",
      b9706=f"{I}.energia_rede_publica", b0725=f"{I}.in_energia_rede_publica"),
    E("energia_gerador", "Energia elétrica - gerador",
      "a revisar: em 2007+ existe também energia_gerador_fossil (item próprio, não somado)",
      b9706=f"{I}.energia_gerador", b0725=f"{I}.in_energia_gerador"),
    E("energia_renovavel", "Energia elétrica - fontes renováveis ou alternativas",
      "1997-2006: solar OU eólica (2004+)", b9706=f"{I}.energia_renovavel", b0725=f"{I}.in_energia_renovavel"),
    E("energia_inexistente", "Não há energia elétrica",
      b9706=f"{I}.energia_inexistente", b0725=f"{I}.in_energia_inexistente"),
    E("esgoto_rede_publica", "Esgoto sanitário - rede pública",
      b9706=f"{I}.esgoto_rede_publica", b0725=f"{I}.in_esgoto_rede_publica"),
    E("esgoto_fossa", "Esgoto sanitário - fossa",
      "a revisar: em 2007+ existem também fossa comum e séptica (itens próprios, não somados)",
      b9706=f"{I}.esgoto_fossa", b0725=f"{I}.in_esgoto_fossa"),
    E("esgoto_inexistente", "Não há esgotamento sanitário",
      b9706=f"{I}.esgoto_inexistente", b0725=f"{I}.in_esgoto_inexistente"),
    E("lixo_servico_coleta", "Destinação do lixo - serviço de coleta", "1997-2006: só 2001+",
      b9706=f"{I}.lixo_coleta", b0725=f"{I}.in_lixo_servico_coleta"),
    # dependências
    E("sala_diretoria", "Sala de diretoria", b9706=f"{I}.diretoria", b0725=f"{I}.in_sala_diretoria"),
    E("secretaria", "Secretaria", b9706=f"{I}.secretaria", b0725=f"{I}.in_secretaria"),
    E("sala_professor", "Sala de professores", "1997-2006: só 1998+",
      b9706=f"{I}.sala_professores", b0725=f"{I}.in_sala_professor"),
    E("biblioteca", "Biblioteca", "2007+: há também biblioteca_sala_leitura (item próprio)",
      b9706=f"{I}.biblioteca", b0725=f"{I}.in_biblioteca"),
    E("sala_leitura", "Sala de leitura", "1997-2006: só 2001+",
      b9706=f"{I}.sala_leitura", b0725=f"{I}.in_sala_leitura"),
    E("laboratorio_informatica", "Laboratório de informática",
      b9706=f"{I}.lab_informatica", b0725=f"{I}.in_laboratorio_informatica"),
    E("laboratorio_ciencias", "Laboratório de ciências",
      b9706=f"{I}.lab_ciencias", b0725=f"{I}.in_laboratorio_ciencias"),
    E("quadra_esportes", "Quadra de esportes (coberta ou descoberta)",
      "1997-2006: QUADRA OU QUAD_DES OU QUAD_COB; 2007+: IN_QUADRA_ESPORTES (coberta e descoberta são itens próprios)",
      b9706=f"{I}.quadra_esportes", b0725=f"{I}.in_quadra_esportes"),
    E("cozinha", "Cozinha", b9706=f"{I}.cozinha", b0725=f"{I}.in_cozinha"),
    E("refeitorio", "Refeitório", b9706=f"{I}.refeitorio", b0725=f"{I}.in_refeitorio"),
    E("parque_infantil", "Parque infantil", b9706=f"{I}.parque_infantil", b0725=f"{I}.in_parque_infantil"),
    E("bercario", "Berçário", b9706=f"{I}.bercario", b0725=f"{I}.in_bercario"),
    E("banheiro_dentro_predio", "Banheiro dentro do prédio",
      b9706=f"{I}.sanitario_dentro_predio", b0725=f"{I}.in_banheiro_dentro_predio"),
    E("banheiro_fora_predio", "Banheiro fora do prédio",
      b9706=f"{I}.sanitario_fora_predio", b0725=f"{I}.in_banheiro_fora_predio"),
    E("banheiro_pne", "Banheiro adequado a alunos com deficiência ou mobilidade reduzida",
      b9706=f"{I}.sanitario_pne", b0725=f"{I}.in_banheiro_pne"),
    E("dependencias_pne", "Dependências e vias adequadas a alunos com deficiência", "1997-2006: só 1998+",
      b9706=f"{I}.dependencias_pne", b0725=f"{I}.in_dependencias_pne"),
    # local de funcionamento (em 1997-2006 está em b9706.escola)
    E("predio_compartilhado", "Prédio compartilhado com outra escola",
      b9706=f"{ES}.predio_compartilhado", b0725=f"{I}.in_predio_compartilhado"),
    E("local_func_predio_escolar", "Local de funcionamento - prédio escolar",
      b9706=f"{ES}.local_predio_escolar", b0725=f"{I}.in_local_func_predio_escolar"),
    E("local_func_templo_igreja", "Local de funcionamento - templo/igreja",
      b9706=f"{ES}.local_templo", b0725=f"{I}.in_local_func_templo_igreja"),
    E("local_func_galpao", "Local de funcionamento - galpão/rancho/paiol/barracão",
      b9706=f"{ES}.local_galpao", b0725=f"{I}.in_local_func_galpao"),
    E("local_func_salas_empresa", "Local de funcionamento - salas de empresa",
      b9706=f"{ES}.local_empresa", b0725=f"{I}.in_local_func_salas_empresa"),
    E("local_func_salas_outra_esc", "Local de funcionamento - salas em outra escola",
      b9706=f"{ES}.local_outra_escola", b0725=f"{I}.in_local_func_salas_outra_esc"),
    E("local_func_casa_professor", "Local de funcionamento - casa do professor",
      b9706=f"{ES}.local_casa_professor", b0725=f"{I}.in_local_func_casa_professor"),
    # internet
    E("internet", "Acesso à internet", "2005-2006: 's' se alguma faixa de velocidade; 'n' se sem conexão",
      b9706=f"{I}.internet", b0725=f"{EQ}.in_internet"),
    # salas de aula (quantidades)
    Q("qt_salas_utilizadas_dentro", "Salas de aula utilizadas dentro do prédio (no estabelecimento)",
      b1996=f"{I}.salas_utilizadas_no_estab", b9706=f"{I}.salas_utilizadas_no_estab",
      b0725=f"{I}.qt_salas_utilizadas_dentro"),
    Q("qt_salas_utilizadas_fora", "Salas de aula utilizadas fora do prédio (fora do estabelecimento)",
      b1996=f"{I}.salas_utilizadas_fora", b9706=f"{I}.salas_utilizadas_fora",
      b0725=f"{I}.qt_salas_utilizadas_fora"),
    # equipamentos (quantidades; a existência de 2007+ é item próprio, sem conversão)
    Q("qt_equip_tv", "Quantidade de aparelhos de televisão", b9706=f"{I}.n_tv", b0725=f"{EQ}.qt_equip_tv"),
    Q("qt_equip_videocassete", "Quantidade de videocassetes",
      b9706=f"{I}.n_videocassete", b0725=f"{EQ}.qt_equip_videocassete"),
    Q("qt_equip_dvd", "Quantidade de aparelhos de DVD", "1997-2006: só 2006",
      b9706=f"{I}.n_dvd", b0725=f"{EQ}.qt_equip_dvd"),
    Q("qt_equip_parabolica", "Quantidade de antenas parabólicas",
      "a revisar: 2005-2006 = soma dos tipos de antena (ANALOGTV + DIGIT_TV + PARA_INT + PARAB_INT)",
      b9706=f"{I}.n_parabolica", b0725=f"{EQ}.qt_equip_parabolica"),
    Q("qt_equip_impressora", "Quantidade de impressoras",
      b9706=f"{I}.n_impressora", b0725=f"{EQ}.qt_equip_impressora"),
    Q("qt_equip_copiadora", "Quantidade de copiadoras", "1997-2006: só 2001+",
      b9706=f"{I}.n_copiadora", b0725=f"{EQ}.qt_equip_copiadora"),
    Q("qt_equip_multimidia", "Quantidade de projetores multimídia (datashow)", "1997-2006: só 2004+",
      b9706=f"{I}.n_datashow", b0725=f"{EQ}.qt_equip_multimidia"),
    Q("qt_computador", "Quantidade de computadores na escola",
      "a revisar: 1997-2006 = soma dos tipos de micro do ano; 2007+ = QT_COMPUTADOR",
      b9706=f"{I}.n_microcomputadores", b0725=f"{EQ}.qt_computador"),
]

# ---------------------------------------------------------------------------------------------------------
# 2. Itens que só existem em 1996 / 1997-2006 (sem par em 2007+)
# ---------------------------------------------------------------------------------------------------------
ITENS_SO_ANTIGOS: list[Item] = [
    Q("qt_salas_existentes_perm", "Salas de aula existentes - permanentes",
      "2007+ tem só qt_salas_existentes (item próprio), sem a divisão permanente/provisória",
      b1996=f"{I}.salas_existentes_perm", b9706=f"{I}.salas_existentes_perm"),
    Q("qt_salas_existentes_prov", "Salas de aula existentes - provisórias",
      b1996=f"{I}.salas_existentes_prov", b9706=f"{I}.salas_existentes_prov"),
    E("rede_local", "Rede local de computadores", "2007+ é TP_REDE_LOCAL (código, fora do catálogo)",
      b9706=f"{I}.rede_local"),
    E("local_func_unidade_internacao", "Local de funcionamento - unidade de internação",
      b9706=f"{ES}.local_unidade_internacao"),
    E("local_func_outra_instituicao", "Local de funcionamento - outra instituição",
      b9706=f"{ES}.local_outra_instituicao"),
    E("ocupacao_propria", "Forma de ocupação do prédio - própria", b9706=f"{ES}.ocupacao_propria"),
    E("ocupacao_alugada", "Forma de ocupação do prédio - alugada", b9706=f"{ES}.ocupacao_alugada"),
    E("ocupacao_cedida", "Forma de ocupação do prédio - cedida", b9706=f"{ES}.ocupacao_cedida"),
]

GRUPOS = [  # primeira regra que casar com o código
    ("agua", r"^agua_"), ("energia", r"^energia_"), ("esgoto", r"^esgoto_"),
    ("lixo", r"lixo"), ("acessibilidade", r"^acessibilidade_|_pne$"),
    ("local_funcionamento", r"^local_func_|^predio_compartilhado$|^ocupacao_"),
    ("salas_de_aula", r"salas_"), ("internet", r"internet|banda_larga"),
    ("computadores", r"comput|comp_|desktop|tablet|rede_local"), ("equipamentos", r"equip"),
    ("dependencias", r""),
]


def grupo(codigo: str) -> str:
    return next(g for g, rx in GRUPOS if re.search(rx, codigo))


def ler_variaveis_0725(raiz: Path) -> list[tuple[str, str, str]]:
    """(tabela, coluna em minúsculas, descrição) das colunas IN_/QT_ de infraestrutura e equipamento."""
    caminho = raiz / "Microdados_0725" / "variavel.csv"
    if not caminho.exists():
        raise SystemExit(f"Não achei {caminho} (é gerado pelo gerar_tabelas_0725.py).")
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        return [(r["tabela"], r["variavel"].lower(), (r["descricao"] or "").strip())
                for r in csv.DictReader(f)
                if r["tabela"] in (I, EQ) and r["variavel"].upper().startswith(("IN_", "QT_"))]


def montar(raiz: Path) -> tuple[list[dict], list[dict]]:
    manuais = ITENS_COMUNS + ITENS_SO_ANTIGOS
    variaveis = ler_variaveis_0725(raiz)
    existentes = {(t, c) for t, c, _ in variaveis}

    # colunas de 2007+ já usadas sozinhas num item manual não viram item próprio;
    # as que entram num OU (ex.: as três da água de poço) viram, porque o item próprio é outro conceito.
    usadas_sozinhas: set[tuple[str, str]] = set()
    for it in manuais:
        for ref in it.origens.get("0725", ()):
            t, c = ref.split(".")
            if (t, c) not in existentes:
                raise SystemExit(f"{it.codigo}: a coluna {ref} não existe no variavel.csv de 2007-2025")
            if len(it.origens["0725"]) == 1:
                usadas_sozinhas.add((t, c))

    codigos = {it.codigo for it in manuais}
    automaticos: list[Item] = []
    for t, c, desc in sorted(variaveis, key=lambda x: x[1]):
        if (t, c) in usadas_sozinhas:
            continue
        codigo = c[3:] if c.startswith("in_") else c          # qt_ fica no nome: quantidade != existência
        if codigo in codigos:
            raise SystemExit(f"código repetido: {codigo} (coluna {t}.{c}); renomeie o item manual")
        codigos.add(codigo)
        tipo = "quantidade" if c.startswith("qt_") else "existencia"
        automaticos.append(Item(codigo, tipo, desc or c, {"0725": (f"{t}.{c}",)}, "só 2007-2025"))

    itens, origens = [], []
    for id_item, it in enumerate(manuais + automaticos, start=1):
        itens.append({"id_item": id_item, "codigo": it.codigo, "descricao": it.descricao,
                      "grupo": grupo(it.codigo), "tipo_valor": it.tipo, "observacao": it.observacao})
        for bloco, refs in it.origens.items():
            sim, nao = SIM_NAO.get(bloco, ("", "")) if it.tipo == "existencia" else ("", "")
            for ref in refs:
                tabela, coluna = ref.split(".")
                origens.append({"id_item": id_item, "bloco": bloco, "tabela": tabela, "coluna": coluna,
                                "valor_sim": sim, "valor_nao": nao})
    return itens, origens


def gravar(linhas: list[dict], caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(linhas)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=RAIZ_PADRAO, help="pasta dados_processados (padrão: %(default)s)")
    args = ap.parse_args()
    itens, origens = montar(args.raiz)
    gravar(itens, args.raiz / "dim" / "item_infraestrutura.csv")
    gravar(origens, args.raiz / "dim" / "item_infraestrutura_origem.csv")
    por_tipo = {t: sum(i["tipo_valor"] == t for i in itens) for t in ("existencia", "quantidade")}
    comuns = len(ITENS_COMUNS)
    print(f"{len(itens)} itens ({por_tipo['existencia']} de existência, {por_tipo['quantidade']} de quantidade): "
          f"{comuns} com par entre blocos, {len(ITENS_SO_ANTIGOS)} só 1996/1997-2006, "
          f"{len(itens) - comuns - len(ITENS_SO_ANTIGOS)} só 2007-2025. {len(origens)} linhas de de-para.")
    print(f"Arquivos em: {(args.raiz / 'dim').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
