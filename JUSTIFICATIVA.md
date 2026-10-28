# Perfil financeiro do usuário

## Quais arquivos foram criados ou modificados?
Criados: `app/perfil.py`, `app/routes/perfil.py`, `app/tools/perfil.py` e `JUSTIFICATIVA.md`. Modificados para integração: `app/main.py`, `app/graph.py` e `app/prompts.py`. Os três arquivos de perfil fornecidos pelo professor e o frontend do chat foram preservados.

Na validação real, `app/guardrail.py` recebeu uma correção pontual de extração: o protocolo de resposta só é aceito fora das tags de raciocínio, evitando exibir a análise interna do revisor no conselho.

## Por onde o perfil entra e onde cada parte é gravada?
Entra pelo formulário via `POST /perfil`, validado por Pydantic. MongoDB guarda o cadastro em `perfis_financeiros` com `_id=user_id`; Qdrant guarda vetor e texto livre em `perfil_preferencias`. O POST só confirma sucesso após ambas as gravações.

## Como as preferências são guardadas e consultadas e por que não é busca por palavra?
O POST gera um embedding de 768 dimensões e a consulta compara o vetor da pergunta por similaridade de cosseno. Um ponto estável por usuário substitui o anterior; a busca filtra usuário e versão, sem procurar palavras do texto.

## Uma tool ou duas? Por quê?
Uma, `buscar_perfil`, reúne a consulta estruturada e as preferências semânticas para o especialista receber o contexto necessário ao conselho. É exclusivamente de leitura.

## O que impede o perfil de um usuário de aparecer para outro?
O `user_id` vem de `RunnableConfig` e não é argumento disponível ao modelo. Mongo filtra pelo identificador e Qdrant pelo mesmo usuário; não existe fallback para outro cadastro. Autenticação está fora do escopo.

## A tool consulta o banco ou chama a própria API? Por quê?
Consulta os bancos por função interna, evitando HTTP desnecessário e uma rota pública de leitura, que o enunciado não permite.

## Por que não existe um agente perfil?
O perfil é contexto do especialista financeiro existente. Apenas uma tool e instruções foram acrescentadas, sem um novo domínio ou mudanças nos agentes de agenda e FAQ.

## Por que o chat não altera o cadastro?
Só a rota HTTP chama `salvar_perfil`; nenhuma tool de escrita de perfil é exposta. As instruções orientam usar a tela e pedidos explícitos de mudança de cadastro são interceptados antes das tools de transação e do fallback de inserção.

## Identificador do formulário fornecido
O `perfil.js` fornecido envia `usuario_teste`; o chat existente gera um identificador no navegador. Os arquivos foram preservados conforme solicitado. Para testar o mesmo cadastro em `/docs`, envie `user_id: "usuario_teste"` no `POST /chat`, mantendo uma sessão própria desse usuário.

Para usar a tela do chat com esse cadastro, execute uma vez no console do navegador em `http://localhost:8000/`. O comando ajusta somente o armazenamento desse navegador e inicia uma nova sessão:

```javascript
localStorage.setItem('assistente_user_id', 'usuario_teste');
localStorage.removeItem('assistente_session_id');
location.reload();
```

## Atualizações e falhas parciais
Mongo faz replace/upsert pela chave única e Qdrant faz upsert com UUID determinístico por usuário. Não há transação distribuída: falhas retornam 503 e devem ser repetidas; o filtro de versão impede usar preferências incompatíveis com o cadastro atual.

## Verificação realizada
Sete testes isolados passaram com FastAPI/TestClient, Mongo simulado e Qdrant local: contrato HTTP, entradas inválidas, preferências vazias, atualização/isolamento, identidade da tool, falha parcial com repetição e bloqueio de edição pelo chat. Embeddings foram simulados nesses testes; eles não comprovam disponibilidade dos provedores externos.
