import sys
from pathlib import Path
from typing import Annotated, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from app.tools.financeiro import (
    add_transaction as _add_transaction,
    saldo_diario as _daily_balance,
    saldo_total as _total_balance,
    search_transactions as _query_transactions,
    update_transaction as _update_transaction,
)

mcp = MCPServer(
    name="assessor-financeiro",
    version="1.0.0",
    instructions=(
        "Ferramentas financeiras do Assessor.AI. Use as ferramentas de leitura "
        "para consultar dados e as de escrita somente quando o usuário pedir "
        "explicitamente para registrar ou atualizar uma transação."
    ),
)

LEITURA = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
ESCRITA = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
)


@mcp.tool(
    name="total_balance",
    title="Saldo total",
    description="Calcula o saldo de todo o histórico: receitas menos despesas.",
    annotations=LEITURA,
)
def total_balance() -> dict[str, Any]:
    """Expõe a consulta de saldo total sem repetir a regra financeira."""
    return _total_balance.invoke({})


@mcp.tool(
    name="daily_balance",
    title="Saldo por dia ou período",
    description=(
        "Calcula receitas, despesas e saldo de um dia ou intervalo de datas "
        "no fuso America/Sao_Paulo."
    ),
    annotations=LEITURA,
)
def daily_balance(
    first_date: Annotated[
        str,
        Field(description="Data inicial em YYYY-MM-DD ou DD/MM/YYYY."),
    ],
    last_date: Annotated[
        Optional[str],
        Field(description="Data final inclusiva; omita para consultar apenas um dia."),
    ] = None,
) -> dict[str, Any]:
    """Encaminha as datas para a tool que normaliza o período e calcula o saldo."""
    return _daily_balance.invoke({
        "first_date": first_date,
        "last_date": last_date,
    })


@mcp.tool(
    name="query_transactions",
    title="Consultar transações",
    description=(
        "Lista transações por texto, período, tipo, categoria ou forma de pagamento."
    ),
    annotations=LEITURA,
)
def query_transactions(
    query: Annotated[
        Optional[str],
        Field(description="Texto parcial da origem ou descrição."),
    ] = None,
    start_date: Annotated[
        Optional[str],
        Field(description="Data inicial inclusiva em YYYY-MM-DD ou DD/MM/YYYY."),
    ] = None,
    end_date: Annotated[
        Optional[str],
        Field(description="Data final inclusiva em YYYY-MM-DD ou DD/MM/YYYY."),
    ] = None,
    type_name: Annotated[
        Optional[str],
        Field(description="INCOME | EXPENSES | TRANSFER."),
    ] = None,
    category_name: Annotated[
        Optional[str],
        Field(description="Nome da categoria em pt-BR."),
    ] = None,
    payment_method: Annotated[
        Optional[str],
        Field(description="Forma de pagamento."),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Quantidade máxima de resultados.", ge=1, le=100),
    ] = 10,
) -> dict[str, Any]:
    """Reaproveita os filtros, validações e SQL da busca já existente."""
    return _query_transactions.invoke({
        "query": query,
        "start_date": start_date,
        "end_date": end_date,
        "type_name": type_name,
        "category_name": category_name,
        "payment_method": payment_method,
        "limit": limit,
    })


@mcp.tool(
    name="add_transaction",
    title="Registrar transação",
    description="Registra uma transação na tabela transactions.",
    annotations=ESCRITA,
)
def add_transaction(
    amount: Annotated[float, Field(description="Valor original, antes da conversão, sempre positivo.", gt=0)],
    source_text: Annotated[str, Field(description="Texto original do usuário.", min_length=1)],
    currency: Annotated[
        Optional[str],
        Field(description="Código ISO da moeda original, como BRL, USD, EUR ou GBP; se ausente, é inferido do texto."),
    ] = None,
    occurred_at: Annotated[
        Optional[str],
        Field(description="Timestamp ISO 8601; se ausente, o banco usa o horário atual."),
    ] = None,
    type_name: Annotated[
        Optional[str],
        Field(description="INCOME | EXPENSES | TRANSFER."),
    ] = None,
    category_name: Annotated[
        Optional[str],
        Field(description="Nome da categoria em pt-BR."),
    ] = None,
    description: Annotated[
        Optional[str],
        Field(description="Descrição da transação."),
    ] = None,
    payment_method: Annotated[
        Optional[str],
        Field(description="Forma de pagamento."),
    ] = None,
) -> dict[str, Any]:
    """Valida o contrato MCP e delega a gravação à implementação existente."""
    return _add_transaction.invoke({
        "amount": amount,
        "source_text": source_text,
        "currency": currency,
        "occurred_at": occurred_at,
        "type_name": type_name,
        "category_name": category_name,
        "description": description,
        "payment_method": payment_method,
    })


@mcp.tool(
    name="update_transaction",
    title="Atualizar transação",
    description=(
        "Atualiza uma transação pelo ID ou pela combinação de texto e data local."
    ),
    annotations=ESCRITA,
)
def update_transaction(
    id: Annotated[
        Optional[int],
        Field(description="ID da transação a atualizar.", gt=0),
    ] = None,
    match_text: Annotated[
        Optional[str],
        Field(description="Texto para localizar a transação quando o ID não for informado."),
    ] = None,
    date_local: Annotated[
        Optional[str],
        Field(description="Data local em YYYY-MM-DD ou DD/MM/YYYY; usada com match_text."),
    ] = None,
    amount: Annotated[
        Optional[float],
        Field(description="Novo valor; zero representa exclusão lógica.", ge=0),
    ] = None,
    type_name: Annotated[
        Optional[str],
        Field(description="Novo tipo: INCOME | EXPENSES | TRANSFER."),
    ] = None,
    category_name: Annotated[
        Optional[str],
        Field(description="Novo nome de categoria em pt-BR."),
    ] = None,
    description: Annotated[
        Optional[str],
        Field(description="Nova descrição."),
    ] = None,
    payment_method: Annotated[
        Optional[str],
        Field(description="Nova forma de pagamento."),
    ] = None,
    occurred_at: Annotated[
        Optional[str],
        Field(description="Novo timestamp ISO 8601."),
    ] = None,
) -> dict[str, Any]:
    """Mantém no domínio financeiro a seleção segura e a atualização no banco."""
    return _update_transaction.invoke({
        "id": id,
        "match_text": match_text,
        "date_local": date_local,
        "amount": amount,
        "type_name": type_name,
        "category_name": category_name,
        "description": description,
        "payment_method": payment_method,
        "occurred_at": occurred_at,
    })


if __name__ == "__main__":
    from app.config import DATABASE_URL

    if not DATABASE_URL:
        print("[assessor-financeiro] DATABASE_URL ausente no .env", file=sys.stderr)

    mcp.run(transport="stdio")
