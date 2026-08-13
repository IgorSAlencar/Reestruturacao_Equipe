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

Valor inicial recomendado para avaliação: `ENFASE_LOJAS = 0,25`. Esse valor ainda
precisa de validação de negócio; não é uma restrição estrutural.

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
- reduzir distância ponderada pela população;
- melhorar representação de regiões com lojas;
- reduzir cruzamentos de UF desnecessários;
- melhorar vínculo das GRs;
- nunca exigir igualdade perfeita entre carteiras.

Cada movimento só substitui o resultado anterior quando mantém as regras
obrigatórias.

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
- concentração elevada de população em uma carteira.

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
- distância média, P90 e máxima;
- distância ponderada pela população;
- distância ponderada pelas lojas;
- população mínima, média e máxima por carteira;
- lojas mínimas, médias e máximas por carteira;
- quantidade de unidades por carteira;
- cruzamentos de UF;
- quantidade de GRs vinculadas por polo;
- distância de cada GR ao polo vinculado;
- polos e carteiras nas metrópoles distritalizadas;
- diferenças em relação à estrutura atual, somente para transição.

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

## 14. Único parâmetro de negócio ainda pendente

Definir `ENFASE_LOJAS`, entre 0 e 1. A recomendação inicial é 0,25:

- `0`: somente população influencia a relevância;
- `0,25`: população principal com reforço moderado das lojas;
- `0,50`: lojas possuem influência secundária forte;
- `1`: participação populacional e participação de lojas têm o mesmo peso.
