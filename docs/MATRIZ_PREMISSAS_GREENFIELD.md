# Matriz de premissas — V3/V4 para V5

Esta matriz registra o destino de cada premissa relevante encontrada nos modelos
atuais. As decisões da V5 permanecem provisórias até validação de negócio.

Legenda:

- **MANTER:** continua como obrigação.
- **FLEXIBILIZAR:** deixa de bloquear o cenário e passa a preferência ou reparo.
- **DIAGNÓSTICO:** continua sendo calculada, sem interferir na viabilidade.
- **REMOVER:** não fará parte da nova lógica.
- **CONFIRMAR:** depende de decisão explícita de negócio.

## 1. Estrutura organizacional

| Premissa atual | Origem | Efeito atual | Destino V5 | Justificativa |
|---|---|---|---|---|
| Exatamente 135 gerências | V3 `current_manager_reference` e V4 `manager_count` | Cenário inválido com quantidade diferente | **MANTER** | Quantidade organizacional central do estudo |
| Devem existir exatamente 135 polos atuais na entrada | V3 `prepare_current_poles` | Aborta antes do GreenField | **FLEXIBILIZAR** | Inconsistência da estrutura atual não deve impedir desenhar a futura |
| Devem existir exatamente 81 GRs | V3 `prepare_regional_points` | Aborta se a base retornar quantidade diferente | **FLEXIBILIZAR** | A quantidade deve ser auditada; alteração cadastral não deve quebrar o modelo |
| Cada GR exige um polo distinto | V3 `match_regional_anchors`; V4 `build_anchor_pairs` | Reserva antecipadamente 81 dos 135 polos | **REMOVER/CONFIRMAR** | A base fornece pontos de GR, não territórios nem quota mínima comprovada |
| Polo da GR deve estar na mesma área | V3/V4 pareamento de âncoras | Elimina candidatos fora da área | **FLEXIBILIZAR** | Mesma área vira preferência forte e exceção auditada |
| Polo da GR deve estar em até 100 km | V3/V4 `regional_anchor_radius_km` | Pode tornar o pareamento inviável | **DIAGNÓSTICO/CONFIRMAR** | Raio continuará medido; obrigação depende de validação operacional |
| Estrutura atual influencia a malha futura | Realocação e baseline V3/V4 | Mistura desenho GreenField e transição | **REMOVER da seleção** | Estrutura atual será usada somente após o desenho, na realocação |

## 2. Elegibilidade das lojas

| Premissa atual | Origem | Efeito atual | Destino V5 | Justificativa |
|---|---|---|---|---|
| Período informado | `sql/LOJAS.sql` | Seleciona a fotografia operacional | **MANTER** | Define o universo temporal |
| `QTD_ATIVOS > 0` | `sql/LOJAS.sql` | Mantém lojas ativas | **MANTER** | Regra de negócio de atividade |
| Empresa fora da lista desconsiderada | `sql/LOJAS.sql` | Remove empresas específicas | **MANTER/CONFIRMAR LISTA** | Regra de escopo, mas a lista precisa ser governada |
| Tipo `Tradicional` ou `Ilhas` | `sql/LOJAS.sql` | Limita os tipos operacionais | **MANTER/CONFIRMAR TIPOS** | Regra de escopo do estudo |
| Loja precisa de coordenada própria | `sql/LOJAS.sql` | Loja sem coordenada desaparece da demanda | **REMOVER como filtro** | Usar município como fallback e auditar precisão |
| Loja precisa de supervisão atual | `sql/LOJAS.sql` | Loja sem vínculo atual desaparece da demanda | **REMOVER como filtro** | GreenField não pode depender da estrutura que pretende redesenhar |
| Loja precisa de município identificável | V3 `prepare_stores` | Loja sem município é descartada | **MANTER para aprovação** | Sem unidade territorial não há atribuição defensável; deve aparecer na auditoria |
| Cobertura mínima de 95% das lojas | V4 `minimum_store_coverage` | Permite deixar 5% sem atendimento | **ALTERAR para 100%/CONFIRMAR** | Linha-base recomendada cobre todas as lojas elegíveis |

## 3. Unidades territoriais

| Premissa atual | Origem | Efeito atual | Destino V5 | Justificativa |
|---|---|---|---|---|
| Município é unidade básica | V3/V4 demanda híbrida | Representa a maior parte do país | **MANTER** | Granularidade simples e estável |
| Cidade com população ≥300 mil sempre vira distritos | V3/V4 `large_city_threshold` | Aumenta demanda, grafo e restrições antecipadamente | **REMOVER** | Distritalizar somente quando a cidade precisar de múltiplos polos |
| Todo distrito metropolitano é obrigatório | V4 `ATENDIMENTO_OBRIGATORIO` | Amplia a cobertura rígida | **FLEXIBILIZAR** | Obrigatoriedade virá das lojas e do desenho local da metrópole |
| Todo município ≥30 mil é estratégico/obrigatório | V3 `EH_UNIDADE_ESTRATEGICA`; V4 `mandatory_population_min` | Obriga atendimento mesmo sem loja | **REMOVER como obrigação** | População será prioridade, não condição de viabilidade |
| Município <30 mil sem loja pode ficar sem atendimento | V3 política de pequenos; V4 opcionais | Permite exclusão territorial | **MANTER** | Coerente com foco operacional, desde que não seja corredor necessário |
| Município pequeno distante acima de 150 km fica sem atendimento | V3/V4 | Corte fixo de atendimento opcional | **FLEXIBILIZAR** | Raio vira referência; contiguidade e proximidade decidem caso a caso |
| Lista formal de municípios excluídos | V3 arquivo/V4 SQL | Retira municípios da demanda | **MANTER** | Exclusão precisa ser explícita, versionada e auditada |
| A lista deve conter exatamente 484 municípios | V4 `expected_excluded_municipalities` | Aborta se a quantidade mudar | **REMOVER** | Validar conteúdo e versão, não uma contagem congelada |

## 4. Candidatos a polo

| Premissa atual | Origem | Efeito atual | Destino V5 | Justificativa |
|---|---|---|---|---|
| Município-sede precisa ter população ≥30 mil | V3/V4 `candidate_parent_population_min` | Elimina sedes territoriais pequenas | **FLEXIBILIZAR** | Porte vira preferência; exceções podem ser necessárias |
| Cada distrito de metrópole pode ser candidato | V3/V4 | Cria muitos candidatos | **FLEXIBILIZAR** | Criar candidatos distritais somente após gatilho de múltiplos polos |
| Polo selecionado atende sua própria sede | V3 `force_poles`; V4 restrição de sede | Garante coerência da sede | **MANTER** | Regra estrutural simples e explicável |
| Até 24 candidatos por unidade | V4 `max_candidates_per_unit` | Reduz o MIP, podendo excluir combinação viável | **REMOVER como regra** | Nova construção regional não precisa desse corte fixo |
| Preferência por cidade mais populosa | Penalidade de sede V3 | Influencia escolha de polo | **MANTER como desempate** | Qualidade de sede sem eliminar opções |

## 5. Fronteiras e contiguidade

| Premissa atual | Origem | Efeito atual | Destino V5 | Justificativa |
|---|---|---|---|---|
| Carteira deve ser contígua | V3 validação; V4 cortes | Pode eliminar resultado ou tornar MIP pesado | **MANTER como aceitação** | Construir/reparar fora do MIP nacional |
| Atendimento entre UFs não vizinhas é proibido | V3 matriz proibida; V4 pares elegíveis | Remove pares antes da solução | **MANTER** | Evita territórios geograficamente incoerentes |
| Cruzamento entre UFs vizinhas é permitido apenas na fronteira local | V3 regra por vizinho da unidade | Pode bloquear corredores e deixar unidade inacessível | **FLEXIBILIZAR** | Avaliar fronteira oficial, distância e contiguidade conjuntamente |
| Cruzamento de UF recebe penalidade fixa de 40 km | V3 `cross_uf_equivalent_km_penalty` | Mistura preferência com distância | **REMOVER peso fixo** | Usar ordem de preferência e registrar exceção sem constante arbitrária |
| Unidade-ponte pode ser tomada pelo primeiro polo do crescimento | V3 heap guloso | Pode isolar unidade obrigatória | **REMOVER comportamento** | Proteger articulações e reparar sementes localmente |
| Contiguidade só é verificada após uma otimização completa | V4 corte posterior | Pode consumir todo o limite antes do primeiro reparo | **REMOVER comportamento** | Construir solução contígua desde o início e reparar incrementalmente |

## 6. Carga e equilíbrio

| Premissa atual | Origem | Efeito atual | Destino V5 | Justificativa |
|---|---|---|---|---|
| Carga soma população, lojas, área, dispersão e constante fixa | V3 `enrich_demand_units_with_score` | Produz índice dependente de pesos arbitrários | **REMOVER** | Avaliar dimensões separadamente |
| Carga aceitável entre 75% e 125% | V3 configuração | Orienta movimentos e diagnósticos | **ALTERAR para 80%–120%/CONFIRMAR** | Linha-base provisória mais clara |
| Equilíbrio perfeito é primeiro objetivo | V4 lexicográfico | Sacrifica geografia e dificulta primeira solução | **REMOVER** | Balancear depois da construção geográfica |
| Lojas e população têm desvios avaliados separadamente | V4 | Evita índice único | **MANTER parcialmente** | Lojas serão dimensão principal; população, secundária |
| Área territorial representa carga | V3 índice composto | Penaliza regiões extensas mesmo sem operação proporcional | **DIAGNÓSTICO** | Área será medida, não convertida automaticamente em trabalho |
| Dispersão representa carga por peso fixo | V3 índice composto | Introduz constante subjetiva | **DIAGNÓSTICO/DESEMPATE** | Usar somente entre alternativas equivalentes |
| Mínimo de dois distritos por polo metropolitano | V3 regra metropolitana | Pode conflitar com âncoras e granularidade | **REMOVER** | Nova distritalização será guiada pela demanda real |

## 7. Distância e objetivos

| Premissa atual | Origem | Efeito atual | Destino V5 | Justificativa |
|---|---|---|---|---|
| Faixas populacionais definem raios de 50/100 km | V3 | Aplica penalidade crescente | **DIAGNÓSTICO** | Manter métricas sem impedir solução |
| Distritos têm raio fixo de 50 km | V3 | Penaliza atendimento metropolitano | **DIAGNÓSTICO/CONFIRMAR** | Operação metropolitana pode exigir regra própria |
| Opcional só pode ser atendido em até 150 km | V4 | Remove pares acima do raio | **FLEXIBILIZAR** | Unidade opcional distante pode servir de corredor |
| Primeiro equilíbrio, depois distância por população, depois por lojas | V4 lexicográfico | Exige três otimizações pesadas | **REMOVER** | Produzir cenários distintos em vez de um único ranking rígido |
| Distância operacional é objetivo principal do cenário geográfico | Não explícita nos atuais | — | **ADICIONAR** | Gera desenho territorial natural e explicável |
| Piora máxima de distância no cenário equilibrado | Não existe | — | **ADICIONAR/CONFIRMAR LIMIAR** | Impede equilíbrio artificial com deslocamentos excessivos |

## 8. Solver e política de execução

| Premissa atual | Origem | Efeito atual | Destino V5 | Justificativa |
|---|---|---|---|---|
| É necessário provar ótimo nacional | Intenção V4 | Horas sem incumbente | **REMOVER** | Melhor solução viável e auditável é suficiente |
| Limite global de 7200 segundos | V4 | Primeira etapa recebe 3240 segundos | **REMOVER como padrão** | Limites serão curtos e por etapa/região |
| Gap de 0,5% | V4 | Irrelevante antes de primeira solução | **REMOVER** | Não haverá MIP nacional principal |
| Uma thread configurada | V4 | Root LP nacional permanece lento | **NÃO APLICÁVEL** | Arquitetura regional reduz o problema antes de paralelizar |
| Exceção em preferência cancela o cenário | V3/V4 | Nenhum resultado após horas | **REMOVER** | Separar cenário gerado de cenário aprovado |
| Melhor solução intermediária pode não existir | V4 warm start opcional | Timeout retorna erro | **REMOVER comportamento** | Cada etapa começa de cenário gerado e preservado |
| Persistência ocorre somente no fim | V3/V4 | Perde contexto da falha | **ALTERAR** | Salvar snapshots, movimentos e melhor estado por etapa |

## 9. Decisões novas propostas

| Nova regra | Classificação provisória |
|---|---|
| Produzir cenários Geográfico, Equilibrado e Compromisso | Regra funcional |
| Recomendar Compromisso quando aprovado | Regra funcional |
| Distribuir 135 gerências por área usando participação de lojas | Método transparente provisório |
| Vincular GRs aos polos após a escolha da malha | Preferência organizacional |
| Balancear primeiro quantidade de lojas | Objetivo principal de carga |
| Usar população como segundo eixo | Objetivo secundário |
| Distritalizar somente cidades com demanda para múltiplas gerências | Regra adaptativa |
| Preservar separadamente o melhor cenário gerado e o melhor aprovado | Regra técnica obrigatória |
| Retornar cenário gerado mesmo quando não aprovado | Regra funcional obrigatória |

## 10. Itens que exigem resposta do negócio

1. Cobertura deve ser 100% das lojas elegíveis ou pode permanecer em 95%?
2. Cada GR precisa obrigatoriamente de uma gerência exclusiva?
3. O raio de 100 km da GR é obrigação ou indicador?
4. A faixa de lojas por carteira deve ser 80%–120%?
5. Quais tipos de posto e empresas compõem definitivamente o universo?
6. Qual ganho mínimo justifica cruzar uma fronteira de UF?
7. Quando uma cidade deve receber mais de um polo?
8. Qual piora máxima de distância é aceitável para melhorar equilíbrio?
9. Municípios sem loja e acima de determinado porte são obrigatórios?
10. Uma exceção de contiguidade reprova o cenário ou apenas exige justificativa?
