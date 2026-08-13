# Territórios BE — Documentação de manutenção

Guia técnico do site, do mapa e das bases usadas para montar a **visão Atual** e os cenários GreenField. Objetivo: permitir manutenção futura sem depender de memória institucional.

---

## 1. O que o site faz

Aplicação local de planejamento de cobertura de **polos** (supervisões) e suas **carteiras** (municípios/distritos com lojas).

Permite:

1. Explorar a **visão Atual** (lojas ativas no período SQL).
2. Comparar cenários **GreenField V3/V4** gerados fora do app.
3. Abrir qualquer cenário no **Builder**, editar carteiras, salvar rascunhos e exportar JSON/GeoJSON.

Stack: React + Mapbox GL (frontend), Express + `mssql` + SQLite (API), GeoJSON local (malhas).

---

## 2. Arquitetura de pastas

| Caminho | Papel |
|---|---|
| `web/` | UI React (`App.tsx`, `MapView.tsx`, `api.ts`, estilos) |
| `api/` | Servidor Express, cache Atual, cenários, SQLite de rascunhos |
| `shared/` | Tipos, cores, população, geometria/métricas reutilizáveis |
| `sql/` | Consultas SQL da visão Atual e overlays |
| `geometria_brasil/` | Malha municipal e distrital (GeoJSON) |
| `public/brazil-mask.json` | Máscara do Brasil (esconde o resto do mundo no mapa) |
| `.territorios-data/` | Cache local (Atual, população, regionais, centros, SQLite) |
| `saida_greenfield_v3/` / `saida_greenfield_v4/` | Pastas de cenários solver (descobertas automaticamente) |
| `scripts/` | Geradores de máscara e malha distrital |

Fluxo resumido:

```
SQL Server ──refresh──► .territorios-data/current.json
GreenField pastas ─────► api/scenarios.js ──► /api/scenarios/:id
Browser (App) ◄──────── /api/*  ◄── MapView (Mapbox)
```

---

## 3. Visão Atual — o que o sistema considera “atual”

### 3.1 Como nasce

A visão **Atual** **não** é um cenário GreenField. Ela é um **cache local** gerado sob demanda:

1. Usuário clica em **Atualizar lojas** (topbar).
2. Frontend chama `POST /api/current-cache/refresh`.
3. API executa `refreshCurrentCache()` em `api/currentCache.js` (assíncrono; responde `202`).
4. Resultado gravado em `.territorios-data/current.json`.
5. Lista de cenários passa a incluir o item `id: "current"`, nome `Atual — lojas ativas`.

Se o cache ainda não existir, `GET /api/scenarios/current` retorna 404 (“Cache atual ainda não foi gerado”).

### 3.2 Período e filtros de lojas

Variável de ambiente: `PERIODO_LOJAS` (padrão `202607`).

Consulta: `sql/LOJAS.sql`.

Uma loja entra na visão Atual se **todas** as condições forem verdadeiras:

| Condição | Origem |
|---|---|
| `PERIODO = :periodo` | `DATAWAREHOUSE.dbo.TB_INDICADORES_BE` |
| `QTD_ATIVOS > 0` | mesma tabela (loja ativa no período) |
| Empresa **não** está na lista de exclusão (ou `COD_EMPRESA` nulo) | `DATALAKE.dbo.DL_BRADESCO_EXPRESSO` |
| `TIPO_POSTO IN ('Tradicional','Ilhas')` | mesma tabela |
| Tem latitude/longitude | `TESTE.dbo.TB_COORD_BE_IGOR` |
| Tem `CHAVE_SUPERVISAO` | `MESU.dbo.CONS_DISTRIBUICAO_ENTIDADES` via `COD_AG_LOJA` |

Empresas excluídas no SQL: `'29000','29001','29002','29003','97463','32399','28077','257956'`.

### 3.3 O que define um polo na visão Atual

Dois SQL distintos se cruzam:

1. **`sql/POLOS_ATUAIS.sql`** — coordenadas do polo  
   `TESTE.dbo.TB_COORD_SUP` → `CHAVE_SUPERVISAO`, `LAT`, `LON`.

2. **`sql/HIERARQUIA_ATUAL.sql`** — nome e gerência de área  
   `MESU.dbo.CONS_DISTRIBUICAO_ENTIDADES` → `DESC_GERENCIA_AREA`, `DESC_SUPERVISAO`, `CHAVE_SUPERVISAO`.

Montagem em `refreshCurrentCache()`:

- Polo só entra se tiver `CHAVE_SUPERVISAO` + coordenadas válidas no Brasil (`lat` ∈ [-35.5, 6.5], `lon` ∈ [-75.5, -32]).
- Nome = `DESC_SUPERVISAO` (fallback: o próprio id).
- Área = `DESC_GERENCIA_AREA` em maiúsculas (fallback: `SEM ÁREA`).
- `source: 'current'`.
- Duplicatas de `CHAVE_SUPERVISAO` são ignoradas (primeira ocorrência vence).

### 3.4 Como municípios são atribuídos aos polos atuais

**Não há algoritmo geográfico (não é “município mais próximo do polo”).**

A atribuição é **via lojas**:

```
Loja ativa (LOJAS.sql)
  → CD_MUNIC (código IBGE do município da loja)
  → COD_AG_LOJA → CHAVE_SUPERVISAO (polo da agência)
```

Regras em `api/currentCache.js`:

1. Normaliza `CD_MUNIC` para 7 dígitos IBGE (aceita 6 dígitos via mapa `seis→sete`).
2. Descarta loja sem município válido, sem polo, ou cujo polo **não** está em `POLOS_ATUAIS`.
3. Deduplica por `CHAVE_LOJA` (cada loja conta uma vez).
4. Agrupa por par `poloId:municipalityCode` e soma lojas.
5. Gera uma **unidade** por par:

```text
id:              MUN-{CD_MUN}-{CHAVE_SUPERVISAO}
type:            MUNICIPIO
municipalityCode / Name / uf
poleId:          CHAVE_SUPERVISAO
population:      IBGE_POP (ou 0)
stores:          quantidade de lojas do par
latitude/longitude: sede municipal (COORDENADAS_MUNICIPIOS)
distanceKm:      0   ← na visão Atual o refresh NÃO calcula distância
```

Consequências importantes:

- O **mesmo município pode aparecer em vários polos** se houver lojas daquele município ligadas a supervisões diferentes.
- Município **sem loja ativa** no período **não entra** na carteira Atual (mesmo que exista na malha).
- A carteira Atual é, na prática, o conjunto de municípios onde o polo tem loja ativa — não um polígono administrativo pré-desenhado.
- `territories` no `current.json` sai **vazio**; o mapa pinta a carteira Atual a partir da **malha municipal** + lista de `units`.

### 3.5 Bases SQL usadas no refresh

| Arquivo | Tabela | Uso |
|---|---|---|
| `COORDENADAS_MUNICIPIOS.sql` | `TESTE.dbo.MUNICIPIOS_COORDENADAS` | Sede (lat/lon) por `CODIGO_IBGE` |
| `POPULACAO.sql` | `IBGE.dbo.IBGE_POP` | População por `COD_UN_REG` |
| `LOJAS.sql` | DW + Datalake + coords BE + MESU | Lojas ativas → município → polo |
| `POLOS_ATUAIS.sql` | `TESTE.dbo.TB_COORD_SUP` | Pontos dos polos |
| `HIERARQUIA_ATUAL.sql` | `MESU.dbo.CONS_DISTRIBUICAO_ENTIDADES` | Nome do polo + gerência de área |

Arquivos auxiliares (não entram no `current.json`, mas no mapa):

| Arquivo | Tabela | Endpoint |
|---|---|---|
| `BASE_GR.sql` | `TESTE.dbo.TB_COORD_GR` | `GET /api/regional-offices` |
| `POPULACAO.sql` | `IBGE.dbo.IBGE_POP` | `GET /api/population` |

### 3.6 Artefatos gravados em `.territorios-data/`

| Arquivo | Conteúdo |
|---|---|
| `current.json` | Cenário Atual completo (`summary`, `poles`, `units`) |
| `population.json` | Mapa `CD_MUN → população` (fallback se SQL cair) |
| `regional-offices.json` | Pontos das gerências regionais |
| `municipality-centers.json` | Centros municipais (SQL preferencial; senão malha) |
| `territorios.sqlite` | Rascunhos do Builder |

Escrita atômica: grava `.tmp` e renomeia.

### 3.7 Payload do cenário Atual

```json
{
  "summary": {
    "id": "current",
    "name": "Atual — lojas ativas",
    "kind": "current",
    "version": "PERIODO_202607",
    "poleCount": N,
    "areaCounts": { "GERÊNCIA X": k, "...": "..." },
    "warnings": []
  },
  "poles": [ /* id, name, lat, lon, area, source */ ],
  "units": [ /* MUN-... por polo */ ],
  "territories": { "type": "FeatureCollection", "features": [] },
  "refreshedAt": "ISO-8601"
}
```

---

## 4. Cenários GreenField (V3 / V4)

Descoberta automática em `api/scenarios.js`:

- Pastas: `OUTPUT_DIR` / `OUTPUT_DIR_V4` (padrão `saida_greenfield_v3`, `saida_greenfield_v4`).
- Cenário válido = subpasta com:
  - `resultado_*.xlsx`
  - `carteiras_unidades.geojson`

Leitura do Excel via `utils/read_scenario_workbook.py` (Python).

Diferença conceitual vs Atual:

| | Visão Atual | GreenField |
|---|---|---|
| Origem do polo | `TB_COORD_SUP` + hierarquia MESU | planilha `gerencias_propostas` |
| Origem da carteira | lojas ativas → município | GeoJSON do solver |
| Geometria territorial | malha municipal sob demanda | `carteiras_unidades.geojson` |
| Distância | 0 no cache | vem de `DISTANCIA_KM` no GeoJSON |
| Tipos de unidade | só `MUNICIPIO` | `MUNICIPIO` e `DISTRITO` |

---

## 5. Builder (rascunhos)

- Criado a partir de qualquer cenário com **Abrir no Builder**.
- Persistência: SQLite `drafts` em `.territorios-data/territorios.sqlite` (`api/db.js`).
- Controle de revisão otimista: `PUT` com `revision` errada → `409`.
- Histórico local undo/redo (até 40 snapshots de poles/units).

Operações principais (`web/App.tsx`):

| Ação | Comportamento |
|---|---|
| **Atribuir** | Unidades selecionadas → polo selecionado; funde duplicatas do mesmo município no polo (`mergeDuplicateMunicipalities`) |
| **Retirar** | `poleId = null` nas unidades da carteira selecionadas |
| **Redistribuir** | Entre polos da **mesma** `DESC_GERENCIA_AREA`, escolhe o polo **mais próximo** (haversine) |
| **Raio contíguo** | Municípios dentro do raio + conectados por saltos curtos entre sedes (`contiguousWithinRadius`) — evita “ilhas” |
| Arrastar polo | Só no Builder; atualiza lat/lon e recalcula `distanceKm` das unidades do polo |
| Shift+arraste | Seleção retangular de polígonos no mapa |

---

## 6. Mapa — detalhes para manutenção (`web/MapView.tsx`)

### 6.1 Inicialização

- Mapbox GL, projeção **Mercator** (forçada em todo reload de estilo).
- Centro inicial: `[-52.5, -14.8]`, zoom `2.8`, `minZoom: 1.8`.
- `maxBounds`: aproximadamente América do Sul.
- Estilos:
  - padrão do app: `mapbox://styles/mapbox/standard` (toggle ligado);
  - alternativo: `MAPBOX_STYLE` / `mapbox/light-v11`.
- Token: `GET /api/config` (`MAPBOX_ACCESS_TOKEN` ou token público embutido em `api/config.js`).

### 6.2 Fontes e camadas (ordem conceitual)

| Source | Layers | Função |
|---|---|---|
| `brazil-mask` | `world-mask`, `brazil-outline`, `state-outline`, `world-label-clip` | Máscara cinza fora do Brasil + contornos |
| `municipalities` | `municipality-fill`, `municipality-line`, `portfolio-fill`, `portfolio-outline`, `portfolio-stitch` | Malha IBGE + pintura de carteira / calor |
| `territories` | `territory-fill/outline/stitch` | GeoJSON de cenário (GreenField/draft) |
| `districts` | `district-fill`, `district-line(-halo)` | Malha distrital (≥ 300 mil hab.) |
| `selection-wave` | `selection-wave` | Onda animada no município focado |
| `poles` | `poles-circle` | Marcadores dos polos |
| `radius-circle` | `radius-fill/outline` | Círculo do raio no Builder |
| `regionals` | `regionals-circle`, `regionals-label` | Gerências regionais (`GR`) |

### 6.3 Cores (`shared/mapColors.ts`)

- **Marker do polo** (`markerColor`): cor sólida por `DESC_GERENCIA_AREA`.
- **Carteira / território** (`territoryColor`): cor distinta por polo (paleta + HSL além do limite).
- **Pré-seleção Builder**: `#f59e0b` / stroke `#b45309` (`PENDING_ASSIGN_*`).
- Contorno “costura” = cor escurecida (`shadeColor`) + `line-dasharray`.

### 6.4 Visibilidade da carteira

- Clique em polo → pinta municípios com `unit.poleId === selectedPole`.
- **Mostrar todas as áreas** → pinta todas as carteiras (se gerência filtrada, só dessa área).
- Filtrar só a gerência na lateral **não** pinta carteiras sozinho — só filtra quais polos aparecem.
- Visão Atual: pintura usa malha `/api/geometry/municipalities` casada por código IBGE.
- Sobreposição no `showAll`: primeiro `poleId` estável (sort) ganha a cor do município.

### 6.5 Controles overlay no mapa

Botões flutuantes:

1. Toggle estilo Standard ↔ clássico.
2. Malha distrital (`Brasil_Distritos_Metro.json`).
3. Malha municipal (contornos cinza).

Topbar:

- **Mapa de calor** — 6 faixas de população (`shared/population.ts`).
- **Gerências regionais** — pontos `TB_COORD_GR`.
- **Mostrar todas as áreas** / **Atualizar lojas** / Importar / Builder.

### 6.6 Interações

| Interação | Comportamento |
|---|---|
| Clique no polo | Seleciona polo (no Builder: mouseup sem arraste) |
| Arrastar polo (Builder) | Move ponto; rejeita fora do bbox Brasil |
| Clique no município | Popup (nome, IBGE, pop, lojas, polo); no Builder+malha pré-seleciona para atribuir |
| Clique no distrito | Popup com nome e `POP_2022` (prioridade sobre município) |
| Clique em regional | Popup com nome, agências, endereço |
| Clique na carteira (lista) | Foca município + onda; no modo exploração faz `easeTo` |
| Labels estrangeiros | Clip/hide via máscara Brasil |

### 6.7 Máscara do Brasil

- Arquivo: `public/brazil-mask.json` (gerado por `scripts/generate_brazil_mask.py` a partir de `Brasil_Municipios.json`).
- Feature `kind=mask`: polígono mundial com buracos = Brasil.
- Features `outline` / `states`: contornos país e UFs.
- Ray-casting nos buracos para decidir se ponto está no Brasil (fallback: bbox).

### 6.8 Malha distrital

Geração: `scripts/generate_district_mesh.py`

- Fonte GPKG em `geometria_brasil/distritos_brasil/...`
- Filtra municípios com população ≥ **300.000** (usa `.territorios-data/population.json`)
- Simplifica geometria (~80 m em EPSG:5880) e grava `Brasil_Distritos_Metro.json`
- Servida em `GET /api/geometry/districts`

---

## 7. Métricas de polo (`shared/geo.ts`)

`calculatePoleMetrics(pole, units)`:

- Conta **município único por `CD_MUN`** (soma lojas; população = max).
- Distritos entram separados.
- Distâncias: haversine sede municipal ↔ coordenada do polo (min / média / max).

Helpers úteis na manutenção:

- `normalizeMunicipalityCode` — 6 dígitos vira `xxxxxx0`; senão 7 primeiros.
- `uniqueUnitsByMunicipality` / `pickPreferredUnit` — preferência: já no polo alvo → mais lojas → id estável.
- `contiguousWithinRadius` — raio + contiguidade por vizinho próximo (link dinâmico baseado no P75 das distâncias NN).
- `circlePolygon` — polígono aproximado do círculo geodésico no mapa.

---

## 8. API — rotas relevantes

| Método | Rota | Função |
|---|---|---|
| GET | `/api/health` | Ping |
| GET | `/api/config` | Token/estilo Mapbox |
| GET | `/api/scenarios` | Atual (se cache) + V3/V4 |
| GET | `/api/scenarios/current` | Cache Atual |
| GET | `/api/scenarios/:id` | Cenário GreenField |
| POST | `/api/current-cache/refresh` | Dispara refresh (202) |
| GET | `/api/current-cache/status` | `available`, `refreshing`, `lastError` |
| GET | `/api/sql/health` | Testa SQL (sem senha) |
| GET | `/api/population` | Calor (com fallback stale) |
| GET | `/api/regional-offices` | Regionais (com fallback stale) |
| GET | `/api/geometry/municipalities` | Malha municipal |
| GET | `/api/geometry/districts` | Malha distrital |
| GET | `/api/geometry/municipality-centers` | Centros p/ raio |
| CRUD | `/api/drafts...` | Rascunhos + export JSON/GeoJSON |

---

## 9. Variáveis de ambiente (sem segredos)

Ver `.env.example`. Principais:

| Variável | Uso |
|---|---|
| `APP_HOST` / `WEB_PORT` / `API_HOST` / `API_PORT` | Bind da app |
| `APP_DATA_DIR` | Cache (padrão `.territorios-data`) |
| `OUTPUT_DIR` / `OUTPUT_DIR_V4` | Pastas GreenField |
| `ARQUIVO_MUNICIPIOS_JSON` / `ARQUIVO_DISTRITOS_JSON` | Override das malhas |
| `MAPBOX_ACCESS_TOKEN` / `MAPBOX_STYLE` | Mapa |
| `SQL_*` | Conexão SQL Server (`mssql`) |
| `PERIODO_LOJAS` | Período da visão Atual |
| `EXCLUDED_MUNICIPALITIES_*` | Solver V4 (não afeta a visão Atual do mapa) |

---

## 10. Checklist de manutenção comum

### Atualizar a visão Atual

1. Conferir `PERIODO_LOJAS` no `.env`.
2. Garantir SQL acessível (`GET /api/sql/health`).
3. Clicar **Atualizar lojas** ou `POST /api/current-cache/refresh`.
4. Verificar `.territorios-data/current.json` e status sem `lastError`.

### Polo “sumiu”

- Sem linha em `TB_COORD_SUP`, ou lat/lon inválidos/fora do Brasil.
- Ou hierarquia existe mas coordenadas não — o polo **não** é criado só pela hierarquia.

### Município “sumiu” da carteira Atual

- Não há loja ativa no período naquele município ligada àquela `CHAVE_SUPERVISAO`.
- Ou `CD_MUNIC` não normaliza para um código presente em `MUNICIPIOS_COORDENADAS`.
- Ou o polo da loja não está em `POLOS_ATUAIS`.

### Mesmo município em dois polos

- Esperado na visão Atual: lojas do município amarradas a supervisões distintas.
- No Builder, **Atribuir** + `mergeDuplicateMunicipalities` unifica no polo alvo.

### Mapa sem pintura na visão Atual

- `territories.features` está vazio por design.
- Precisa da malha `geometria_brasil/Brasil_Municipios.json` servida em `/api/geometry/municipalities`.
- Selecionar um polo (ou “Mostrar todas as áreas”).

### Regenerar máscara / distritos

```bash
python scripts/generate_brazil_mask.py
python scripts/generate_district_mesh.py   # exige population.json e o GPKG fonte
```

### Distâncias na visão Atual

- No refresh, `distanceKm` fica `0`.
- Painel lateral recalcula haversine em tempo real com sede municipal × coordenada do polo.
- No Builder, ao mover polo ou atribuir, `distanceKm` é persistido no rascunho.

---

## 11. Modelo mental rápido

```
Visão Atual =
  polos com coordenada em TB_COORD_SUP
  + nome/área em CONS_DISTRIBUICAO_ENTIDADES
  + carteira = municípios onde existem lojas ativas
      do período, tipo Tradicional/Ilhas,
      com agência mapeada para a CHAVE_SUPERVISAO do polo

NÃO é:
  - Voronoi / nearest office
  - malha oficial de “área de supervisão”
  - solver GreenField
```

GreenField = proposta otimizada em pasta de saída.  
Builder = edição manual em cima de qualquer base (Atual ou GreenField).  
Mapa = visualização Mapbox das unidades + malhas locais.

---

## 12. Arquivos-chave para abrir primeiro

1. `api/currentCache.js` — montagem da visão Atual  
2. `sql/LOJAS.sql` + `POLOS_ATUAIS.sql` + `HIERARQUIA_ATUAL.sql` — regras de negócio SQL  
3. `web/MapView.tsx` — camadas e interações do mapa  
4. `web/App.tsx` — UI, Builder, seleção/atribuição  
5. `shared/geo.ts` — métricas, raio contíguo, normalização IBGE  
6. `api/scenarios.js` — leitura GreenField  
7. `api/server.js` — contrato HTTP  

Quando a regra de “quem pertence a qual polo hoje” mudar, o lugar certo é quase sempre **`LOJAS.sql` + o agrupamento em `refreshCurrentCache()`**, não o Mapbox.
