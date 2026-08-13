# GreenField V5 — regras consolidadas

Status: **especificação funcional autoritativa para a próxima implementação**.

Este documento substitui as decisões provisórias dos rascunhos anteriores. A
matriz de premissas continua válida como diagnóstico histórico do V3/V4.

## 1. Finalidade

Calcular uma malha nacional matematicamente viável com exatamente 135 gerências,
priorizando a distribuição da população e usando a presença de lojas para reforçar
a relevância dos territórios.

O modelo não precisa encontrar ou provar o cenário perfeito. Ele precisa:

- concluir o cálculo;
- entregar uma solução coerente;
- reduzir a probabilidade de falha;
- preservar resultados intermediários;
- explicar lojas não atendidas e vínculos de GR atendidos com ressalvas.

## 2. Regras obrigatórias

### 2.1 Quantidade de gerências

- O cenário deve possuir exatamente 135 gerências.
- A localização atual das gerências não limita a escolha futura.
- Movimentos bruscos entre o desenho atual e o proposto são permitidos.
- A realocação das pessoas ocorre somente depois do cálculo da nova malha.

### 2.2 Exclusões

- Municípios presentes na lista SQL de exclusão não precisam e não devem receber
  atendimento.
- A lista contém aproximadamente 484 municípios no momento.
- O conteúdo da lista é a regra; a quantidade 484 é auditada, mas não fica
  congelada como condição para executar.
- Todos os municípios e distritos que não estiverem nessa lista formam o universo
  obrigatório de atendimento e devem pertencer a exatamente uma carteira.
- A dispensa de cobertura de 100% refere-se ao Brasil completo e às lojas; ela não
  autoriza o descarte livre de municípios fora da lista SQL.

### 2.3 Distritalização

- Todo município com população igual ou superior a 300 mil habitantes será
  substituído pelos seus distritos oficiais antes da escolha dos polos.
- Nesses municípios não será emitida simultaneamente a unidade municipal agregada.
- Os distritos poderão receber polos distintos.
- A relevância populacional e de lojas deverá permitir que grandes metrópoles
  recebam múltiplas gerências sem criar uma quantidade fixa por distância.

### 2.4 Contiguidade

- Cada carteira precisa formar um caminho territorial contínuo a partir do polo.
- Para toda unidade atendida deve existir um caminho de unidades da mesma carteira
  até a unidade-sede do polo.
- Não existe limite máximo obrigatório de quilômetros, municípios ou área por
  carteira.
- A distância participa da qualidade da solução, mas não cria cortes rígidos de
  50, 100 ou 150 km.

### 2.5 Fronteiras de UF

- Cruzamento de UF é permitido somente entre UFs vizinhas.
- A unidade atendida do outro estado precisa estar na fronteira territorial.
- Deve existir vizinhança municipal direta com uma unidade da carteira pertencente
  à UF do polo.
- O modelo não poderá avançar profundamente por outra UF apenas porque cruzou uma
  primeira fronteira.
- Todo cruzamento será registrado na auditoria.

### 2.6 GRs

- As 81 GRs precisam ser vinculadas a polos do cenário.
- O vínculo não é exclusivo nem 1:1.
- Um mesmo polo pode atender duas ou mais GRs próximas.
- Não existe raio máximo obrigatório para esse vínculo.
- A proximidade entre GR e polo participa da qualidade da solução.
- Nenhuma das 81 GRs pode ficar sem polo vinculado no resultado.

## 3. O que não é obrigatório

- Atender 100% das lojas elegíveis.
- Atender 100% dos municípios brasileiros.
- Manter as gerências próximas de suas localizações atuais.
- Ter uma gerência exclusiva para cada GR.
- Manter todas as carteiras com a mesma quantidade de municípios.
- Manter todas as carteiras com a mesma população.
- Manter todas as carteiras com a mesma quantidade de lojas.
- Aplicar faixa rígida de 75%–125% ou 80%–120%.
- Criar novo polo ao ultrapassar uma distância fixa.
- Provar ótimo global ou gap matemático mínimo.

A cobertura e o equilíbrio serão resultados avaliados, não condições que façam o
modelo terminar sem cenário.

## 4. Relevância territorial

### 4.1 Dimensão principal

A população é a principal medida de relevância. Regiões mais populosas devem:

- influenciar mais fortemente a escolha dos polos;
- tender a receber mais gerências;
- ter maior impacto na função de qualidade;
- receber preferência durante a alocação territorial.

### 4.2 Papel das lojas

As lojas são um indicador adicional de dominância. Elas:

- reforçam a relevância de regiões que já concentram população ou operação;
- ajudam a diferenciar municípios de população semelhante;
- influenciam a seleção de polos e o desenho das carteiras;
- não são a principal medida de carga;
- não criam obrigação de cobertura integral.

### 4.3 Índice transparente

Para evitar os vários pesos arbitrários do V3, a V5 terá somente um parâmetro de
ênfase das lojas:

```text
participacao_pop_u   = populacao_u / populacao_total
participacao_lojas_u = lojas_u / lojas_total

relevancia_u = participacao_pop_u
             + ENFASE_LOJAS * participacao_lojas_u
```

Propriedades:

- a população sempre possui coeficiente 1;
- `ENFASE_LOJAS` deve ser menor que 1;
- o parâmetro será exibido nos relatórios;
- nenhuma área, dispersão ou constante fixa será somada à carga;
- área e dispersão permanecem indicadores de qualidade.

O valor inicial fica definido como `ENFASE_LOJAS = 0,25`. A população permanece
dominante e as lojas produzem somente um reforço moderado.

## 5. Unidade de demanda

### 5.1 Municípios abaixo de 300 mil

- Permanecem como unidades municipais.
- Se não estiverem na lista SQL de exclusão, devem ser atribuídos a exatamente um
  polo.
- Municípios com maior relevância têm prioridade.
- Município sem loja continua podendo ser atendido pela população ou por ser
  corredor de contiguidade.

### 5.2 Municípios a partir de 300 mil

- São representados somente por distritos.
- População e lojas são distribuídas entre os distritos.
- Cada distrito participa individualmente da seleção e da atribuição.
- Múltiplos polos dentro da metrópole são permitidos e esperados quando a relevância
  territorial justificar.

## 6. Candidatos a polo

Um município ou distrito poderá ser candidato quando possuir dados territoriais
válidos. População inferior a 30 mil não elimina automaticamente o candidato.

A escolha das 135 sedes priorizará, nesta ordem:

1. garantir pelo menos um polo em cada componente territorial obrigatório;
2. redução da distância ponderada pela relevância populacional;
3. ganho de cobertura de população relevante;
4. reforço produzido pela presença de lojas;
5. proximidade às GRs ainda mal representadas;
6. estabilidade determinística do resultado.

Não existe reserva antecipada de 81 candidatos exclusivos para as GRs.

## 7. Seleção dos 135 polos

A seleção será heurística e nacional, sem MIP nacional completo:

```text
selecionar sementes necessárias para componentes territoriais relevantes

enquanto houver menos de 135 polos:
    avaliar o ganho marginal de cada candidato
    priorizar redução do custo populacional
    usar lojas como reforço secundário
    considerar GRs ainda distantes dos polos selecionados
    selecionar o melhor candidato

preservar a combinação completa de 135 polos
```

O custo principal de atendimento será:

```text
custo_populacional = relevancia_u * distancia_unidade_polo
```

A distância não possui teto; ela apenas diferencia soluções melhores e piores.

### 7.1 Baseline municipal atual: distância X

A comparação com a estrutura atual será feita pelas unidades territoriais atendidas,
e não pelas lojas individualmente:

- município abaixo de 300 mil habitantes será uma observação municipal;
- município a partir de 300 mil habitantes será comparado por distrito;
- cada unidade aparecerá uma única vez no baseline;
- o ponto da unidade será seu centro territorial representativo;
- a distância será geográfica Haversine, em quilômetros.

Para identificar o polo atual de referência de cada unidade:

```text
agrupar as lojas da unidade por supervisor atual
escolher o supervisor com a maior quantidade de lojas

em caso de empate:
    escolher o supervisor cujo polo atual esteja mais próximo da unidade
    persistindo o empate, escolher o menor identificador estável de supervisor

se a unidade não tiver loja ou supervisor atual válido:
    escolher o polo atual geograficamente mais próximo
    registrar METODO_BASELINE = FALLBACK_POLO_ATUAL_MAIS_PROXIMO

DISTANCIA_ATUAL_X_KM = haversine(centro_unidade, polo_atual_referencia)
```

As lojas determinam somente a dominância observada da carteira atual. A quantidade
de lojas não será a população de observações da métrica de distância e não duplicará
municípios atendidos por mais de um supervisor.

O baseline usará o mesmo universo territorial obrigatório do cenário proposto. Nas
metrópoles, as lojas serão associadas espacialmente aos distritos antes de identificar
o supervisor dominante. Unidade sem centro territorial válido continuará sendo erro
de dados do modelo; ausência de vínculo atual válido acionará o fallback descrito.

### 7.2 Distância proposta Y e redução

Depois de formar as novas carteiras:

```text
DISTANCIA_PROPOSTA_Y_KM = haversine(centro_unidade, novo_polo)
REDUCAO_DISTANCIA_KM = DISTANCIA_ATUAL_X_KM - DISTANCIA_PROPOSTA_Y_KM
```

- redução positiva significa melhoria;
- zero significa manutenção;
- redução negativa significa aumento da distância;
- para cada nova carteira, X e Y serão agregados sobre exatamente as mesmas
  unidades territoriais atribuídas a ela no cenário proposto;
- não haverá pareamento artificial entre polos atuais e propostos.

Cada registro territorial do comparativo deverá expor:

- `DEMAND_ID` e tipo da unidade;
- `SUPERVISOR_ATUAL_DOMINANTE`;
- `POLO_ATUAL_REFERENCIA_ID`;
- `METODO_BASELINE` com `SUPERVISOR_DOMINANTE`,
  `DESEMPATE_MENOR_DISTANCIA`, `DESEMPATE_ID_ESTAVEL` ou
  `FALLBACK_POLO_ATUAL_MAIS_PROXIMO`;
- `DISTANCIA_ATUAL_X_KM`;
- `DISTANCIA_PROPOSTA_Y_KM`;
- `REDUCAO_DISTANCIA_KM`;
- `PERC_REDUCAO_DISTANCIA`, nulo quando X for zero;
- `STATUS_DISTANCIA` com `MELHOROU`, `MANTEVE` ou `PIOROU`.

### 7.3 Métrica oficial de raio

O raio oficial será o P90 das distâncias das unidades, ponderado pela população:

```text
RAIO_P90 = percentil_ponderado(distancia_unidade_polo, populacao_unidade, 0,90)
```

A distância máxima será mantida como indicador auditável e critério de desempate,
mas um município extremo não definirá sozinho a função principal. Não será criado
limite máximo obrigatório de quilômetros.

## 8. Construção das carteiras

### 8.1 Voronoi no grafo territorial

As carteiras serão construídas por caminhos mínimos no grafo de vizinhança:

```text
iniciar uma expansão em cada um dos 135 polos

para cada unidade alcançada:
    registrar polo proprietário
    registrar unidade predecessora
    manter o mesmo polo do predecessor
```

O predecessor cria um caminho explícito entre cada unidade e seu polo.

### 8.2 Prioridade da expansão

1. preservar caminho territorial até o polo;
2. respeitar a regra de fronteira de UF;
3. menor custo ponderado pela relevância;
4. maior população alcançada;
5. maior presença de lojas;
6. identificador estável para desempate.

### 8.3 Unidades excluídas e falhas de atendimento

- Unidade presente na lista SQL fica fora do grafo de atendimento e é registrada
  como exclusão deliberada.
- Unidade não excluída sem polo, sem caminho contíguo ou sem componente coberto não
  é ressalva aceitável: é falha de construção a ser reparada.
- Se o reparo não conseguir incorporar todas as unidades obrigatórias, o estado
  parcial será preservado para diagnóstico, mas não será publicado como cenário
  calculado.
- O erro deverá informar a unidade, componente, UF e regra que impediu a atribuição.
- Loja sem atendimento não invalida o cenário.

## 9. Vínculo das GRs

Após construir a malha:

```text
para cada uma das 81 GRs:
    calcular distância até os 135 polos
    vincular ao polo mais adequado
    permitir que o polo já esteja vinculado a outra GR
```

O vínculo considera proximidade e coerência territorial, mas não impõe distância
máxima. A quantidade de GRs por polo será reportada.

## 10. Melhoria local

Depois de obter um cenário completo:

- trocar sedes dentro das próprias carteiras;
- mover unidades de fronteira quando preservar contiguidade;
- reduzir o custo de distância ponderado pela relevância territorial;
- reduzir o P90 nacional ponderado pela população;
- reduzir a distância média ponderada pela população;
- usar a distância máxima como desempate;
- melhorar representação de regiões com lojas;
- reduzir cruzamentos de UF desnecessários;
- melhorar vínculo das GRs;
- nunca exigir igualdade perfeita entre carteiras.

Cada movimento só substitui o resultado anterior quando mantém as regras
obrigatórias e não aumenta o custo de distância ponderado pela relevância. Entre
movimentos admissíveis, será escolhido primeiro o que mais reduzir o P90 populacional,
depois a média populacional e, por último, a distância máxima.

A melhoria será avaliada nacionalmente. Uma carteira individual poderá aumentar sua
distância quando o conjunto nacional melhorar; não haverá obrigação de redução para
cada carteira.

## 11. Saída e status

### 11.1 Cenário calculado

O cenário é considerado `CALCULADO` quando possui:

- exatamente 135 polos;
- 135 carteiras com suas respectivas sedes;
- atribuição única de todas as unidades não excluídas;
- caminho territorial das unidades até os polos;
- todas as 81 GRs vinculadas;
- nenhum município da lista de exclusão atendido;
- cruzamentos de UF dentro da regra de fronteira.

### 11.2 Cenário calculado com ressalvas

O cenário recebe `CALCULADO_COM_RESSALVAS` quando existe, por exemplo:

- loja sem atendimento;
- GR vinculada a polo muito distante;
- dado cadastral incompleto;
- concentração elevada de população em uma carteira;
- P90 nacional proposto igual ou superior ao baseline atual, identificado por
  `SEM_REDUCAO_RAIO_P90`.

Essas condições aparecem no relatório, mas não apagam o resultado.

Um município ou distrito não excluído sem atendimento produz estado `INCOMPLETO`,
e não `CALCULADO_COM_RESSALVAS`.

## 12. Indicadores de qualidade

- população total e percentual atendido;
- população deliberadamente excluída pela lista SQL;
- população obrigatória sem atendimento, que deve ser zero no cenário calculado;
- lojas atendidas e não atendidas;
- municípios e distritos atendidos;
- municípios não atendidos por motivo;
- distância atual X e proposta Y de cada município ou distrito;
- redução absoluta e percentual de distância por unidade;
- P90 atual e proposto, ponderados pela população;
- distância média atual e proposta, ponderadas pela população;
- distância máxima atual e proposta;
- redução absoluta e percentual do P90, da média e da máxima;
- quantidade e população das unidades que melhoraram, mantiveram ou pioraram;
- método de identificação do polo atual de cada unidade;
- métricas X e Y nacionais e por nova carteira, calculadas sobre as mesmas unidades;
- `RAIO_P90_ATUAL_X_KM`, `RAIO_P90_PROPOSTO_Y_KM` e
  `REDUCAO_RAIO_P90_KM`;
- `DISTANCIA_MEDIA_ATUAL_X_KM`, `DISTANCIA_MEDIA_PROPOSTA_Y_KM` e redução;
- `DISTANCIA_MAX_ATUAL_X_KM`, `DISTANCIA_MAX_PROPOSTA_Y_KM` e redução;
- percentual de unidades e de população cobertas pelo baseline comparativo;
- população mínima, média e máxima por carteira;
- lojas mínimas, médias e máximas por carteira;
- quantidade de unidades por carteira;
- cruzamentos de UF;
- quantidade de GRs vinculadas por polo;
- distância de cada GR ao polo vinculado;
- polos e carteiras nas metrópoles distritalizadas;
- diferenças em relação à estrutura atual para transição e para o comparativo X/Y.

Nenhum indicador isolado, exceto uma regra obrigatória, transforma o cenário em
erro de execução.

## 13. Política para reduzir falhas

- Construir uma solução inicial antes de qualquer melhoria longa.
- Salvar o melhor cenário após cada etapa.
- Usar limites curtos por etapa, sem uma única chamada de horas.
- Ao atingir limite, retornar o melhor cenário preservado.
- Separar erro de dados, regra obrigatória e indicador de qualidade.
- Informar IDs e motivos de cada unidade não atendida.
- Evitar `except` que esconda a causa de rejeição da solução.
- Não exigir prova de ótimo global.
- Não executar um solver longo sem cenário inicial recuperável.

## 14. Parâmetros de negócio consolidados

- `ENFASE_LOJAS = 0,25`;
- distância geográfica Haversine;
- raio oficial igual ao P90 ponderado pela população;
- distância máxima apenas como auditoria e desempate;
- melhoria nacional, sem trava individual por carteira;
- baseline municipal pelo supervisor dominante em quantidade de lojas;
- comparação por distrito nas cidades a partir de 300 mil habitantes.
