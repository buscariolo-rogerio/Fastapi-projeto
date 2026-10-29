# Chat Write Authorization Specification

## Purpose

Autorizar a permanência de escritas financeiras e de agenda feitas no chat somente após o julgamento da resposta, mantendo o PostgreSQL coerente com o desfecho informado ao usuário.

## Requirements

### Requirement: Escrita provisória por turno
As tools de criação de transação, atualização de transação e criação de evento SHALL executar SQL em uma transação exclusiva do turno sem confirmar a alteração antes do JEV.

#### Scenario: Retorno real antes do julgamento
- **WHEN** uma tool de escrita executa SQL com sucesso
- **THEN** o especialista recebe o retorno real da operação, incluindo valores e ID disponível, enquanto a alteração permanece não confirmada para outras conexões

#### Scenario: Múltiplas escritas no turno
- **WHEN** o turno executa mais de uma escrita PostgreSQL
- **THEN** todas participam da mesma transação e nenhuma é confirmada separadamente

### Requirement: Autorização estruturada
O JEV SHALL emitir aprovação, recusa ou falha referente apenas ao turno atual; o resultado da frase e rótulos acumulados SHALL NOT substituir o veredito estruturado.

#### Scenario: Evidência da inclusão financeira
- **WHEN** `add_transaction` conclui uma inclusão pelo especialista ou pelo fallback
- **THEN** o serviço externo do JEV recebe status, ID provisório, valores original e convertido, moeda, taxa de câmbio e data da cotação junto da resposta candidata

#### Scenario: Aprovação íntegra
- **WHEN** o JEV aprova a resposta candidata com evidência suficiente e nenhuma etapa do turno falhou
- **THEN** o finalizador tenta confirmar a transação antes de entregar a resposta de sucesso

#### Scenario: Recusa ou indisponibilidade
- **WHEN** o JEV recusa, não responde, está sem chave, retorna dados inválidos ou falha
- **THEN** o finalizador desfaz toda escrita PostgreSQL do turno e entrega uma resposta segura

#### Scenario: Sem evidência da escrita
- **WHEN** uma resposta afirma sucesso de escrita sem retorno real da tool no turno atual
- **THEN** nenhuma escrita do turno é confirmada

### Requirement: Falhas impedem confirmação
Uma falha de tool ou de etapa necessária após o SQL SHALL invalidar a transação do turno, mesmo quando a falha for devolvida como resultado em vez de exceção.

#### Scenario: Tool devolve erro
- **WHEN** uma tool de escrita devolve status de erro
- **THEN** todas as escritas PostgreSQL do turno são desfeitas

#### Scenario: Modelo ou guardrail falha após SQL
- **WHEN** especialista, orquestrador ou guardrail de saída falha após uma escrita provisória
- **THEN** a transação é desfeita e nenhuma frase de sucesso é entregue

#### Scenario: Commit incerto
- **WHEN** a conexão cai durante o commit e o resultado não pode ser confirmado
- **THEN** a resposta informa incerteza, não afirma sucesso ou ausência de escrita e não repete a operação automaticamente

### Requirement: Resposta fiel ao resultado confirmado
A resposta de sucesso entregue ao usuário SHALL ser a candidata julgada pelo JEV e SHALL sair apenas após commit confirmado. Um rollback SHALL impedir que retornos provisórios sejam tratados como registros permanentes em turnos futuros.

#### Scenario: Escrita aprovada
- **WHEN** o JEV aprova e o commit confirma
- **THEN** o usuário recebe a resposta julgada, apoiada nos retornos reais das tools

#### Scenario: Escrita recusada
- **WHEN** o JEV recusa uma escrita
- **THEN** transactions e events ficam iguais ao estado anterior do turno e o histórico não apresenta o retorno provisório como sucesso confirmado

### Requirement: Efeito externo fora do chat
O fluxo do chat SHALL evitar chamadas automáticas ao Google Calendar antes e depois do JEV nesta mudança. A resposta de sucesso SHALL afirmar apenas o efeito local confirmado.

#### Scenario: Evento recusado
- **WHEN** o JEV recusa um evento
- **THEN** nem PostgreSQL nem Google Calendar recebem o novo evento

#### Scenario: Evento local aprovado
- **WHEN** o evento local é confirmado no PostgreSQL
- **THEN** o chat não envia os dados ao Google e não afirma sincronização bem-sucedida

### Requirement: Compatibilidade e observabilidade
O fluxo SHALL preservar o contrato público de /chat, as consultas e o modo direto do MCP financeiro; o monitor SHALL distinguir aprovação confirmada, recusa e falha operacional.

#### Scenario: Consulta sem escrita
- **WHEN** o usuário faz apenas uma consulta
- **THEN** as tools de leitura continuam disponíveis sem transação de escrita pendente

#### Scenario: Chamada direta fora do chat
- **WHEN** o MCP usa uma tool financeira fora do turno do chat
- **THEN** a chamada conserva seu commit próprio

#### Scenario: Métrica do turno
- **WHEN** um turno termina
- **THEN** o monitor registra latência total, status, tokens e custo, incluindo o tempo até commit ou rollback
