# Territórios BE

Aplicação local para explorar polos atuais, comparar cenários GreenField V3/V4/V5 e construir simulações manuais de carteira.

## Início rápido

Pré-requisitos: Node.js 22.5–22.x e npm 10.x. A versão usada na máquina
corporativa (Node 22.11.0 / npm 10.9.0) é suportada.

1. Copie as variáveis necessárias de `.env.example` para `.env`. O mapa já possui um token público (`pk.`); `MAPBOX_ACCESS_TOKEN` é apenas uma substituição opcional.
2. Instale exatamente as dependências validadas com `npm ci`.
3. Em um terminal execute `npm run dev:api`. O script habilita automaticamente
   o SQLite experimental exigido pelo Node 22.11.0.
   Esse comando mantém a API ativa sem reinícios automáticos. Para desenvolver
   o backend com recarga ao editar seus arquivos, use `npm run dev:api:watch`.
4. Em outro terminal execute `npm run dev`. Esse comando compila e serve uma
   versão estável, sem o cliente HMR que recarrega a tela dos usuários. Para
   desenvolver o frontend com recarga automática, use `npm run dev:web:watch`.
5. Abra `http://10.206.168.97:5173`. A API responde no mesmo IP em
   `http://10.206.168.97:333` e o frontend encaminha `/api` para esse endereço.

Os endereços são controlados por `APP_HOST`, `WEB_PORT`, `API_HOST` e
`API_PORT`. Os padrões estão preparados para a máquina corporativa
(`10.206.168.97`, portas `5173` e `333`).

A API descobre automaticamente cenários completos nas pastas `saida_greenfield_v3`, `saida_greenfield_v4` e `saida_greenfield_v5`. A visão **Atual** é criada pelo botão **Atualizar lojas**, que executa a consulta SQL existente e grava um cache local em `.territorios-data`.

## Documentação

Os guias técnicos ficam em `docs/`:

| Arquivo | Conteúdo |
|---|---|
| [docs/DOCUMENTACAO_MANUTENCAO.md](docs/DOCUMENTACAO_MANUTENCAO.md) | Manutenção do site, do mapa e das bases |
| [docs/COMO_ADICIONAR_CENARIOS.md](docs/COMO_ADICIONAR_CENARIOS.md) | Como incluir cenários V3/V4/V5 no mapa |
| [docs/GREENFIELD_V5_REGRAS_CONSOLIDADAS.md](docs/GREENFIELD_V5_REGRAS_CONSOLIDADAS.md) | Regras autoritativas da V5 |
| [docs/ESPECIFICACAO_GREENFIELD_V5.md](docs/ESPECIFICACAO_GREENFIELD_V5.md) | Especificação da V5 |
| [docs/ALGORITMO_GREENFIELD_V5.md](docs/ALGORITMO_GREENFIELD_V5.md) | Algoritmo da V5 |
| [docs/ARQUITETURA_GREENFIELD_V5.md](docs/ARQUITETURA_GREENFIELD_V5.md) | Arquitetura da V5 |
| [docs/MATRIZ_PREMISSAS_GREENFIELD.md](docs/MATRIZ_PREMISSAS_GREENFIELD.md) | Premissas V3/V4 → V5 |

## GreenField V5

A V5 usa uma heurística populacional contígua e não depende do SCIP. Para preparar
o ambiente e executar:

```powershell
python -m pip install -r requirements-v5.txt
python Estudo_GreenField_V5.py
```

Parâmetros úteis:

```powershell
python Estudo_GreenField_V5.py --periodo 202607 --sem-sql
python Estudo_GreenField_V5.py --output-dir saida_greenfield_v5
```

A tabela SQL de municípios excluídos deve estar configurada em
`EXCLUDED_MUNICIPALITIES_TABLE`. A saída padrão é criada em
`saida_greenfield_v5/V5_135_<RUN_ID>` e passa a ser descoberta automaticamente
pelo mapa quando contém o Excel final e `carteiras_unidades.geojson`.

A conexão SQL da visão **Atual** usa diretamente o pacote Node `mssql`, com as
variáveis `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, `SQL_PASSWORD`, `SQL_DOMAIN`
e `SQL_INSTANCE`. `SQL_TRUSTED_CONNECTION=true` mantém a conexão confiável
habilitada por padrão, seguindo a configuração corporativa. É possível testar
a conexão em `GET /api/sql/health`; a rota não devolve senha nem credenciais.

Se uma tentativa anterior deixou `node_modules` parcialmente instalado, feche
terminais Node/Vite e o Explorer do VS Code dentro de `node_modules`, remova a
pasta e execute `npm ci`. As versões de React, Vite, Tailwind, TypeScript,
Mapbox, Express e demais pacotes foram alinhadas ao repositório
`mapa-hierarquia-visualiza`, já homologado no Nexus corporativo. A API roda em
JavaScript puro e não depende de `tsx`.

## Builder

Abra qualquer cenário no Builder, arraste polos, selecione unidades territoriais e atribua-as manualmente ou redistribua a seleção entre polos da mesma gerência de área. Rascunhos são persistidos localmente em SQLite com controle de revisão.

## Análise de um cenário do Builder

O script `Analisar_Cenario_Builder.py` compara um JSON exportado pelo Builder
com o cenário **Atual**. Aceita o envelope do rascunho (`id`, `name`, `revision`
e o cenário em `data.poles` / `data.units`) ou um `ScenarioData` bruto. As
distâncias são recalculadas por Haversine; a comparação carteira a carteira só
acontece quando o `pole_id` é o mesmo nos dois cenários.

Na pasta do projeto:

```powershell
python -m pip install -r requirements-v5.txt
python Analisar_Cenario_Builder.py --builder-json caminho\cenario-builder.json
```

Por padrão a comparação usa `.territorios-data/current.json` e grava um Excel e
um Markdown em `analise_builder`. Para escolher a pasta de saída ou outro
cenário atual:

```powershell
python Analisar_Cenario_Builder.py --builder-json caminho\cenario-builder.json --output-dir .\analise_builder
python Analisar_Cenario_Builder.py --builder-json caminho\cenario-builder.json --current-json .\outro-atual.json
```

O terminal imprime os caminhos dos relatórios, por exemplo:

```text
Excel: C:\Users\Igor\Desktop\Reestruturacao_Equipe\analise_builder\analise_cenarios_20260813_234800.xlsx
Markdown: C:\Users\Igor\Desktop\Reestruturacao_Equipe\analise_builder\analise_cenarios_20260813_234800.md
Ocorrências de qualidade: 12
```

O que olhar no resultado:

| Aba / seção | Para quê |
|---|---|
| **Resumo** | Totais nacionais: municípios, correspondentes, população, km médio/P90/máximo |
| **Comparacao_Carteiras** | Delta por polo (mesmo `pole_id`) |
| **Movimentacoes** | Unidades que mudaram de polo |
| **Gerencias_Area** | Impacto por gerência |
| **Insights** | Achados automáticos (pior distância, maior variação, etc.) |
| **Qualidade_Dados** | Erros e avisos (polo sem coordenada, unidade órfã, etc.) |

Se der erro:

- `Cenário atual não encontrado` → no app, use **Atualizar lojas** para gerar `.territorios-data/current.json`
- `A exportação Excel requer openpyxl` → rode de novo o `pip install -r requirements-v5.txt`
- `JSON do Builder não encontrado` → o arquivo precisa estar na pasta de onde você rodou o comando, ou use o caminho completo

O script só analisa; ele não altera o rascunho nem o mapa.

## Readequação automática de cenários

O script `Readequar_Cenario.py` recebe um JSON exportado pelo modelo ou pelo
Builder e redistribui polos e carteiras conforme metas regionais definidas em
outro JSON. Ele não possui quantidades ou nomes de região embutidos no código.

Exemplo de configuração (todas as áreas e UFs do cenário precisam aparecer
exatamente uma vez, e a soma de `targetPoles` deve ser igual ao total nacional):

```json
{
  "regions": [
    {
      "name": "REGIAO A",
      "areas": ["GERENCIA DE AREA A"],
      "ufs": ["AA", "AB"],
      "targetPoles": 20
    },
    {
      "name": "REGIAO B",
      "areas": ["GERENCIA DE AREA B"],
      "ufs": ["BA", "BB"],
      "targetPoles": 18
    }
  ],
  "balanceTolerancePct": 20,
  "maxP90IncreasePct": 10,
  "maxAssignmentPasses": 10,
  "seed": 20260813
}
```

Execução:

```powershell
python Readequar_Cenario.py --input caminho\cenario.json --config caminho\metas.json --output-dir .\resultados_readequacao
```

A pasta de saída recebe os cenários `conservador`, `equilibrado` e
`geografico`, um Excel comparativo e um manifesto da execução. A população é
a medida de carga: cada município fica em um único polo e só pode ser dividido
quando o JSON contiver distritos explícitos. Municípios repetidos são
consolidados usando a maior população e a soma das lojas.

As metas regionais e a vinculação UF/região são obrigatórias. Cruzamentos de
UF dentro da mesma região recebem penalidade, enquanto cruzamentos de região
são proibidos. A faixa populacional de 80%–120% e o limite de piora de 10% no
P90 ponderado são preferências auditadas: quando não forem viáveis, o cenário
ainda é salvo com status `COM_RESSALVAS` e os motivos aparecem no Excel e no
próprio JSON.

O readequador usa somente coordenadas e não possui malha de vizinhança. Assim,
ele otimiza proximidade geográfica, mas não certifica contiguidade territorial.

## Camadas do mapa

O mapa usa projeção Mercator. Cada `DESC_GERENCIA_AREA` recebe uma cor própria;
ao selecionar uma gerência, somente seus polos permanecem visíveis. O clique em
um polo destaca sua carteira municipal com preenchimento translúcido e contorno.

O botão **Mapa de calor** consulta `IBGE.dbo.IBGE_POP`, relacionando
`COD_UN_REG` à malha municipal e classificando `POPULACAO` em seis faixas. A
resposta é armazenada em `.territorios-data/population.json` para permitir a
última visualização disponível durante indisponibilidades temporárias do SQL.
