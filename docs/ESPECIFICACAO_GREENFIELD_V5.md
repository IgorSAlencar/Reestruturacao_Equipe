# GreenField V5 — especificação funcional provisória

> **Rascunho superado.** As regras confirmadas estão em
> `GREENFIELD_V5_REGRAS_CONSOLIDADAS.md`. Este arquivo permanece apenas como
> histórico da primeira proposta.

## 1. Objetivo

Construir cenários utilizáveis para distribuir exatamente 135 gerências, cobrindo
as lojas elegíveis com territórios geograficamente coerentes, deslocamentos
reduzidos e cargas dentro de uma faixa operacional aceitável.

A V5 não buscará uma única solução chamada de "ideal". Ela produzirá alternativas
viáveis que evidenciem o conflito entre geografia, distância e equilíbrio.

## 2. Princípios de desenho

1. Encontrar uma solução viável vem antes de provar ótimo matemático.
2. Somente regras de negócio inegociáveis podem eliminar um cenário.
3. Preferências geram custo ou penalidade; não tornam o modelo inviável.
4. Exceções são registradas e explicadas, sem apagar o cenário completo.
5. A seleção e a alocação devem ocorrer em problemas regionais menores, com
   reconciliação posterior das fronteiras.
6. Contiguidade deve ser construída e reparada, não imposta dentro de um MIP
   nacional de grande porte.
7. A execução deve sempre manter a melhor solução viável conhecida.
8. Resultados parciais e dados de entrada devem ser preservados para reprodução.

## 3. Classificação das regras

### 3.1 Restrições obrigatórias

- Exatamente 135 gerências.
- Cada unidade atendida pertence a uma única gerência.
- Cada polo selecionado atende sua própria unidade-sede.
- Todas as lojas elegíveis devem pertencer a alguma gerência.
- Municípios formalmente excluídos não entram na demanda elegível.
- Nenhuma gerência pode terminar sem unidade-sede ou sem carteira.
- Códigos territoriais, coordenadas e relações hierárquicas precisam ser válidos
  antes da construção do cenário.

### 3.2 Critérios de aceitação do resultado

Estes critérios devem ser atendidos ao final, mas podem ser obtidos por reparos
posteriores à seleção inicial:

- carteiras territorialmente contíguas;
- nenhuma loja ou unidade obrigatória sem atendimento;
- exatamente 135 carteiras não vazias;
- polos pertencentes às próprias carteiras;
- ausência de sobreposição de atribuição;
- exceções de fronteira explicitamente justificadas;
- fechamento das quantidades por área e GR.

### 3.3 Preferências otimizáveis

- menor distância entre unidade e polo;
- menor distância ponderada pelas lojas;
- menor distância ponderada pela população;
- polo e unidade na mesma UF;
- polo próximo da GR de referência;
- sede em município com estrutura operacional adequada;
- carga dentro da faixa definida;
- menor quantidade de carteiras fora da faixa;
- menor quantidade de cruzamentos de UF;
- menor alteração em relação à estrutura atual;
- evitar polos redundantes na mesma cidade quando não houver demanda suficiente.

### 3.4 Indicadores apenas diagnósticos

- unidades acima do raio de referência;
- municípios pequenos sem atendimento;
- maior distância individual;
- carga mínima e máxima;
- amplitude e dispersão das cargas;
- quantidade de exceções territoriais;
- quantidade de mudanças de sede;
- movimentação dos gerentes atuais;
- municípios atendidos por polo que não é o mais próximo.

Indicadores diagnósticos não podem, isoladamente, cancelar a execução.

## 4. Premissas atuais que deixam de ser rígidas

- As 81 GRs não consomem automaticamente 81 polos exclusivos.
- O raio de 100 km entre GR e polo passa a ser preferência auditável.
- Município com menos de 30 mil habitantes pode ser polo quando necessário para
  cobertura territorial ou operacional.
- Município com mais de 30 mil habitantes, mas sem loja, não é automaticamente
  obrigatório.
- Cruzamento de UF deixa de ser proibido de forma absoluta.
- Cidades acima de 300 mil habitantes não são sempre divididas em distritos.
- A carga não precisa ser matematicamente igual entre as 135 carteiras.
- O modelo não precisa comprovar ótimo global para entregar resultado.
- O limite de candidatos por unidade é uma estratégia computacional adaptativa,
  não uma premissa de negócio.

## 5. Unidade territorial

### 5.1 Regra padrão

O município é a unidade territorial padrão.

### 5.2 Distritalização condicional

Uma metrópole somente será dividida em distritos quando receber mais de um polo
ou quando a concentração de lojas justificar explicitamente a divisão.

O fluxo será:

1. resolver inicialmente com municípios;
2. identificar cidades que comportam múltiplos polos;
3. substituir somente essas cidades por distritos;
4. refazer localmente a seleção e a atribuição.

Isso evita multiplicar antecipadamente o tamanho do problema nacional.

## 6. Cenários produzidos

### 6.1 Cenário Geográfico

Prioriza distância, mesma UF e formação de territórios naturais. O equilíbrio de
carga é corrigido somente quando houver desvio operacional relevante.

### 6.2 Cenário Equilibrado

Prioriza redução dos maiores desvios de carga, respeitando um limite máximo de
piora territorial e de distância.

### 6.3 Cenário Compromisso

Parte do cenário geográfico e aplica somente movimentações que melhorem cargas
sem criar descontinuidade, cruzamento injustificado ou aumento excessivo de
distância. Este será o cenário recomendado por padrão.

## 7. Fluxo de construção

### Etapa 1 — validação e snapshot

- validar lojas, população, municípios, GRs, exclusões e geometrias;
- registrar parâmetros e hashes das entradas;
- salvar uma base reproduzível antes da otimização;
- emitir erros de dados antes de iniciar processamento longo.

### Etapa 2 — distribuição das 135 gerências

- calcular a necessidade de gerências por área ou GR;
- garantir mínimos organizacionais aprovados;
- distribuir o saldo por demanda operacional;
- fechar exatamente 135 antes da escolha das sedes.

### Etapa 3 — escolha inicial de polos

- preservar polos necessários para cobertura regional;
- selecionar os demais por ganho de cobertura e redução de distância;
- admitir exceções de porte quando forem territorialmente necessárias;
- manter sempre uma solução completa de 135 polos.

### Etapa 4 — atribuição inicial

- atribuir primeiro unidades com lojas;
- atribuir depois outras unidades obrigatórias;
- incorporar unidades opcionais quando houver benefício territorial;
- preferir mesma UF sem bloquear exceções de fronteira.

### Etapa 5 — reparo territorial

- detectar componentes desconectados;
- mover ilhas para carteiras vizinhas quando possível;
- trocar ou criar sementes locais quando uma unidade ficar inacessível;
- impedir que uma unidade-ponte seja capturada por uma carteira incompatível;
- registrar exceção somente quando não existir reparo aceitável.

### Etapa 6 — balanceamento

- calcular a carga de cada carteira;
- mover somente unidades de fronteira;
- preservar contiguidade, sede, cobertura e coerência de UF;
- parar quando todas as carteiras estiverem na faixa ou não houver melhoria
  aceitável.

### Etapa 7 — melhoria local

- avaliar troca de sede dentro da própria carteira;
- avaliar troca de unidades entre carteiras vizinhas;
- aceitar movimentos que melhorem o cenário sem violar critérios de aceitação;
- manter o melhor cenário gerado após cada movimento e, separadamente, o melhor
  cenário aprovado quando existir.

### Etapa 8 — auditoria e comparação

- validar todas as restrições obrigatórias;
- calcular os indicadores dos três cenários;
- explicar cada exceção;
- recomendar o cenário Compromisso, mantendo os demais para comparação.

## 8. Ordem de prioridade

1. Produzir uma solução válida com exatamente 135 gerências.
2. Atender todas as lojas e unidades obrigatórias.
3. Garantir territórios coerentes e contíguos.
4. Reduzir distâncias operacionais.
5. Manter cargas dentro da faixa aceitável.
6. Atender preferências de UF, GR e qualidade da sede.
7. Reduzir mudanças em relação à estrutura atual.

Nenhuma etapa posterior pode destruir uma condição já garantida por etapa
anterior.

## 9. Política de falha e exceção

- A execução não pode passar horas sem uma solução viável armazenada.
- Se a construção inicial falhar, a execução termina antes da otimização longa e
  informa as unidades e regras responsáveis.
- Se o limite de tempo for atingido, retorna-se o melhor cenário gerado e, quando
  existir, o melhor cenário aprovado conhecido.
- Se uma preferência não puder ser atendida, o cenário continua e a exceção é
  registrada.
- Se uma restrição obrigatória for inviável, o relatório deve identificar a menor
  combinação conflitante conhecida, com unidades, polos e regra envolvida.

## 10. Painel de avaliação

Cada cenário deverá apresentar no mínimo:

- lojas atendidas e percentual de cobertura;
- municípios e distritos atendidos;
- distância média, P90 e máxima;
- distância ponderada por lojas e por população;
- carga mínima, média, máxima e dispersão;
- carteiras abaixo e acima da faixa;
- carteiras descontíguas;
- cruzamentos de UF;
- unidades acima do raio de referência;
- polos por área e GR;
- exceções e justificativas;
- mudanças em relação ao desenho atual;
- tempo de execução e melhor solução intermediária preservada.

## 11. Decisões de negócio a confirmar

1. Os 135 gerentes são absolutamente fixos?
2. Todas as lojas elegíveis devem ser atendidas ou a cobertura mínima continua em
   95%?
3. Cada GR precisa ter ao menos um polo, apenas estar próxima de um polo, ou somente
   receber uma quantidade mínima de gerentes?
4. Qual faixa de carga é aceitável: 75%–125%, 80%–120% ou outra?
5. Como a carga deve ser calculada: lojas, população, dispersão, área ou combinação?
6. Cruzamentos de UF podem ser aceitos quando reduzirem distância ou melhorarem a
   contiguidade?
7. Municípios sem lojas podem permanecer sem atendimento?
8. Contiguidade é absoluta ou pequenas exceções justificadas são aceitáveis?
9. A estrutura atual deve influenciar a escolha dos polos ou apenas a realocação
   posterior dos gerentes?
10. A divisão de metrópoles deve depender do número de lojas, da carga, do número de
    polos ou de uma combinação desses critérios?

### 11.1 Linha-base recomendada enquanto as decisões não forem confirmadas

Para permitir o avanço do desenho, a V5 adotará provisoriamente as seguintes
respostas. Cada item poderá ser alterado antes da implementação:

| Tema | Decisão provisória | Classificação |
|---|---|---|
| Quantidade de gerências | Exatamente 135 | Restrição obrigatória |
| Cobertura | 100% das lojas ativas elegíveis | Restrição obrigatória |
| Papel das GRs | Toda GR é vinculada ao polo proposto mais adequado para auditoria; quantidade mínima por GR depende de confirmação de negócio | Preferência geográfica + decisão pendente |
| Equilíbrio | Faixa-alvo de 80% a 120%, com exceções explicadas | Critério de aceitação |
| Medição da carga | Lojas como dimensão principal e população como dimensão secundária, sem índice composto | Objetivos separados |
| Cruzamento de UF | Permitido em fronteira direta quando melhorar distância ou contiguidade | Preferência penalizada e auditada |
| Município sem loja | Opcional, independentemente de superar 30 mil habitantes | Preferência de cobertura |
| Contiguidade | Obrigatória como objetivo final; exceção não apaga o cenário, mas impede classificá-lo como aprovado | Critério de aceitação |
| Estrutura atual | Não interfere na escolha da malha; usada somente na realocação posterior | Diagnóstico/transição |
| Distritalização | Aplicada quando a demanda projetada da cidade ultrapassar a capacidade aceitável de uma carteira | Regra adaptativa |

### 11.2 Definição provisória de loja elegível

Uma loja é elegível quando:

- está ativa no período analisado;
- pertence aos tipos de operação incluídos no estudo;
- possui município identificável;
- não pertence à lista formal de exclusões;
- não pertence às empresas formalmente desconsideradas.

Coordenada própria e supervisão atual não definem elegibilidade GreenField:

- loja sem supervisão atual continua elegível, mas não participa da comparação de
  transição até que o vínculo seja corrigido;
- loja sem coordenada própria, mas com município válido, será associada à unidade
  municipal e marcada com qualidade geográfica reduzida;
- loja sem município identificável entra em auditoria crítica e impede a aprovação
  final até ser corrigida ou formalmente excluída.

Lojas com erro cadastral não são silenciosamente descartadas. A extração deve
separar filtro de negócio de filtro de qualidade dos dados.

### 11.2.1 Evidência encontrada na extração atual

A consulta atual de lojas exige simultaneamente:

- período analisado;
- `QTD_ATIVOS > 0`;
- empresa fora da lista de exclusão;
- tipo `Tradicional` ou `Ilhas`;
- latitude e longitude próprias;
- `CHAVE_SUPERVISAO` atual preenchida.

Os quatro primeiros itens caracterizam elegibilidade de negócio. Os dois últimos
são condições cadastrais usadas pela implementação atual e não devem eliminar uma
loja do universo GreenField.

### 11.2.2 Papel provisório das GRs

A base atual das GRs fornece código, nome, coordenada e agência de referência. Ela
não fornece uma malha territorial oficial nem comprova que cada GR precise receber
uma quantidade mínima de gerências.

Por isso, a V5 distribuirá inicialmente as 135 gerências por área organizacional e
demanda de lojas. Depois, cada GR será vinculada ao polo proposto mais adequado. Uma
quota mínima por GR somente será ativada após confirmação explícita de negócio.

### 11.3 Carga sem pesos arbitrários

A V5 não somará população, lojas, área e dispersão em um único índice artificial.
Cada carteira será avaliada em eixos independentes:

1. quantidade de lojas;
2. população atendida;
3. distância operacional;
4. dispersão territorial;
5. quantidade de unidades atendidas.

O balanceamento buscará primeiro reduzir carteiras muito acima ou abaixo da média
de lojas. Entre alternativas equivalentes, usará população, distância e dispersão
como critérios de desempate. Área territorial permanecerá como indicador, não como
carga presumida.

Uma carteira poderá ficar fora da faixa de população se isso for necessário para
preservar contiguidade ou evitar deslocamentos excessivos. A justificativa será
registrada no painel do cenário.

### 11.4 Aprovação em dois níveis

Cada resultado receberá dois estados independentes:

- **CENÁRIO GERADO:** possui 135 gerências e uma atribuição completa das lojas;
- **CENÁRIO APROVADO:** além de gerado, atende todos os critérios territoriais e
  organizacionais definidos.

Essa separação impede que uma exceção transforme todo o processamento em ausência
de resultado. Um cenário gerado, mas não aprovado, continua disponível para análise
e correção.

## 12. Critério de aprovação da especificação

A especificação estará pronta para implementação quando as dez decisões acima
estiverem definidas e cada uma estiver classificada como:

- restrição obrigatória;
- critério de aceitação;
- preferência com penalidade;
- indicador diagnóstico.
