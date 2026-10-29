# Design

## Context

O grafo atual chama especialista, orquestrador, guardrail de saída e JEV nessa ordem. As três tools PostgreSQL confirmam e fecham a conexão antes do JEV. O fallback financeiro chama add_transaction diretamente. O especialista de agenda também pode chamar Google antes do julgamento. O estado de mensagens usa MemorySaver entre turnos.

## Goals / Non-Goals

Goals: manter os nomes e retornos reais das tools; dar ao JEV evidência do SQL executado; confirmar ou desfazer todas as escritas PostgreSQL do turno após o veredito; preservar /chat e o MCP direto.

Non-goals: atomicidade entre PostgreSQL, Mongo e Google; deduplicação de duas novas requisições HTTP indistinguíveis; garantir que uma conexão perdida durante commit prove rollback.

## Decisions

### 1. Unidade de trabalho exclusiva do turno

Um gestor de transação é criado no início de cada chamada de chat e abre a conexão somente na primeira escrita. Sua identidade é passada pelo RunnableConfig e resolvida por um registro de runtime protegido por lock. A conexão não entra no estado serializado do grafo. As tools do chat usam a conexão gerida, fecham apenas o cursor e devolvem resultados reais provisórios. Chamadas diretas sem identidade de turno, como MCP, mantêm a conexão e commit próprios. A implementação testa a propagação de RunnableConfig dentro de create_agent; se falhar, usa injeção explícita equivalente antes de ativar o fluxo.

O gestor marca aborto irrevogável quando uma tool devolve erro ou lança exceção. Guarda resultados somente durante a vida do turno. O payload de `add_transaction` enviado ao JEV é limitado a status, ID provisório, valor original, valor em BRL, moeda, taxa e data da cotação; não inclui descrição nem data da transação. Operações concorrentes no mesmo turno são serializadas. Consultas após escrita usam a mesma conexão quando precisam ler o próprio resultado provisório.

### 2. Resposta e veredito

O SQL executado na transação retorna ID e valores reais para especialista e orquestrador. O texto produzido é candidato e não sai para o usuário antes de commit confirmado. O JEV recebe a pergunta do turno após anonimização, a candidata e a evidência real das tools. Para `add_transaction`, o retorno estruturado é transportado mesmo quando o nó financeiro substitui as mensagens internas do especialista; o fallback usa esse mesmo caminho. Seu resultado inclui veredito estruturado do turno: aprovado, recusado ou falha. Campos transitórios são reiniciados no começo e ao final do turno. Um erro de tool não pode ser apagado por uma frase favorável do agente ou pelo JEV.

O guardrail de saída atual revisa a resposta, mas não bloqueia. Sua exceção aborta o turno. Protocolo inválido do revisor conserva a frase original, que ainda passa ao JEV.

### 3. Finalização

O grafo executa um finalizador após o JEV. Sem escrita PostgreSQL ele apenas preserva a resposta julgada. Com escrita e sem erro, aprovação chama commit uma vez. Recusa, falha e erro anterior chamam rollback e substituem a candidata por frase segura ou operacional. O gestor fecha a conexão em todos os caminhos. Uma exceção fora do finalizador também dispara rollback no wrapper da chamada do grafo.

Se commit lançar exceção, seu resultado é incerto por padrão. O turno não afirma sucesso nem ausência de escrita e não repete SQL automaticamente. Um commit confirmado seguido de falha de Mongo, monitor, checkpoint ou HTTP não é desfeito; esse limite é comunicado no documento técnico. Sem chave estável no contrato /chat, uma nova requisição pode representar repetição ou intenção nova.

Mensagens de tool provisórias que foram desfeitas não podem aparecer no contexto futuro como confirmação. O finalizador remove ou marca esses resultados no histórico persistido do grafo.

### 4. Google, memória e métricas

A tool Google não fica disponível ao especialista nesse fluxo do chat. O prompt da agenda fala apenas de confirmação local. A sincronização automática com Google Calendar fica fora desta mudança e pode ser redesenhada depois; nenhuma informação do evento é enviada ao Google pelo finalizador. O Mongo salva a resposta efetivamente exibida após finalização. O monitor distingue ok após commit, bloqueado na recusa e erro operacional, medindo a latência completa.

## Risks / Trade-offs

- A conexão e locks de UPDATE ficam abertos durante a chamada externa ao JEV, potencialmente acima do SLO p95 de 8 segundos. Medir duração e concorrência.
- ToolMessage com retorno provisório pode ser persistida pelo MemorySaver. Limpar ou sinalizar rollback antes de reutilizar o histórico.
- Chamada direta sem contexto de chat tem commit próprio. Testes precisam provar que a configuração do agente nunca perde a identidade de turno; falha nesse contrato bloqueia ativação.
- Falha durante commit gera resultado incerto. Não há idempotência entre novas requisições HTTP.
- A sincronização automática com Google Calendar não ocorre no chat nesta mudança; o usuário recebe somente confirmação local.

## Migration Plan

1. Substituir o contrato OpenSpec antigo de proposta preparada por esta transação por turno e validar os artefatos.
2. Implementar gestor, tools e finalizador mantendo o contrato direto do MCP.
3. Testar aprovação, recusa, falhas, concorrência e regressões de leitura sem depender de serviços externos.
4. Medir latência e registrar evidência com banco de teste quando disponível.
5. Produzir decisao_tecnica.md com decisão, alternativas, SRE, limites e lista real de arquivos.
