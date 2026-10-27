
# CONTEXTO DO PROJETO: PIPELINE DE INGESTÃO E MODELAGEM RELACIONAL DO CENSO INEP (1996 - 2025)


## 1. Visão Geral e Arquitetura do Projeto

Este projeto consiste no desenvolvimento de uma pipeline de engenharia de dados para extrair, unificar, modelar e carregar os dados históricos do Censo Escolar/Superior do INEP.
A arquitetura final do projeto seguirá o fluxo abaixo:
1. Extração (Web Scraping): Captura automatizada dos links e downloads das bases históricas diretas do portal do INEP.
2. Transformação e Unificação: Consolidação e tratamento de dados que sofreram mutações estruturais ao longo das décadas (1996 a 2025).
3. Modelagem Relacional (OLTP Flexível): Design de um banco de dados transacional focado em dados agregados e consolidados.
4. Destino (AWS S3): Armazenamento dos dados finais em um repositório cloud (S3) estruturado, servindo de base de conhecimento para consumo por um Agente de IA.

## 2. Premissas e Diretrizes de Modelagem

* Flexibilização da Terceira Forma Normal (3NF): Devido à natureza puramente estatística e analítica dos dados do INEP, foi tomada a decisão consciente de desnormalizar parcialmente o modelo OLTP. O objetivo é mitigar o excesso de tabelas correlatas e reduzir o custo computacional de múltiplos JOINs complexos nas consultas.
* Foco no Aprendizado Ativo: O propósito central do desenvolvimento é a evolução técnica do desenvolvedor. Soluções mágicas de "copiar e colar" devem ser evitadas. O foco deve ser na explicação dos conceitos, arquitetura e lógica envolvida.

## 3. Diretrizes para a Atuação do Claude


### A. Revisão do Código de Ingestão (Foco Atual)

O processo de extração de links, download e organização inicial dos arquivos já foi concluído. As interações iniciais devem focar na revisão crítica desses scripts sob os seguintes critérios:
* Gargalos de Performance: Identificar pontos de lentidão (I/O bound ou CPU bound) e propor melhorias como concorrência/assincronismo de forma didática.
* Resiliência e Idempotência: Avaliar mecanismos de tratamento de erros, reconexão pós-queda de rede e garantia de que reexecutar o script não duplique dados ou corrompa downloads parciais.
* Legibilidade e Padrões de Mercado: Sugerir refatorações usando boas práticas de mercado (Type Hinting, tratamento de exceções específico, logs bem estruturados).

### B. Metodologia de Resposta (Abordagem Pedagógica)

1. Não entregue o código refatorado de imediato: Explique primeiro onde está o problema/gargalo e o porquê dele existir.
2. Esquematize a lógica: Use pseudocódigos, diagramas ou tópicos para demonstrar a abordagem sugerida antes de codificar.
3. Apresente o código de forma incremental: Mostre a implementação da melhoria em blocos e guie o desenvolvedor para que ele entenda o impacto de cada linha alterada, promovendo um aprendizado real durante o processo.