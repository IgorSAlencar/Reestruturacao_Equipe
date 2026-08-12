# Territórios BE

Aplicação local para explorar polos atuais, comparar cenários GreenField V3/V4 e construir simulações manuais de carteira.

## Início rápido

1. Copie as novas variáveis de `.env.example` para `.env` e informe um `MAPBOX_ACCESS_TOKEN` público (`pk.`).
2. Instale as dependências com `npm install`.
3. Em um terminal execute `npm run dev:api`.
4. Em outro terminal execute `npm run dev`.
5. Abra o endereço de rede exibido pelo Vite.

A API descobre automaticamente cenários completos nas pastas `saida_greenfield_v3` e `saida_greenfield_v4`. A visão **Atual** é criada pelo botão **Atualizar lojas**, que executa a consulta SQL existente e grava um cache local em `.territorios-data`.

## Builder

Abra qualquer cenário no Builder, arraste polos, selecione unidades territoriais e atribua-as manualmente ou redistribua a seleção entre polos da mesma gerência de área. Rascunhos são persistidos localmente em SQLite com controle de revisão.
