# Como adicionar cenários V3 / V4 / V5 no mapa

A visão **Atual** (lojas ativas) é um cache SQL separado.  
Os cenários **GreenField V3, V4 e V5** entram no mapa só por **descoberta automática de pastas** — sem cadastrar nada na interface.

---

## Como o mapa “enxerga” um cenário

```
saida_greenfield_v3/  ─┐
                       ├─► api/scenarios.js ─► GET /api/scenarios
saida_greenfield_v4/  ─┘         │
saida_greenfield_v5/  ───────────┤
                                 ▼
                    select "Cenário" no App (web/App.tsx)
                                 │
                                 ▼
                    GET /api/scenarios/:id  ─► polos + unidades + GeoJSON
                                 │
                                 ▼
                              MapView
```

1. A API lista subpastas em `OUTPUT_DIR` (V3), `OUTPUT_DIR_V4` (V4) e
   `OUTPUT_DIR_V5` (V5).
2. Só entram pastas **completas** (ver checklist abaixo).
3. O frontend chama `/api/scenarios` e monta o `<select>` do topo.
4. Ao escolher um item, carrega `/api/scenarios/:id` e o mapa desenha polos + carteiras.

O `id` do cenário no select é o **nome da subpasta**.

---

## Checklist: pasta válida

Coloque a saída do solver assim:

```text
saida_greenfield_v5/          ← equivalente para V3 e V4 nas respectivas pastas
  └── meu_cenario_2026_08/    ← este nome vira o id no mapa
        ├── resultado_algo.xlsx
        └── carteiras_unidades.geojson
```

| Arquivo | Obrigatório? | Uso no mapa |
|---|---|---|
| `resultado_*.xlsx` | Sim | Polos (`gerencias_propostas`) + metadados (`cenario`) |
| `carteiras_unidades.geojson` | Sim | Polígonos das unidades e vínculo `GERENCIA_ID` |

Sem **os dois**, a pasta é ignorada.

O Excel precisa das abas:

- `cenario` — `MODELO_VERSAO`, `DATA_EXECUCAO`
- `gerencias_propostas` — polos (id, lat/lon, área, etc.)

O GeoJSON precisa, em cada feature, de propriedades como:

- `GERENCIA_ID` (liga a unidade ao polo)
- `DEMAND_ID` (id da unidade)
- `COD_IBGE` / `CD_MUN`, `NM_MUN`, `UF`
- `TIPO_UNIDADE` (`MUNICIPIO` ou `DISTRITO`)
- opcional: `POPULACAO_UNIDADE`, `QTD_LOJAS`, `DISTANCIA_KM`, `CD_DIST`

A V5 valida esse contrato antes de publicar os arquivos. Uma execução que não tenha
os 135 polos, todas as unidades vinculadas, os campos obrigatórios ou todas as
geometrias encerra como erro e não gera uma pasta completa reconhecida pela API.

---

## Onde colocar V3, V4 e V5

| Versão | Pasta padrão | Variável no `.env` |
|---|---|---|
| V3 | `saida_greenfield_v3/` | `OUTPUT_DIR` |
| V4 | `saida_greenfield_v4/` | `OUTPUT_DIR_V4` |
| V5 | `saida_greenfield_v5/` | `OUTPUT_DIR_V5` |

Exemplos no `.env`:

```env
OUTPUT_DIR=saida_greenfield_v3
OUTPUT_DIR_V4=saida_greenfield_v4
OUTPUT_DIR_V5=saida_greenfield_v5
```

Pode apontar para outro caminho absoluto se as saídas do solver ficarem fora do repositório.

### Como a API decide se é V3, V4 ou V5

Em `api/scenarios.js` (`detectKind`):

1. Lê `MODELO_VERSAO` na aba `cenario`.
2. Se o texto contém `V5` **ou** o caminho da pasta contém `v5` → marca `kind: "v5"`.
3. Senão, se contém `V4` ou o caminho contém `v4` → marca `kind: "v4"`.
4. Caso contrário → `kind: "v3"`.

No select aparece algo como: `V5 · V5_135_AB12CD34`.

---

## Passo a passo (novo cenário)

1. Gere a saída do GreenField normalmente.
2. Use uma **subpasta** da pasta da versão. A V5 cria automaticamente
   `saida_greenfield_v5/V5_135_<RUN_ID>`.
3. Na V3/V4, copie para dentro dela:
   - o `resultado_*.xlsx`
   - o `carteiras_unidades.geojson`
   Na V5, o próprio executável grava os dois arquivos diretamente na subpasta.
4. Confirme que a API está rodando (`npm run dev:api`).
5. Atualize a página do mapa (F5).
6. No select **Cenário**, escolha o novo item com o prefixo da versão.

Não precisa reiniciar a API só por copiar arquivos: a listagem é lida do disco a cada request.  
Reinicie a API se mudar `OUTPUT_DIR`, `OUTPUT_DIR_V4` ou `OUTPUT_DIR_V5` no `.env`.

---

## Atual vs GreenField (não misturar)

| | Atual | GreenField V3/V4/V5 |
|---|---|---|
| Origem | Botão **Atualizar lojas** (SQL) | Pastas do solver |
| Arquivo | `.territorios-data/current.json` | `resultado_*.xlsx` + GeoJSON |
| Id no select | `current` | Nome da subpasta |
| Geometria | Malha municipal do app | `carteiras_unidades.geojson` |

Para ver o Atual: use **Atualizar lojas** e selecione **Atual · …**.  
Isso **não** substitui nem remove os cenários GreenField.

---

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| Cenário não aparece no select | Falta `resultado_*.xlsx` ou `carteiras_unidades.geojson` |
| Pasta existe mas é ignorada | Arquivos na raiz da pasta errada (precisa ser **subpasta**) |
| Versão incorreta no select | `MODELO_VERSAO` sem V4/V5 ou cenário na pasta errada |
| Erro ao abrir o cenário | Excel sem abas `cenario` / `gerencias_propostas`, ou GeoJSON inválido |
| Polos sem marcador | Lat/lon vazios em `gerencias_propostas` |
| Carteira vazia no mapa | Features sem `GERENCIA_ID` válido |

Teste rápido na API:

```text
GET /api/scenarios          → deve listar o novo id
GET /api/scenarios/<id>     → deve devolver poles, units e territories
```

---

## Resumo

Para o mapa mostrar um cenário **além do Atual**:

1. Coloque a saída na pasta da versão; a V5 faz isso automaticamente.
2. Garanta `resultado_*.xlsx` + `carteiras_unidades.geojson` na subpasta.
3. Atualize o browser e escolha no select **Cenário**.

Código relevante: `api/config.js` (caminhos), `api/scenarios.js` (descoberta/carga), `api/server.js` (rotas), `web/App.tsx` (select).
