# Tasks

## 1. Contrato e base
- [x] 1.1 Substituir a spec de preparação pelo contrato de transação por turno e validar OpenSpec.
- [ ] 1.2 Registrar a linha de base do defeito nas três escritas com testes e marcadores, sem alterar dados de produção.

## 2. Transação das tools
- [x] 2.1 Criar gestor de transação por turno com conexão tardia, isolamento por chamada, aborto irrevogável e fechamento seguro.
- [x] 2.2 Adaptar add_transaction, update_transaction e add_event para usar a transação do chat sem commit local, mantendo o modo direto do MCP.
- [x] 2.3 Garantir que leituras após escrita e o fallback financeiro usem a mesma transação quando necessário; capturar resultados reais para o JEV.

## 3. Grafo e julgamento
- [x] 3.1 Fazer o JEV devolver veredito estruturado do turno e usar evidência das tools mesmo quando o especialista substitui suas mensagens.
- [x] 3.2 Ligar um finalizador após o juiz para commit aprovado ou rollback em recusa, falha e erro de tool; impedir sucesso falso no histórico.
- [x] 3.3 Tratar falhas antes e durante commit, exceções de modelo e guardrail, sem reexecutar automaticamente uma escrita incerta.

## 4. Integrações e verificação
- [x] 4.1 Impedir chamadas automáticas ao Google Calendar no chat e restringir a resposta julgada ao efeito confirmado no PostgreSQL.
- [x] 4.2 Preservar Mongo, rotas e consultas; ajustar status do monitor para aprovação, recusa e erro.
- [ ] 4.3 Testar as três tools com aprovação, recusa, erro SQL, exceção após SQL, timeout do JEV, repetição e concorrência; executar regressões relevantes.
- [x] 4.4 Sincronizar a spec delta com openspec/specs e validar as specs principais.
- [x] 4.5 Criar decisao_tecnica.md com fluxo anterior/novo, alternativas, métricas, falhas, limites e arquivos realmente alterados.
