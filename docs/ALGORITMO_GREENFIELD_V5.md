# GreenField V5 — algoritmo proposto

> **Rascunho a revisar.** As regras confirmadas estão em
> `GREENFIELD_V5_REGRAS_CONSOLIDADAS.md`. A próxima versão do algoritmo deverá ser
> derivada exclusivamente da especificação consolidada.

Este documento apresenta o pseudocódigo da linha-base provisória da V5. Ele deve
ser ajustado após as decisões de negócio pendentes.

## 1. Invariantes e critérios de aprovação

### 1.1 Invariantes do cenário gerado

Depois que a seleção dos polos estiver concluída, o algoritmo deve preservar:

1. exatamente 135 polos após a etapa de seleção;
2. cada polo atribuído à própria sede;
3. cada unidade atendida atribuída uma única vez;
4. todas as lojas elegíveis cobertas;
5. 135 carteiras não vazias;
6. melhor cenário gerado salvo após cada etapa;
7. execução determinística para os mesmos dados e parâmetros.

Uma preferência não pode ser tratada como invariante.

### 1.2 Critérios adicionais do cenário aprovado

Um cenário gerado somente recebe o estado `APROVADO` quando também possui:

- cada carteira conectada ao respectivo polo;
- nenhuma unidade obrigatória ausente;
- cruzamentos de UF justificados;
- nenhuma inconsistência cadastral crítica.

Falhar em um critério de aprovação dispara reparo e mantém o cenário gerado para
análise. Não apaga a solução nem viola os invariantes de geração.

## 2. Preparação

```text
extrair todas as lojas do período usando somente filtros de negócio
classificar qualidade de município, coordenada e supervisão atual

se loja possui município válido:
    incluir na demanda GreenField
    se coordenada própria ausente:
        usar centro municipal apenas para referência
        registrar QUALIDADE_COORDENADA = CENTRO_MUNICIPAL
senão:
    registrar inconsistência crítica

carregar municípios, população, exclusões, GRs e geometrias
remover somente municípios formalmente excluídos
agrupar lojas por município
marcar município com loja como ATENDIMENTO_OBRIGATORIO
marcar município sem loja como ATENDIMENTO_OPCIONAL
construir grafo oficial de vizinhança municipal
salvar snapshot das entradas e parâmetros
```

O cenário pode ser gerado com lojas sem supervisão atual. Entretanto, nenhuma loja
sem município válido pode ser silenciosamente ignorada.

## 3. Distritalização condicional

Primeiro calcula-se uma referência nacional simples:

```text
media_lojas_por_gerencia = total_lojas_elegiveis / 135
limite_superior_provisorio = 1.20 * media_lojas_por_gerencia
```

Para cada cidade:

```text
se lojas_da_cidade <= limite_superior_provisorio:
    manter município agregado
senão:
    polos_necessarios = ceil(lojas_da_cidade / media_lojas_por_gerencia)
    se polos_necessarios >= 2 e houver geometria distrital confiável:
        substituir município pelos distritos
        associar lojas aos distritos
    senão:
        manter município e registrar necessidade metropolitana não detalhada
```

População de 300 mil habitantes deixa de ser gatilho isolado. O gatilho principal
passa a ser a demanda operacional comparada à capacidade de uma carteira.

## 4. Candidatos a polo

```text
candidatos = unidades que:
    possuem loja
    OU possuem população acima do porte preferencial
    OU são necessárias para cobrir componente com lojas
    OU foram incluídas como exceção territorial auditada

para cada candidato:
    registrar população
    registrar lojas locais
    registrar distância às GRs
    registrar qualidade cadastral
    registrar se é exceção de porte
```

Não há exclusão automática por população inferior a 30 mil.

## 5. Distribuição indicativa das 135 gerências

A quota por área orienta a escolha, mas pode ser ajustada por viabilidade.

```text
para cada área com lojas elegíveis:
    quota_teorica = 135 * lojas_area / lojas_total
    quota_inicial = floor(quota_teorica)

distribuir vagas restantes pelos maiores restos
garantir ao menos uma vaga para cada área com demanda
ajustar quotas para que a soma seja exatamente 135
```

Transferências de quota entre áreas ficam permitidas quando necessárias e são
registradas. Não existe quota rígida por GR na linha-base.

## 6. Sementes territoriais obrigatórias

Antes de otimizar distância:

```text
componentes_obrigatorios = componentes do grafo contendo ao menos uma loja

para cada componente_obrigatorio:
    selecionar um candidato local como semente

se quantidade_de_sementes > 135:
    cenário é estruturalmente inviável
    informar cada componente e seus candidatos
```

Essas sementes permanecem protegidas até o fim da construção.

## 7. Seleção dos polos restantes

Os polos são adicionados incrementalmente:

```text
selecionados = sementes_obrigatorias

enquanto quantidade(selecionados) < 135:
    para cada candidato ainda não selecionado:
        calcular redução da distância ponderada por lojas
        calcular redução da maior concentração de lojas
        verificar contribuição à quota indicativa da área
        verificar cobertura de GRs distantes

    escolher candidato pelo seguinte desempate ordenado:
        1. maior redução de distância ponderada por lojas
        2. maior redução de concentração
        3. área abaixo da quota indicativa
        4. maior população da sede
        5. identificador estável

    adicionar candidato
```

O cálculo é heurístico e mantém uma combinação completa de polos a cada passo.

## 8. Territórios por Voronoi no grafo

### 8.1 Motivo

O crescimento do V3 atribui uma unidade definitivamente ao primeiro polo vencedor.
Uma unidade de articulação pode ser tomada por uma carteira e bloquear outra. A V5
usará um Voronoi de caminhos mínimos com predecessor registrado.

### 8.2 Inicialização

```text
para cada polo:
    custo[polo_sede] = 0
    dono[polo_sede] = polo
    predecessor[polo_sede] = nulo
    inserir sede na fila de prioridade
```

### 8.3 Expansão

Para cada aresta territorial entre `u` e `v`:

```text
custo_aresta = distância entre centros territoriais

se UF(v) diferente da UF(polo):
    se UFs não são oficialmente vizinhas:
        expansão proibida
    senão:
        aplicar prioridade inferior de fronteira, sem somar peso km arbitrário

se v é unidade de articulação necessária a outra componente obrigatória:
    adiar decisão até comparar caminhos concorrentes

novo_custo = custo[u] + custo_aresta

se caminho for melhor segundo a ordem:
    1. menor quantidade de cruzamentos de UF
    2. menor custo territorial acumulado
    3. menor distância direta ao polo
    4. carteira com menos lojas
    5. identificador estável
então:
    dono[v] = polo
    predecessor[v] = u
    atualizar fila
```

Como cada unidade recebe o mesmo dono de seu predecessor, a construção tende a
produzir carteiras conectadas à sede. A validação independente continua obrigatória;
qualquer exceção impede a aprovação, mas não elimina o cenário gerado.

## 9. Inclusão dos municípios opcionais

Depois das unidades com lojas:

```text
para cada município opcional:
    incluir se:
        for necessário como corredor
        OU tocar uma carteira e estiver dentro do raio de referência
        OU reduzir lacuna territorial relevante
    caso contrário:
        deixar sem atendimento
        registrar motivo
```

Uma unidade opcional não pode deslocar uma unidade com loja nem fragmentar uma
carteira.

## 10. Auditoria da solução geográfica

```text
validar 135 polos
validar 135 carteiras não vazias
validar polo dentro da própria carteira
validar cobertura de todas as lojas
validar atribuição única
validar invariantes do cenário gerado

se invariantes de geração forem satisfeitos:
    salvar CENARIO_GEOGRAFICO como melhor cenário gerado
senão:
    encerrar antes das melhorias e informar a obrigação conflitante

validar critérios de aprovação:
    conexão de cada carteira ao polo
    unidades obrigatórias
    cruzamentos de UF
    inconsistências cadastrais críticas

se critérios de aprovação forem satisfeitos:
    salvar também como melhor cenário aprovado
senão:
    manter cenário gerado
    executar reparo local
```

## 11. Reparo local

```text
para cada violação:
    identificar subgrafo mínimo afetado
    congelar restante do país

    tentar nesta ordem:
        transferir ilha para carteira vizinha
        devolver unidade de articulação ao caminho obrigatório
        trocar sede dentro da carteira
        adicionar semente local e retirar polo redundante da mesma área
        reconstruir somente o subgrafo afetado

    aceitar reparo apenas se preservar os invariantes de geração
    priorizar reparos que reduzam violações de aprovação

se nenhum reparo funcionar:
    manter melhor cenário gerado
    registrar motivo de não aprovação
```

## 12. Cenário Equilibrado

Parte sempre do cenário Geográfico válido.

```text
alvo_lojas = total_lojas / 135
faixa_inferior = 0.80 * alvo_lojas
faixa_superior = 1.20 * alvo_lojas

enquanto houver movimento aceitável:
    localizar carteira acima da faixa
    avaliar somente unidades de fronteira
    avaliar carteiras vizinhas abaixo da faixa

    rejeitar movimento se:
        fragmentar origem
        não tocar destino
        mover sede
        deixar loja sem atendimento
        criar cruzamento de UF não justificável
        ultrapassar teto de piora de distância

    ordenar movimentos por:
        1. redução do maior desvio de lojas
        2. redução do desvio de população
        3. menor aumento de distância
        4. menor dispersão

    aplicar melhor movimento
    preservar os invariantes de geração
    não aumentar violações de aprovação
    salvar novo cenário gerado
```

## 13. Cenário Compromisso

Também parte do cenário Geográfico.

```text
avaliar movimentos do balanceamento

aceitar apenas movimentos que:
    retirem uma carteira de fora da faixa
    OU reduzam materialmente o maior desvio

e simultaneamente:
    preservem os invariantes de geração
    não aumentem violações de aprovação
    respeitem teto mais rigoroso de piora de distância
    não aumentem cruzamentos injustificados
```

O cenário Compromisso não é a média matemática dos outros dois. Ele é o cenário
geográfico com o menor conjunto de correções operacionais necessárias.

## 14. Vínculo das GRs

Depois da malha pronta:

```text
para cada GR:
    candidatos = polos da mesma área
    escolher polo por menor distância
    registrar distância, UF e área

    se distância exceder referência:
        registrar exceção
        avaliar polo alternativo somente se não prejudicar território
```

O vínculo não altera automaticamente a sede nem a carteira.

## 15. Escolha da sede dentro da carteira

```text
para cada carteira:
    avaliar candidatos pertencentes à própria carteira
    escolher sede que minimize:
        1. distância ponderada pelas lojas
        2. maior distância às unidades
        3. distância à GR vinculada
        4. exceção de porte populacional

    aceitar troca somente se a nova sede continuar na carteira
```

## 16. Estados de saída

```text
GERADO:
    135 polos
    135 carteiras
    lojas elegíveis cobertas
    atribuição completa e única

APROVADO:
    GERADO
    + contiguidade validada
    + nenhuma unidade obrigatória ausente
    + cruzamentos de UF justificados
    + dados críticos resolvidos
```

Os três cenários podem ser gerados. Somente os que cumprirem os critérios finais
recebem o estado `APROVADO`. Para cada variante serão preservados separadamente o
melhor cenário gerado e o melhor cenário aprovado, quando este existir.

## 17. Limites e fallback

```text
cada etapa possui limite próprio de tempo e movimentos
antes da etapa, salvar melhor cenário gerado
durante a etapa, salvar cada melhoria aceita

ao atingir limite:
    retornar melhor cenário gerado
    retornar também melhor cenário aprovado, quando existir
    marcar etapa como INTERROMPIDA_COM_FALLBACK
    continuar geração de relatórios
```

Não existe caminho em que o algoritmo passe horas sem possuir uma solução
intermediária recuperável.
