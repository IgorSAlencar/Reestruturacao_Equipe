# GreenField V5 — arquitetura técnica provisória

> **Rascunho a revisar.** As regras confirmadas estão em
> `GREENFIELD_V5_REGRAS_CONSOLIDADAS.md`. As decisões provisórias incompatíveis
> deste arquivo não devem orientar implementação.

Este documento traduz a especificação funcional da V5 em uma proposta de
implementação. Ele não altera o comportamento dos modelos V3 e V4.

## 1. Direção técnica

A V5 será um construtor heurístico auditável, não um único MIP nacional. O desenho
terá quatro níveis:

1. distribuição nacional das 135 gerências;
2. escolha de polos em problemas regionais menores;
3. construção territorial sobre o grafo de municípios/distritos;
4. reparos locais de fronteira, contiguidade e equilíbrio.

Em todos os níveis, serão preservados separadamente o melhor cenário gerado e o
melhor cenário aprovado, quando este existir.

## 2. Estrutura proposta

```text
greenfield_v5/
  config.py          parâmetros e classificação das regras
  models.py          estruturas de dados e estado do cenário
  data.py            extração, normalização e snapshots
  geography.py       geometrias, distâncias, fronteiras e componentes
  demand.py          lojas, municípios e distritalização condicional
  quotas.py          distribuição das 135 gerências
  poles.py           geração e seleção dos candidatos a polo
  territories.py     construção e reparo das carteiras
  balance.py         balanceamento por movimentos de fronteira
  scenarios.py       Geográfico, Equilibrado e Compromisso
  validation.py      cenário gerado versus cenário aprovado
  outputs.py         SQL, Excel, GeoJSON e relatórios
Estudo_GreenField_V5.py
```

O arquivo principal será apenas um orquestrador. Regras de negócio não ficarão
misturadas com SQL, geometrias, otimização e persistência.

## 3. Componentes existentes

### 3.1 Reaproveitar com pequenas adaptações

Do V3:

- carregamento das consultas SQL;
- preparação das referências municipais;
- preparação das lojas e hierarquia atual;
- carregamento e reconciliação das geometrias;
- cálculo de distâncias geodésicas;
- construção do grafo territorial;
- geração de arquivos Excel e GeoJSON.

Do V4:

- carregamento da lista SQL de exclusões;
- derivação oficial da adjacência entre UFs;
- auditoria independente da solução;
- realocação posterior dos gerentes atuais;
- estrutura dos relatórios de objetivos e exceções.

### 3.2 Reescrever

- construção da demanda, para permitir distritalização condicional;
- geração dos candidatos, removendo o corte absoluto de 30 mil habitantes;
- relação entre GRs e polos;
- escolha das 135 sedes;
- crescimento territorial;
- reparo de unidades inacessíveis;
- balanceamento sem índice composto;
- política de falha e preservação de solução intermediária.

### 3.3 Não levar para a V5

- MIP nacional de atribuição unidade-candidato;
- três otimizações lexicográficas completas no SCIP;
- uma restrição `x <= y` para cada par unidade-candidato;
- shortlist fixa de 24 candidatos como premissa de negócio;
- 81 âncoras exclusivas escolhidas antes da malha;
- bloqueio absoluto de atendimento entre UFs;
- distritalização automática baseada somente em 300 mil habitantes;
- índice único de carga que soma população, lojas, área e dispersão;
- erro que descarta todo o cenário quando apenas uma preferência falha.

## 4. Estruturas centrais

### 4.1 Unidade de demanda

Cada unidade conterá:

- identificador territorial;
- município e, quando aplicável, distrito;
- UF e área organizacional;
- população;
- quantidade de lojas;
- coordenadas;
- vizinhos territoriais;
- obrigatoriedade de atendimento;
- motivo da obrigatoriedade;
- GR de referência para cálculo de quota;
- indicadores de qualidade dos dados.

### 4.2 Candidato a polo

Um município será candidato quando atender ao menos uma condição:

- possuir loja ativa;
- possuir população acima do porte preferencial;
- ser necessário para cobrir uma GR;
- ser necessário para conectar uma componente territorial;
- ser incluído como exceção operacional justificada.

O porte populacional gera preferência, não exclusão.

### 4.3 Estado do cenário

```text
ScenarioState
  selected_poles
  assignment_by_unit
  manager_to_gr
  stores_by_manager
  population_by_manager
  connected_components_by_manager
  objective_metrics
  exceptions
  generated_status
  approved_status
```

Cada operação recebe um cenário gerado e só o substitui quando produz outro estado
que preserve os invariantes de geração. Violações territoriais são tratadas como
pendências de aprovação até serem reparadas.

## 5. Distribuição das 135 gerências

### 5.1 Associação de referência

Cada unidade será associada inicialmente à área organizacional derivada da UF. As
GRs serão utilizadas como pontos de referência e auditoria, não como territórios
oficiais inexistentes na base atual.

### 5.2 Quota inicial

Na linha-base provisória, as 135 gerências serão distribuídas entre as áreas pela
participação de lojas elegíveis:

```text
participacao_area = lojas_elegiveis_area / lojas_elegiveis_total
quota_teorica_area = 135 * participacao_area
```

A parte inteira será atribuída diretamente. As gerências restantes serão
distribuídas pelo método dos maiores restos. Cada área com demanda elegível terá ao
menos uma gerência. Isso fecha exatamente 135 sem solver.

População, distância e dispersão entrarão como critérios de desempate. A fórmula
não criará uma soma escondida de pesos.

Depois da escolha da malha, cada uma das 81 GRs será vinculada ao polo proposto mais
adequado, priorizando mesma área e distância. Exigir ao menos uma gerência exclusiva
por GR continuará como opção configurável até validação de negócio.

### 5.3 Ajustes de viabilidade

Antes da seleção dos polos, quotas poderão ser transferidas entre áreas vizinhas
quando:

- uma área não possuir candidatos suficientes;
- a quantidade de lojas não justificar a quota recebida;
- uma metrópole exigir múltiplos polos;
- a transferência reduzir uma descontinuidade territorial previsível.

Toda transferência será registrada.

## 6. Escolha de polos

### 6.1 Sementes mínimas

Serão escolhidas sedes suficientes para cumprir a quota de cada área. Proximidade
das GRs, cobertura territorial e qualidade operacional participarão da escolha,
sem reservar antecipadamente 81 candidatos exclusivos.

### 6.2 Polos adicionais

Os polos restantes serão incluídos um por vez pelo maior ganho marginal:

1. redução da distância das lojas ainda mal atendidas;
2. cobertura de componentes ou corredores territoriais;
3. redução da concentração de lojas em uma carteira;
4. atendimento de metrópoles com demanda para múltiplas gerências;
5. desempate por população e estabilidade da sede.

Não haverá exigência de provar que a combinação é globalmente ótima.

### 6.3 Troca de sede

Após formar as carteiras, cada polo poderá ser substituído por outro candidato da
própria carteira quando a troca reduzir distância e preservar todas as regras.

## 7. Construção dos territórios

### 7.1 Atribuição obrigatória

As unidades com lojas serão atribuídas primeiro. Depois serão incluídas outras
unidades classificadas como obrigatórias.

### 7.2 Crescimento no grafo

Cada polo crescerá pelo grafo de vizinhança. A prioridade de uma expansão será
avaliada nesta ordem:

1. mesma UF;
2. menor distância ao polo;
3. necessidade de conectar uma unidade obrigatória;
4. menor carga atual da carteira;
5. estabilidade determinística do identificador.

Uma unidade de articulação não será atribuída definitivamente antes de verificar
se sua remoção bloqueia unidades obrigatórias de outra carteira.

### 7.3 Cruzamento de UF

Um cruzamento será permitido apenas quando as UFs forem oficialmente vizinhas e
ao menos uma condição ocorrer:

- o polo da outra UF for materialmente mais próximo;
- o cruzamento eliminar uma descontinuidade;
- não houver alternativa operacional na UF da unidade;
- o cruzamento preservar uma carteira já existente com lojas.

O limiar de "materialmente mais próximo" ficará configurável e auditável.

### 7.4 Municípios opcionais

Municípios sem loja poderão ser:

- incluídos quando completarem ou conectarem um território;
- incluídos quando estiverem próximos do polo;
- deixados sem atendimento quando forem distantes e desnecessários como corredor.

## 8. Reparo territorial

Após a atribuição inicial:

1. detectar carteiras com mais de uma componente;
2. identificar a componente que contém o polo;
3. transferir ilhas para carteiras vizinhas compatíveis;
4. preservar corredores necessários;
5. trocar sementes quando uma unidade obrigatória ficar inacessível;
6. reconstruir localmente somente a região afetada;
7. registrar exceção se nenhum reparo preservar as restrições obrigatórias.

O reparo será limitado por região e número de tentativas. Ele não reiniciará o
processamento nacional completo.

## 9. Balanceamento sem carga composta

### 9.1 Ordem

1. reduzir carteiras fora da faixa de lojas;
2. entre movimentos equivalentes, reduzir desvio de população;
3. minimizar aumento de distância;
4. minimizar dispersão e cruzamentos de UF.

### 9.2 Movimento permitido

Somente unidades de fronteira poderão mudar de carteira. Um movimento será aceito
se:

- a carteira de origem continuar conectada;
- a unidade tocar a carteira de destino;
- nenhuma sede for removida de sua própria carteira;
- nenhuma loja ficar sem atendimento;
- a melhora de carga justificar a variação de distância.

### 9.3 Faixa provisória

O alvo inicial será de 80% a 120% da média nacional de lojas por gerência. A faixa
de população será reportada separadamente e usada como critério secundário.

## 10. Três cenários

### 10.1 Geográfico

- seleção orientada por distância e cobertura;
- crescimento com forte preferência por mesma UF;
- apenas reparos mínimos de carga.

### 10.2 Equilibrado

- inicia no cenário Geográfico;
- executa mais movimentos de fronteira;
- reduz primeiro o maior desvio de lojas;
- respeita um teto configurável de piora de distância.

### 10.3 Compromisso

- inicia no cenário Geográfico;
- corrige carteiras fora de 80%–120%;
- aceita somente movimentos com relação clara entre ganho de carga e custo
  territorial;
- será recomendado quando estiver aprovado.

## 11. Validação

### 11.1 Cenário gerado

Exige:

- 135 polos distintos;
- 135 carteiras não vazias;
- todas as lojas elegíveis atendidas;
- cada polo dentro de sua própria carteira;
- atribuição única por unidade.

### 11.2 Cenário aprovado

Além dos requisitos de geração, exige:

- carteiras contíguas;
- todas as GRs com vínculo organizacional;
- nenhum cruzamento de UF sem justificativa;
- nenhuma violação cadastral crítica;
- nenhuma unidade obrigatória sem atendimento.

Faixa de carga e raios de atendimento permanecerão indicadores de qualidade, não
motivos automáticos de reprovação, até decisão de negócio contrária.

## 12. Política de execução

Cada etapa salvará:

- melhor cenário gerado;
- melhor cenário aprovado, quando existir;
- parâmetros efetivos;
- métricas antes e depois;
- movimentos realizados;
- exceções encontradas;
- tempo consumido.

Uma etapa com limite de tempo retorna o cenário gerado anterior e, separadamente,
o melhor aprovado disponível. Uma etapa sem cenário gerado termina cedo, antes de
iniciar processamento longo.

### 12.1 Separação entre elegibilidade e qualidade cadastral

A extração de lojas será dividida em duas camadas:

1. **Elegibilidade de negócio:** período, atividade, empresa e tipo de posto;
2. **Qualidade cadastral:** município, coordenada e vínculo atual de supervisão.

A ausência de supervisão atual não remove uma loja do GreenField. A ausência de
coordenada própria poderá usar o centro municipal, com indicador de precisão. A
ausência de município continuará sendo uma inconsistência crítica.

Na consulta atual, filtros sobre coordenada e `CHAVE_SUPERVISAO` aparecem no
`WHERE` após `LEFT JOIN`, convertendo esses vínculos em exclusões efetivas. A V5
deverá extrair também os registros incompletos e classificá-los na auditoria.

## 13. Ordem recomendada de implementação

1. snapshots e contratos de dados;
2. demanda municipal e lojas elegíveis;
3. associação provisória unidade–GR e quotas;
4. candidatos e escolha dos 135 polos;
5. cenário Geográfico;
6. reparo de contiguidade;
7. cenário Equilibrado;
8. cenário Compromisso;
9. distritalização condicional;
10. realocação atual versus proposta;
11. saídas e painel comparativo;
12. persistência SQL após aprovação funcional.

## 14. Pontos ainda dependentes de validação de negócio

- definição final de loja elegível;
- obrigação ou não de uma gerência mínima por GR;
- faixa final de lojas por carteira;
- limiar de ganho que permite cruzar UF;
- conceito final de unidade obrigatória sem loja;
- gatilho quantitativo para distritalizar metrópoles;
- teto de piora de distância no cenário Equilibrado;
- critérios formais para recomendar o cenário Compromisso.
