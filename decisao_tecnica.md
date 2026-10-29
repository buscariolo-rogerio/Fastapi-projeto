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

## Alternativas avaliadas e não escolhidas

Só **Transacionar** foi implementado. As demais famílias foram avaliadas, mas não coexistem no código:

- **Interceptar antes da tool:** o JEV avaliaria a intenção antes de qualquer SQL. A vantagem seria não manter conexão e locks abertos durante a chamada externa. O custo é julgar uma ação ainda sem o retorno real do banco — ID, valor convertido, linha localizada e erros SQL ainda não existem — e depois precisar de outra verificação para a frase final. Ficou de fora porque enfraquece justamente a evidência usada pelo juiz.
- **Adiar como operação preparada:** cada tool devolveria um comando ou objeto de mudança, e outra camada executaria o SQL após aprovação. A vantagem é não segurar transação durante o JEV. O custo é duplicar o contrato de cada escrita e reimplementar resolução de tipo, categoria, linha-alvo e retorno real fora das tools atuais. Ficou de fora porque exigiria refatoração maior e mudaria a responsabilidade das três tools.
- **Compensar depois do commit:** a escrita seria confirmada imediatamente e uma operação inversa tentaria desfazê-la na recusa. A vantagem é conexão curta. O custo é haver uma janela em que o dado recusado fica visível, além de exigir guardar o estado anterior completo de `update_transaction`; a compensação também pode falhar. Ficou de fora porque não garante a invariante com a mesma força de um rollback PostgreSQL.

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

Na solução escolhida, o JEV já existia e não há uma chamada de modelo adicional: tokens e custo do turno permanecem os das chamadas já presentes. A latência medida passa a incluir o finalizador e o tempo de `commit`/`rollback`; o principal risco ao SLO de p95 de 8 s é manter a conexão/lock enquanto o JEV responde. Aprovação confirmada é `ok`, recusa do JEV é `bloqueado`, e timeout, erro do JEV, falha SQL ou resultado incerto é `erro`; bloqueio do guardrail de entrada continua fora da taxa de erro.

Na alternativa de **Compensar**, haveria um segundo efeito SQL após a recusa. Isso aumentaria a latência dos turnos recusados e poderia elevar a taxa de erro se a compensação falhasse; tokens e custo de modelo não mudariam, mas o turno só poderia ser marcado como `bloqueado` depois de a compensação ser confirmada. Se ela falhasse, o status correto seria `erro`, não `bloqueado`.

O Mongo mantém a pergunta anonimizada registrada na entrada e salva a resposta exibida somente depois da finalização. Essa escrita não participa da transação PostgreSQL; uma falha ao salvar a resposta é registrada sem induzir repetição de uma operação já confirmada. O Google Calendar ficou fora do chat porque não há transação distribuída entre Google e PostgreSQL; `add_google_event` continua disponível apenas fora desse fluxo automático.

Se o processo cair antes do finalizador, o fechamento da conexão pelo processo/PostgreSQL desfaz a transação não confirmada; se cair durante o `commit`, o resultado permanece incerto e exige consulta antes de repetir. Uma repetição HTTP é tratada como um novo pedido porque `/chat` ainda não recebe chave de idempotência.

## Evidência reproduzível

A linha de base está no commit `eceb2c3`: `add_transaction`, `update_transaction` e `add_event` continham `commit()` dentro das tools, e o grafo ligava `juiz` diretamente ao fim. A implementação atual é comparada a essa revisão pelo histórico Git.

`tests/test_turn_transaction.py` usa marcadores únicos (`APROVA-*`, `RECUSA-*`, `ERRO-SQL-*`, `POS-SQL-*` e `CONCORRENTE-*`) e conexões simuladas. Para cada uma das três tools, prova que antes do veredito há zero commits; aprovação confirma exatamente uma vez; recusa, erro SQL e exceção depois do SQL executam rollback e nenhum commit. O mesmo arquivo cobre finalização duplicada e isolamento entre turnos concorrentes. `tests/test_juiz.py` cobre timeout do JEV como veredito `falha`, que segue para rollback.

## Validação

- `python -m pytest -q`: **26 testes passaram**, com **20 subtestes**, incluindo as três escritas, recusa, aprovação, erros, timeout, repetição e concorrência.
- A linha de base anterior à solução permanece identificável pelo commit `eceb2c3`.
- Teste integrado posterior no PostgreSQL configurado: uma inserção recusada pelo JEV real gerou ID provisório e terminou em rollback; a consulta por esse ID retornou zero linhas.
- Teste integrado posterior de aprovação pelo JEV real: inserção confirmada por commit, com valor, descrição, categoria e texto de origem conferidos pelo ID e marcador exclusivos. A linha de teste foi removida em seguida.
- No caminho de lançamento cotidiano em reais, o monitor mediu 3,96 s no turno aprovado (limite de p95: 8 s). Esta amostra isolada não garante o SLO sob carga ou com falhas externas.
- O MongoDB Atlas apresentou falha de resolução DNS no ambiente de teste; a memória persistente não foi validada nessa execução.

## Arquivos criados

- `app/turn_transaction.py` — gestor da conexão e da decisão final de commit ou rollback por turno.
- `tests/test_turn_transaction.py` — testes com conexão simulada para o ciclo transacional.
- `decisao_tecnica.md` — este registro das decisões, falhas, limites e arquivos.

## Arquivos modificados

- `app/graph.py` — passa o contexto transacional às tools, registra evidência autorizada de `add_transaction`, condiciona commit ou rollback pelo veredito do juiz, limpa evidência provisória e preserva a resposta confirmada se o histórico Mongo falhar.
- `app/juiz.py` — coleta a evidência estruturada de `add_transaction` e grava no estado o veredito aprovado, recusado ou falha.
- `app/observabilidade.py` — classifica status por veredito e resultado transacional.
- `app/prompts.py` — remove promessas de sincronização com Google Calendar no chat.
- `app/tools/agenda.py` — executa `add_event` na transação do chat, mantém commit próprio fora do chat e compartilha a conexão nas leituras do turno.
- `app/tools/financeiro.py` — adapta `add_transaction` e `update_transaction`, compartilha a conexão de leitura e mantém commit próprio fora do chat.
- `.gitignore` — permite versionar os testes e as especificações do projeto, mantendo caches e relatórios gerados ignorados.
- `tests/test_juiz.py` — prova que timeout do JEV falha fechado e produz veredito de falha.
- `openspec/changes/defer-chat-writes-until-jev/proposal.md` — descreve a mudança para Transacionar e o envio autorizado da evidência de `add_transaction` ao JEV.
- `openspec/changes/defer-chat-writes-until-jev/design.md` — registra propriedade da conexão, fluxo de decisão, escopo do payload e riscos.
- `openspec/changes/defer-chat-writes-until-jev/tasks.md` — marca as partes implementadas e mantém pendentes linha de base e regressão final.
- `openspec/changes/defer-chat-writes-until-jev/specs/chat-write-authorization/spec.md` — atualiza o contrato delta, incluindo o retorno do fallback como evidência.

As rotas e o contrato público da API não foram modificados.
