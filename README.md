# Territórios BE

Aplicação local para explorar polos atuais, comparar cenários GreenField V3/V4 e construir simulações manuais de carteira.

## Início rápido

Pré-requisitos: Node.js 22.5–22.x e npm 10.x. A versão usada na máquina
corporativa (Node 22.11.0 / npm 10.9.0) é suportada.

1. Copie as variáveis necessárias de `.env.example` para `.env`. O mapa já possui um token público (`pk.`); `MAPBOX_ACCESS_TOKEN` é apenas uma substituição opcional.
2. Instale exatamente as dependências validadas com `npm ci`.
3. Em um terminal execute `npm run dev:api`. O script habilita automaticamente
   o SQLite experimental exigido pelo Node 22.11.0.
4. Em outro terminal execute `npm run dev`.
5. Abra `http://10.206.168.97:5173`. A API responde no mesmo IP em
   `http://10.206.168.97:333` e o frontend encaminha `/api` para esse endereço.

Os endereços são controlados por `APP_HOST`, `WEB_PORT`, `API_HOST` e
`API_PORT`. Os padrões estão preparados para a máquina corporativa
(`10.206.168.97`, portas `5173` e `333`).

A API descobre automaticamente cenários completos nas pastas `saida_greenfield_v3` e `saida_greenfield_v4`. A visão **Atual** é criada pelo botão **Atualizar lojas**, que executa a consulta SQL existente e grava um cache local em `.territorios-data`.

A conexão SQL da visão **Atual** usa diretamente o pacote Node `mssql`, com as
variáveis `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, `SQL_PASSWORD`, `SQL_DOMAIN`
e `SQL_INSTANCE`. É possível testar a conexão em `GET /api/sql/health`; a rota
não devolve senha nem outras credenciais.

Se uma tentativa anterior deixou `node_modules` parcialmente instalado, feche
terminais Node/Vite e o Explorer do VS Code dentro de `node_modules`, remova a
pasta e execute `npm ci`. As versões de React, Vite, Tailwind, TypeScript,
Mapbox, Express e demais pacotes foram alinhadas ao repositório
`mapa-hierarquia-visualiza`, já homologado no Nexus corporativo. A API roda em
JavaScript puro e não depende de `tsx`.

## Builder

Abra qualquer cenário no Builder, arraste polos, selecione unidades territoriais e atribua-as manualmente ou redistribua a seleção entre polos da mesma gerência de área. Rascunhos são persistidos localmente em SQLite com controle de revisão.
