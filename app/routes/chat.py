import logging
from fastapi import APIRouter
from app.schemas import ChatRequest, ChatResponse
from app.graph import executar_fluxo_assessor_detalhado

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

@router.post("/chat", response_model=ChatResponse)
def chat(requisition: ChatRequest) -> ChatResponse:
    try:
        result, agentes_chamados = executar_fluxo_assessor_detalhado(
            requisition.pergunta,
            requisition.session_id,
            requisition.user_id
        )

        return ChatResponse(
            resposta=result,
            agentes_chamados=agentes_chamados,
        )
    except Exception as e:
        logger.exception("Erro ao processar mensagem do chat: %s", e)
        return ChatResponse(
            resposta="Desculpe, ocorreu um erro interno ao processar sua mensagem. Por favor, tente novamente.",
            agentes_chamados=["erro_sistema"],
        )

