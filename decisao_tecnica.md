# Decisão técnica: transação por turno com julgamento do JEV

## Decisão

Usar o método **Transacionar** para escritas do chat. As tools continuam executando SQL e devolvendo o resultado real ao especialista, mas deixam de confirmar a conexão por conta própria quando são chamadas dentro de um turno do chat. O grafo mantém uma única conexão PostgreSQL por turno, aberta na primeira escrita, e um nó finalizador faz `commit` ou `rollback` depois do veredito estruturado do JEV.

O fallback financeiro chama a mesma `add_transaction`, com o mesmo contexto transacional. O resultado de sucesso do fallback é transportado como evidência do turno para o JEV, tal como o resultado de uma chamada normal da tool. O payload externo dessa evidência contém somente status, ID provisório, valor original, valor em BRL, moeda, taxa e data da cotação. Não contém descrição nem data da transação. Esse envio ao TypeSafe/JEV foi autorizado pelo usuário.

O modo direto das tools financeiras, incluindo o MCP sem identidade de turno, conserva seu próprio `commit`. O fluxo do chat não oferece ao modelo uma segunda tool de commit ou rollback.

## Fluxo

```text
especialista → tool executa SQL na transação do turno → orquestrador
             → guardrail de saída → juiz/JEV
                                      ↓ veredito estruturado
                        condição do grafo após o juiz
                         ├─ aprovado e sem falha → nó de commit → resposta julgada
                         └─ recusado/falha/erro → nó de rollback → frase segura
```

Uma exceção durante `commit` é tratada como resultado incerto. A resposta não afirma que a escrita ocorreu ou que foi desfeita, e o sistema não repete automaticamente a operação.

## Por que esta decisão

- Preserva a estrutura existente: especialista, tools, orquestrador, guardrail e juiz continuam no mesmo fluxo.
- Mantém as tools como responsáveis pelo SQL e pelo retorno de evidência; o código comum do grafo controla o fim da transação.
- Evita gravar e depois reconstruir o estado anterior. Em especial, uma atualização não precisa guardar uma cópia de cada campo anterior para tentar desfazê-la por SQL inverso.
- Usa transações PostgreSQL e `commit`/`rollback` já disponíveis no driver. Não exige nova tecnologia, um segundo banco, uma camada adicional de acesso a dados ou outra tool visível ao modelo.
- Permite condicionar a permanência da escrita ao valor estruturado `veredito_turno` produzido pelo juiz.

## Propriedade da conexão

Cada chamada de chat registra um gestor de transação no runtime. O identificador segue no `RunnableConfig`, sem colocar uma conexão não serializável no estado do LangGraph. As tools resolvem o gestor por esse identificador. A conexão abre sob demanda na primeira escrita, permanece aberta enquanto especialista, orquestrador, guardrail e JEV trabalham, e fecha ao terminar a transação. Uma aresta condicional do LangGraph encaminha o veredito aprovado ao nó de commit e os demais resultados ao nó de rollback.

Escritas e leituras que compartilham uma conexão usam o mesmo lock por turno. Uma leitura feita depois de uma escrita pode ver o resultado provisório na própria transação. O estado de evidência específico de `add_transaction` é limpo pelo finalizador para não carregar valores provisórios para o próximo turno.

## Tratamento de falhas

| Falha | Ação | Resposta/limite |
|---|---|---|
| Tool PostgreSQL devolve `status: error` ou lança exceção | Marca o turno como abortado; o juiz não aprova uma escrita após esse erro; o finalizador desfaz a transação | Mensagem operacional segura |
| Erro de leitura ou de outra tool no especialista financeiro | Marca o turno como abortado, inclusive se uma escrita anterior estiver pendente | Rollback no finalizador |
| Especialista financeiro falha | Marca o turno como abortado e devolve resposta operacional; o finalizador desfaz | Nenhuma frase de sucesso |
| Especialista, orquestrador ou guardrail lança exceção fora do tratamento local | A chamada do grafo termina; o `finally` do wrapper remove o gestor e tenta rollback | A rota devolve a mensagem genérica existente; se rollback também falhar, não há confirmação de ausência de escrita |
| Resposta financeira viola o JSON contratado após SQL | Aborta o turno | Rollback e resposta operacional |
| JEV sem chave, indisponível, timeout, HTTP inválido, payload inválido ou recusa | Define falha ou recusa no estado estruturado | Rollback e frase segura |
| Erro ao fazer rollback | Registra resultado incerto do desfazimento | Orienta consultar os registros antes de repetir |
| Conexão cai durante `commit` | Não assume sucesso nem rollback | Informa incerteza e não repete SQL automaticamente |
| Histórico Mongo falha depois de commit confirmado | Registra a falha sem substituir a resposta confirmada | A resposta julgada e confirmada continua sendo devolvida; o histórico pode não ter sido salvo |

O contrato atual de `/chat` não contém uma chave de idempotência. Se o cliente repetir uma requisição depois de uma resposta incerta, ela pode executar uma nova operação. O sistema não tenta inferir se a repetição representa o mesmo pedido.

## Integrações e observabilidade

O chat não chama Google Calendar antes nem depois do JEV. O especialista de agenda confirma somente o registro local no PostgreSQL; não promete sincronização externa. O acesso MCP direto mantém o comportamento próprio.

O monitor usa o veredito e o resultado final da transação para diferenciar aprovação confirmada, recusa e falha operacional. A medição de turno inclui o período até o `commit` ou `rollback`. A conexão permanece aberta durante a chamada externa do JEV, então a latência completa e o p95 precisam ser observados no ambiente integrado; não foi medida uma linha de base com PostgreSQL de teste nesta execução.

## Validação

- `openspec validate --specs --strict` passou: 1 spec validada.
- `openspec validate defer-chat-writes-until-jev --strict` passou.
- A análise sintática Python passou para os sete arquivos alterados ou criados em `app/`.
- Uma rodada anterior da implementação executou `python -m pytest -q`: 27 testes passaram. Essa rodada antecede os ajustes finais de transporte de evidência e serialização de leituras; a tarefa de regressão final permanece pendente.
- Não foi feita escrita em banco de produção nem medição com serviços externos nesta execução.

## Arquivos criados

- `app/turn_transaction.py` — gestor da conexão e da decisão final de commit ou rollback por turno.
- `tests/test_turn_transaction.py` — testes com conexão simulada para o ciclo transacional.
- `openspec/specs/chat-write-authorization/spec.md` — spec principal que substitui o marcador anterior no diretório `openspec/specs/`.
- `decisao_tecnica.md` — este registro das decisões, falhas, limites e arquivos.

## Arquivos modificados

- `app/graph.py` — passa o contexto transacional às tools, registra evidência autorizada de `add_transaction`, condiciona commit ou rollback pelo veredito do juiz, limpa evidência provisória e preserva a resposta confirmada se o histórico Mongo falhar.
- `app/juiz.py` — coleta a evidência estruturada de `add_transaction` e grava no estado o veredito aprovado, recusado ou falha.
- `app/observabilidade.py` — classifica status por veredito e resultado transacional.
- `app/prompts.py` — remove promessas de sincronização com Google Calendar no chat.
- `app/tools/agenda.py` — executa `add_event` na transação do chat, mantém commit próprio fora do chat e compartilha a conexão nas leituras do turno.
- `app/tools/financeiro.py` — adapta `add_transaction` e `update_transaction`, compartilha a conexão de leitura e mantém commit próprio fora do chat.
- `openspec/changes/defer-chat-writes-until-jev/proposal.md` — descreve a mudança para Transacionar e o envio autorizado da evidência de `add_transaction` ao JEV.
- `openspec/changes/defer-chat-writes-until-jev/design.md` — registra propriedade da conexão, fluxo de decisão, escopo do payload e riscos.
- `openspec/changes/defer-chat-writes-until-jev/tasks.md` — marca as partes implementadas e mantém pendentes linha de base e regressão final.
- `openspec/changes/defer-chat-writes-until-jev/specs/chat-write-authorization/spec.md` — atualiza o contrato delta, incluindo o retorno do fallback como evidência.
- `openspec/specs/.gitkeep` — removido porque o diretório agora contém a spec principal.

As rotas e o contrato público da API não foram modificados.
