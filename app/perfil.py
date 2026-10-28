import hashlib
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field
from pymongo import MongoClient
from qdrant_client import models

from app.config import MONGODB_DB, MONGODB_URI
from app.vectorstore import EMBEDDING_DIM, gerar_embedding, qdrant


class Perfil(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(min_length=1)
    renda_mensal: float = Field(gt=0, allow_inf_nan=False, strict=True)
    objetivo: str = Field(min_length=1)
    tolerancia_risco: Literal["baixa", "media", "alta"]
    preferencias: str | None = None


_mongo = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, connect=False)
col_perfis = _mongo[MONGODB_DB]["perfis_financeiros"]
COLLECTION_PERFIL = "perfil_preferencias"


def _versao(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _preparar_indice() -> None:
    if not qdrant.collection_exists(COLLECTION_PERFIL):
        try:
            qdrant.create_collection(
                collection_name=COLLECTION_PERFIL,
                vectors_config=models.VectorParams(size=EMBEDDING_DIM, distance=models.Distance.COSINE),
            )
        except Exception:
            # Outra requisição pode ter criado a collection simultaneamente.
            if not qdrant.collection_exists(COLLECTION_PERFIL):
                raise
    indices = qdrant.get_collection(COLLECTION_PERFIL).payload_schema or {}
    for campo in ("user_id", "versao"):
        if campo in indices:
            continue
        qdrant.create_payload_index(
            collection_name=COLLECTION_PERFIL,
            field_name=campo,
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )


def salvar_perfil(perfil: Perfil) -> Perfil:
    dados = perfil.model_dump()
    versao = _versao(perfil.preferencias or "")
    vetor = gerar_embedding(perfil.preferencias or "Sem preferências informadas.")
    _preparar_indice()
    # Um único ponto por usuário: upsert substitui também o texto anterior.
    qdrant.upsert(
        collection_name=COLLECTION_PERFIL,
        points=[models.PointStruct(
            id=str(uuid5(NAMESPACE_URL, "assessor:perfil:" + perfil.user_id)),
            vector=vetor,
            payload={"user_id": perfil.user_id, "versao": versao,
                     "preferencias": perfil.preferencias},
        )],
        wait=True,
    )
    # _id garante unicidade no Mongo, inclusive em salvamentos concorrentes.
    col_perfis.replace_one(
        {"_id": perfil.user_id}, {"_id": perfil.user_id, **dados}, upsert=True,
    )
    return perfil


def consultar_perfil(user_id: str, busca: str) -> dict:
    doc = col_perfis.find_one({"_id": user_id})
    if not doc:
        return {"status": "nao_cadastrado", "mensagem": "Cadastre seu perfil na tela Perfil (/perfil.html). Não presuma renda, objetivo ou risco."}

    resultado = {"status": "ok", "renda_mensal": doc["renda_mensal"],
                 "objetivo": doc["objetivo"], "tolerancia_risco": doc["tolerancia_risco"]}
    if not doc["preferencias"]:
        return {**resultado, "preferencias": ""}
    pontos = qdrant.query_points(
        collection_name=COLLECTION_PERFIL,
        query=gerar_embedding(busca),
        query_filter=models.Filter(must=[
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
            models.FieldCondition(key="versao", match=models.MatchValue(value=_versao(doc["preferencias"]))),
        ]),
        limit=1,
    ).points
    if not pontos:
        # Não usar versões antigas em caso de falha parcial entre os bancos.
        return {**resultado, "status": "indice_indisponivel", "mensagem": "Preferências indisponíveis. Salve novamente na tela Perfil antes de receber conselho personalizado."}
    return {**resultado, "preferencias": pontos[0].payload["preferencias"]}
