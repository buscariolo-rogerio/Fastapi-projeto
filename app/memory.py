"""Persistência e recuperação da memória de longo prazo do assessor.

O checkpointer do LangGraph mantém a conversa atual em memória de processo.
Este módulo mantém as sessões no MongoDB e Qdrant para que uma conversa encerrada
possa ser resumida e consultada em conversas posteriores. Tratamento de exceções
e fallback em memória garantem resiliência contra falhas de conexão de BD.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient
from qdrant_client import models

from app.config import MONGODB_DB, MONGODB_URI
from app.llms import llm_rapido
from app.vectorstore import COLLECTION_MEMORIA, gerar_embedding, qdrant

logger = logging.getLogger(__name__)

# Tenta certifi para SSL seguro se disponível
_mongo_kwargs = {
    "serverSelectionTimeoutMS": 3000,
    "connectTimeoutMS": 3000,
    "tlsAllowInvalidCertificates": True,
}
try:
    import certifi
    _mongo_kwargs["tlsCAFile"] = certifi.where()
except Exception:
    pass

try:
    _mongo = MongoClient(MONGODB_URI, **_mongo_kwargs)
    db = _mongo[MONGODB_DB]
    col_sessoes = db["sessoes"]
except Exception as _e:
    logger.warning("Falha ao conectar ao MongoDB Atlas no startup: %s", _e)
    _mongo = None
    db = None
    col_sessoes = None

# Cache e fallback em memória para evitar indisponibilidade do serviço caso o DB falhe
_sessoes_ativas: dict[str, str] = {}
_mensagens_memoria: dict[str, list[dict]] = {}
_resumos_memoria: dict[str, dict] = {}

_llm_resumo = llm_rapido

_PROMPT_RESUMO = """\
Você é um assistente que resume conversas de assessoria financeira e agenda.
Gere um resumo conciso em 2-4 frases capturando:
- O que o usuário fez (transações registradas, eventos agendados)
- O que o usuário perguntou
- Informações relevantes mencionadas (valores, datas, categorias)

Responda APENAS com o resumo, sem introdução ou explicação.

Conversa:
{conversa}
"""

_TOKEN_PII = re.compile(r"\[PII_[A-Z]+_[0-9a-f]+\]", re.IGNORECASE)


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _formatar_conversa(mensagens: list[dict]) -> str:
    """Formata mensagens para o prompt de resumo."""
    return "\n".join(
        f"{mensagem.get('role', 'mensagem')}: {mensagem.get('content', '')}"
        for mensagem in mensagens
    )


def _limpar_tokens_pii(texto: str) -> str:
    """Evita salvar no resumo tokens que não poderão ser desanonimizados."""
    return _TOKEN_PII.sub("[DADO PESSOAL OMITIDO]", texto)


def _gerar_resumo(mensagens: list[dict]) -> str:
    """Gera e higieniza o resumo da sessão."""
    try:
        resumo = _llm_resumo.invoke(
            _PROMPT_RESUMO.format(conversa=_formatar_conversa(mensagens))
        ).content.strip()
        return _limpar_tokens_pii(resumo)
    except Exception as e:
        logger.warning("Falha ao gerar resumo via LLM: %s", e)
        return "Sessão encerrada com mensagens registradas."


def _doc_id_da_sessao(session_id: str, user_id: str | None = None) -> str | None:
    """Encontra a sessão em andamento no cache ou MongoDB."""
    doc_id = _sessoes_ativas.get(session_id)
    if doc_id:
        if not user_id:
            return doc_id
        if col_sessoes is not None:
            try:
                doc = col_sessoes.find_one({"_id": doc_id, "user_id": user_id}, {"_id": 1})
                if doc:
                    return doc_id
            except Exception as e:
                logger.warning("Falha na consulta Mongo (_doc_id_da_sessao): %s", e)
                return doc_id
        return doc_id

    if col_sessoes is not None:
        try:
            filtro = {
                "session_id": session_id,
                "resumo": {"$in": ["", None]},
            }
            if user_id:
                filtro["user_id"] = user_id
            doc = col_sessoes.find_one(
                filtro,
                {"_id": 1},
                sort=[("iniciada_em", -1)],
            )
            if doc:
                _sessoes_ativas[session_id] = str(doc["_id"])
                return str(doc["_id"])
        except Exception as e:
            logger.warning("Falha na consulta de sessão no MongoDB: %s", e)

    return _sessoes_ativas.get(session_id)


def iniciar_sessao(session_id: str, user_id: str = "usuario_teste") -> None:
    """Garante um documento aberto para ``session_id``."""
    doc_id_existente = _doc_id_da_sessao(session_id, user_id=user_id)
    if doc_id_existente:
        return

    doc_id = str(uuid.uuid4())
    agora = _agora()
    _sessoes_ativas[session_id] = doc_id
    _mensagens_memoria[doc_id] = []

    if col_sessoes is not None:
        try:
            col_sessoes.insert_one(
                {
                    "_id": doc_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "iniciada_em": agora,
                    "atualizada_em": agora,
                    "resumo": "",
                    "mensagens": [],
                }
            )
        except Exception as e:
            logger.warning("Falha ao criar sessão no MongoDB (fallback ativo): %s", e)


def salvar_mensagem(
    session_id: str,
    role: str,
    content: str,
    user_id: str = "usuario_teste",
) -> None:
    """Adiciona uma mensagem à sessão aberta, usando fallback local se o Mongo falhar."""
    try:
        iniciar_sessao(session_id, user_id=user_id)
        doc_id = _doc_id_da_sessao(session_id, user_id=user_id) or _sessoes_ativas.get(session_id)
        if not doc_id:
            doc_id = str(uuid.uuid4())
            _sessoes_ativas[session_id] = doc_id

        # Salva sempre no fallback em memória
        if doc_id not in _mensagens_memoria:
            _mensagens_memoria[doc_id] = []
        _mensagens_memoria[doc_id].append({"role": role, "content": content})

        if col_sessoes is not None:
            try:
                col_sessoes.update_one(
                    {"_id": doc_id},
                    {
                        "$push": {"mensagens": {"role": role, "content": content}},
                        "$set": {"atualizada_em": _agora()},
                    },
                )
            except Exception as e:
                logger.warning("Falha ao salvar mensagem no MongoDB (salva em memória): %s", e)
    except Exception as e:
        logger.warning("Falha geral em salvar_mensagem: %s", e)


def _data_para_payload(valor) -> str:
    """Converte datetime do Mongo ou texto para ISO format."""
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return str(valor)


def encerrar_sessao(session_id: str, user_id: str | None = None) -> str:
    """Resume e fecha a sessão aberta; retorna vazio se nada houver para fechar."""
    doc_id = _doc_id_da_sessao(session_id, user_id=user_id) or _sessoes_ativas.get(session_id)
    if not doc_id:
        return ""

    mensagens = []
    doc = None
    if col_sessoes is not None:
        try:
            doc = col_sessoes.find_one({"_id": doc_id})
        except Exception as e:
            logger.warning("Falha ao buscar sessão no Mongo durante encerramento: %s", e)

    if doc and doc.get("mensagens"):
        mensagens = doc["mensagens"]
    else:
        mensagens = _mensagens_memoria.get(doc_id, [])

    if not mensagens:
        _sessoes_ativas.pop(session_id, None)
        return ""

    resumo = _gerar_resumo(mensagens)
    agora_iso = _agora().isoformat()

    if col_sessoes is not None:
        try:
            col_sessoes.update_one(
                {"_id": doc_id},
                {"$set": {"resumo": resumo, "atualizada_em": _agora()}},
            )
        except Exception as e:
            logger.warning("Falha ao atualizar resumo no MongoDB: %s", e)

    effective_user_id = (doc.get("user_id") if doc else None) or user_id or "usuario_teste"
    _resumos_memoria[doc_id] = {
        "user_id": effective_user_id,
        "session_id": session_id,
        "resumo": resumo,
        "iniciada_em": agora_iso,
    }

    try:
        vetor = gerar_embedding(resumo)
        qdrant.upsert(
            collection_name=COLLECTION_MEMORIA,
            points=[
                models.PointStruct(
                    id=doc_id,
                    vector=vetor,
                    payload={
                        "user_id": effective_user_id,
                        "session_id": session_id,
                        "resumo": resumo,
                        "iniciada_em": _data_para_payload(doc.get("iniciada_em") if doc else agora_iso),
                    },
                )
            ],
        )
    except Exception as e:
        logger.warning("Falha ao salvar vetor no Qdrant: %s", e)

    _sessoes_ativas.pop(session_id, None)
    return resumo


def recuperar_historico(user_id: str, busca: str = "", limite: int = 3) -> list[dict]:
    """Recupera históricos de sessões anteriores no Qdrant, Mongo ou fallback em memória."""
    if busca:
        try:
            vetor = gerar_embedding(busca)
            resultados = qdrant.query_points(
                collection_name=COLLECTION_MEMORIA,
                query=vetor,
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=user_id),
                        )
                    ]
                ),
                limit=limite,
            )
            if resultados and resultados.points:
                encontrados = []
                for ponto in resultados.points:
                    payload = ponto.payload or {}
                    res = payload.get("resumo")
                    if res:
                        encontrados.append(
                            {
                                "doc_id": ponto.id,
                                "iniciada_em": payload.get("iniciada_em", ""),
                                "resumo": res,
                            }
                        )
                if encontrados:
                    return encontrados
        except Exception as e:
            logger.warning("Falha na consulta ao Qdrant: %s", e)

    if col_sessoes is not None:
        try:
            filtro = {"user_id": user_id, "resumo": {"$nin": ["", None]}}
            docs = (
                col_sessoes
                .find(filtro, {"resumo": 1, "iniciada_em": 1})
                .sort("iniciada_em", -1)
                .limit(limite)
            )
            res = [
                {"doc_id": d["_id"], "iniciada_em": _data_para_payload(d.get("iniciada_em", "")), "resumo": d["resumo"]}
                for d in docs
            ]
            if res:
                return res
        except Exception as e:
            logger.warning("Falha na consulta de histórico no MongoDB: %s", e)

    # Fallback em memória se Mongo/Qdrant falharem
    res_memoria = []
    for d_id, dados in _resumos_memoria.items():
        if dados.get("user_id") == user_id and dados.get("resumo"):
            res_memoria.append({
                "doc_id": d_id,
                "iniciada_em": dados.get("iniciada_em", ""),
                "resumo": dados["resumo"],
            })
    return res_memoria[:limite]


def recuperar_mensagens(doc_id: str) -> list[dict]:
    """Recupera mensagens de uma sessão específica no Mongo ou fallback em memória."""
    if col_sessoes is not None:
        try:
            doc = col_sessoes.find_one({"_id": doc_id}, {"mensagens": 1})
            if doc and "mensagens" in doc:
                return doc["mensagens"]
        except Exception as e:
            logger.warning("Falha ao recuperar mensagens no Mongo: %s", e)

    return _mensagens_memoria.get(doc_id, [])


def salvar_turno(
    session_id: str,
    pergunta: str,
    resposta: str,
    user_id: str | None = None,
) -> None:
    """Compatibilidade para consumidores antigos."""
    effective_user_id = user_id or "usuario_teste"
    salvar_mensagem(session_id, "user", pergunta, user_id=effective_user_id)
    salvar_mensagem(session_id, "assistant", resposta, user_id=effective_user_id)
