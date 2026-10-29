# Proposal

## Why

No chat, add_transaction, update_transaction e add_event fazem commit antes do JEV. Uma recusa troca a resposta, mas deixa a alteração no PostgreSQL. O contrato da prática exige que uma escrita recusada não permaneça.

## What Changes

- As três tools continuam executando SQL e retornando dados reais ao especialista. Durante o chat, usam uma transação exclusiva do turno e não fazem commit nem fecham a conexão.
- Orquestrador, guardrail de saída e JEV continuam na ordem atual. O JEV devolve um veredito estruturado do turno. Um finalizador confirma todas as escritas somente se o turno for aprovado e não houver erro; nos demais casos, desfaz todas.
- O fallback financeiro participa da mesma transação. Erro devolvido por tool, exceção de modelo e falha do JEV impedem commit.
- O JEV externo recebe o retorno estruturado real de `add_transaction`, inclusive pelo fallback, com o ID provisório e os dados da conversão para validar a resposta.
- A sincronização automática com Google Calendar fica fora do chat nesta mudança. O especialista confirma apenas o registro local; a tool Google permanece disponível fora desse fluxo.
- A rota e o JSON de /chat, as consultas, o histórico de resposta exibida no Mongo e o acesso MCP direto permanecem. O MCP mantém seu modo próprio de commit.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- chat-write-authorization: autorização de permanência das escritas PostgreSQL por veredito do JEV no turno.

## Impact

- Código: grafo, juiz, gestão de conexão, três tools de escrita, prompt da agenda, observabilidade e testes.
- A conexão fica aberta durante orquestrador, guardrail e JEV. Esse custo e o p95 serão medidos.
- O resultado da tool é provisório até o commit. Falha durante o commit pode deixar resultado incerto e nunca autoriza uma resposta falsa de sucesso.
- Sem mudança no contrato público da API e sem segundo agente ou tool de commit exposta ao modelo.
