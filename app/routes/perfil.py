"""Única rota pública de perfil: cadastro/atualização pelo formulário."""

import logging
from fastapi import APIRouter, HTTPException
from app.perfil import Perfil, salvar_perfil

router = APIRouter(tags=["perfil"])
logger = logging.getLogger(__name__)


@router.post("/perfil", response_model=Perfil)
def gravar_perfil(perfil: Perfil) -> Perfil:
    try:
        return salvar_perfil(perfil)
    except Exception:
        logger.exception("Falha ao persistir o perfil")
        raise HTTPException(status_code=503, detail="Não foi possível confirmar o salvamento do perfil. Tente salvar novamente.") from None
