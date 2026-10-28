"""Leitura de perfil para o especialista financeiro; nenhuma tool de escrita."""

import logging
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from app.perfil import consultar_perfil

logger = logging.getLogger(__name__)


@tool
def buscar_perfil(busca: str, config: RunnableConfig) -> dict:
    """Consulta o cadastro atual antes de aconselhar sobre poupança, metas ou investimentos.

    busca: assunto ou pergunta para recuperar preferências por semelhança semântica.
    Não cadastra nem altera perfil. A identidade vem exclusivamente da requisição.
    """
    user_id = (config or {}).get("configurable", {}).get("user_id")
    if not user_id:
        return {"status": "sem_usuario", "mensagem": "Não foi possível identificar o usuário. Não presuma dados de perfil."}
    try:
        return consultar_perfil(user_id, busca)
    except Exception:
        logger.exception("Falha ao consultar perfil")
        return {"status": "indisponivel", "mensagem": "Não foi possível consultar o perfil agora. Não invente dados nem dê conselho personalizado sem o cadastro."}
