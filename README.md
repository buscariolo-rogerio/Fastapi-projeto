# Assessor.AI

Assistente financeiro com FastAPI, LangGraph, LangChain, PostgreSQL, MongoDB e Qdrant.

O sistema conversa com o usuário, consulta e registra transações financeiras, responde dúvidas com base em um FAQ e recupera informações relevantes de sessões anteriores.

## Principais recursos

- Roteamento entre agentes especializados.
- Consulta, cadastro, atualização e cálculo de saldo financeiro.
- Busca semântica no FAQ em PDF.
- Histórico de curto prazo por sessão.
- Resumos de sessões encerradas armazenados no MongoDB e indexados no Qdrant.
- Interface web incluída em `frontend/`.
- Conversão automática de transações em moeda estrangeira para BRL antes da gravação.
- Juiz de fidelidade JEV depois da resposta final, com bloqueio de fatos sem evidência.

## Serviços utilizados

- PostgreSQL: dados financeiros.
- MongoDB: mensagens e resumos das sessões.
- Qdrant: embeddings do FAQ e do histórico.
- Gemini e Groq: modelos de linguagem e embeddings.
- TypeSafe JEV: decisão de fidelidade entre resposta e evidências do turno.

Collections do Qdrant:

- `faq_chunks`
- `memoria_resumos`

## Requisitos

- Python 3.10 ou superior.
- PostgreSQL, MongoDB e Qdrant configurados.
- Chaves de API do Gemini, Groq, Qdrant e TypeSafe.

## Configuração

Crie um arquivo `.env` na raiz do projeto. O arquivo já é ignorado pelo Git e não deve conter valores publicados no repositório:

```env
GEMINI_API_KEY=<sua-chave>
GROQ_API_KEY=<sua-chave>
TYPESAFE_API_KEY=<sua-chave-typesafe>
DATABASE_URL=<sua-connection-string-do-postgresql>
MONGODB_URI=<sua-url-do-mongodb>
MONGODB_DB=assessor
QDRANT_URL=<sua-url-do-qdrant>
QDRANT_API_KEY=<sua-chave>
```

Nunca substitua os placeholders por chaves reais no `README.md` ou em qualquer arquivo versionado.

`TYPESAFE_API_KEY` habilita o juiz de fidelidade JEV. Para julgar a resposta,
o sistema envia à TypeSafe apenas o turno atual: pergunta, resposta final e
evidências das ferramentas, limitadas aos últimos 6.000 caracteres. Sem essa
chave, ou se o serviço estiver indisponível, o juiz falha de forma fechada e
substitui a resposta por `Não tenho evidência para afirmar isso.`

## Instalação e execução

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi "uvicorn[standard]" pydantic python-dotenv psycopg2-binary pymongo qdrant-client langchain langgraph langchain-google-genai langchain-groq langchain-community langchain-text-splitters pypdf "mcp[cli]"
uvicorn app.main:app --reload
```

Com o servidor em execução:

- Aplicação: http://localhost:8000
- Documentação da API: http://localhost:8000/docs
- Verificação de configuração: `GET /health`

## Servidor MCP financeiro

O projeto também expõe as ferramentas financeiras por MCP via `stdio`. O Cursor
usa a configuração em `.cursor/mcp.json` para iniciar `app/mcp_server.py` com o
Python do ambiente virtual do projeto.

Depois de instalar as dependências, recarregue a janela do Cursor e abra o chat
em modo Agent. O servidor `assessor-financeiro` deve anunciar cinco ferramentas:

- `query_transactions`
- `total_balance`
- `daily_balance`
- `add_transaction`
- `update_transaction`

O PostgreSQL e a variável `DATABASE_URL` no `.env` precisam estar disponíveis
para executar consultas e alterações financeiras.

## Indexação do FAQ

Depois de configurar o Qdrant, indexe o PDF oficial:

```powershell
python -m app.ingest_faq
```

O comando gera embeddings e atualiza a collection `faq_chunks`. Execute-o novamente quando o PDF for alterado.

## Uso da API

### Moedas estrangeiras

Ao registrar uma transação como `gastei 5 dólares com café`, o sistema identifica
a moeda, consulta a cotação de referência mais recente na API pública
[Frankfurter](https://frankfurter.dev/) e salva `amount` já convertido em reais.
Nomes comuns (dólar, euro, libra, iene etc.), símbolos e códigos ISO 4217 como
`USD`, `EUR`, `GBP`, `JPY` e `NOK` são aceitos. O texto original permanece em
`source_text` para auditoria.

Se a moeda não for reconhecida ou a cotação estiver indisponível, a transação
não é gravada. As cotações ficam em cache por seis horas; são taxas de referência
diárias e podem diferir da taxa efetivamente cobrada por cartão, banco ou casa de câmbio.

### Conversa

`POST /chat`

```json
{
  "user_id": "usuario-exemplo",
  "session_id": "sessao-exemplo",
  "pergunta": "Qual é o meu saldo total?"
}
```

### Encerrar uma sessão

`POST /sessions/{session_id}/encerrar`

```json
{
  "user_id": "usuario-exemplo"
}
```

O encerramento gera o resumo que será usado pela memória semântica em conversas futuras. O mesmo `user_id` deve ser mantido entre sessões do mesmo usuário; o `session_id` deve mudar a cada nova conversa.

## Teste rápido da memória

1. Envie uma mensagem sobre um plano ou preferência usando um `session_id`.
2. Encerre essa sessão pela rota acima.
3. Use outro `session_id`, mantendo o mesmo `user_id`.
4. Pergunte sobre o assunto anterior.

O histórico só fica disponível para busca semântica depois que a sessão é encerrada e resumida.

## Agenda e Google Calendar

O agente de agenda usa três tools:

- `query_events`: consulta a tabela `events` no PostgreSQL;
- `add_event`: grava o evento nessa tabela;
- `add_google_event`: tenta criar o mesmo evento no MCP oficial do Google
  Calendar e usa a Calendar API como fallback quando o serviço MCP estiver
  indisponível no projeto Google Cloud.

O arquivo OAuth `gcp-oauth.keys.json` deve ficar na raiz e nunca deve ser
versionado. Para autorizar sua conta Google uma única vez, com o ambiente
virtual ativado, execute:

```powershell
python -c "from app.tools.calendario_google import autenticar; print(autenticar())"
```

Escolha a conta no navegador e permita o acesso ao Calendar. O token será salvo
em `google-calendar.token.json`, também ignorado pelo Git. Depois, reinicie o
Uvicorn e peça no chat, por exemplo: `marque dentista amanhã às 14h por uma hora`.

Para verificar apenas o catálogo público do MCP, sem ler nem alterar eventos:

```powershell
python -c "from app.tools.calendario_google import listar_tools_google; print([t['name'] for t in listar_tools_google()['result']['tools']])"
```

## Observabilidade

Com a API em execução, abra `http://127.0.0.1:8000/monitor.html`. O painel é
atualizado a cada quatro segundos e mostra, para os últimos 50 turnos do
processo:

- latência média e p95, taxa de erro, bloqueios e estado do SLO;
- tokens de entrada e saída, custo estimado e modelo mais usado;
- fallbacks dos especialistas, métricas por rota e por modelo;
- séries de latência e custo acumulado.

As métricas ficam somente em memória e são zeradas quando o servidor reinicia.
Prompts, respostas, chaves e tokens não são armazenados pelo coletor.

Para habilitar traces externos no LangSmith, copie as variáveis da seção
LangSmith de `.env.example` para o seu `.env` local e substitua somente
`LANGSMITH_API_KEY` pela chave real. Nunca versione nem envie esse `.env`.

## Segurança

- Não versione `.env`, chaves, senhas ou connection strings reais.
- Revise o `git diff` antes de publicar alterações.
- A configuração de CORS atual é voltada para desenvolvimento; restrinja as origens antes de usar em produção.

## Status

Projeto acadêmico em desenvolvimento.
