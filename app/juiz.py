"""Juiz de fidelidade executado depois da resposta final do Assessor.AI.

O JEV devolve decisões tipadas; ele não redige a resposta. O código preserva
integralmente uma resposta apoiada ou a substitui por uma frase segura.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from app.config import TYPESAFE_API_KEY
from app.turn_transaction import transaction_for
from langchain_core.runnables import RunnableConfig


logger = logging.getLogger(__name__)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODELO = "jev-latest"
LIMIAR = 0.70
FRASE_SEGURA = "Não tenho evidência para afirmar isso."

REGRAS = (
    "A pergunta do usuário também é evidência do que ele disse que fez. "
    "Somas, contagens, datas convertidas para o horário de Brasília e "
    "reformatação de valores tirados da evidência contam como apoiada. "
    "Esclarecimento, saudação, oferta de ajuda, sugestão de próximo passo e "
    "ausência de registro contam como apoiada. Opinião do usuário pode ser "
    "citada como fala dele. Quando a evidência de add_transaction trouxer "
    "original_amount, original_currency, exchange_rate, rate_date e amount_brl, "
    "a resposta pode apresentar a conversão correspondente para reais. "
    "Quando add_transaction devolver status ok e amount_brl, a frase de que "
    "essa transação foi registrada pelo valor em reais é apoiada pela tool; "
    "o commit final só ocorre depois deste julgamento. Fora "
    "desse caso, a moeda de cada valor é a que aparece no source_text ou na fala "
    "do usuário. Moeda trocada, valores de moedas diferentes somados sem uma "
    "conversão documentada, opinião dita como fato do sistema, número, data, "
    "telefone ou regra que não saiam da evidência contam como sem_apoio."
)


def _tipo(mensagem: Any) -> str:
    if isinstance(mensagem, dict):
        return str(mensagem.get("role") or mensagem.get("type") or "")
    return str(getattr(mensagem, "type", "") or "")


def _texto(mensagem: Any) -> str:
    if isinstance(mensagem, dict):
        conteudo = mensagem.get("content", "")
    else:
        texto = getattr(mensagem, "text", None)
        if isinstance(texto, str) and texto.strip():
            return texto
        conteudo = getattr(mensagem, "content", "")
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        pedacos = []
        for parte in conteudo:
            if isinstance(parte, str):
                pedacos.append(parte)
            elif isinstance(parte, dict):
                pedacos.append(str(parte.get("text") or ""))
            else:
                pedacos.append(str(getattr(parte, "text", "") or ""))
        return "\n".join(pedaco for pedaco in pedacos if pedaco)
    return str(conteudo or "")


def _tool_calls(mensagem: Any) -> list[dict]:
    if isinstance(mensagem, dict):
        chamadas = mensagem.get("tool_calls") or []
    else:
        chamadas = getattr(mensagem, "tool_calls", None) or []
    return [chamada for chamada in chamadas if isinstance(chamada, dict)]


def _coleta(estado: dict) -> tuple[str, str, str]:
    """Coleta somente pergunta, resposta e evidências do turno mais recente."""
    pergunta = ""
    resposta = ""
    evidencias: list[str] = []
    for mensagem in estado.get("messages", []):
        tipo = _tipo(mensagem)
        texto = _texto(mensagem).strip()
        if tipo in ("human", "user") and texto:
            pergunta = texto
            resposta = ""
            evidencias = []
        elif tipo == "tool" and texto:
            nome = (
                mensagem.get("name", "")
                if isinstance(mensagem, dict)
                else getattr(mensagem, "name", "")
            ) or "tool"
            evidencias.append(f"[{nome}]\n{texto}")
        elif tipo in ("ai", "assistant"):
            for chamada in _tool_calls(mensagem):
                argumentos = json.dumps(chamada.get("args", {}), ensure_ascii=False, default=str)
                evidencias.append(f"[chamada {chamada.get('name') or 'tool'}]\n{argumentos}")
            if texto:
                resposta = texto

    # O nó financeiro normaliza a resposta do especialista e não conserva
    # seus ToolMessages internos no histórico do grafo. O resultado real de
    # add_transaction é transportado separadamente no estado deste turno.
    for evidencia_tool in estado.get("evidencias_tools", []):
        if not isinstance(evidencia_tool, dict):
            continue
        nome = str(evidencia_tool.get("name") or "tool")
        conteudo = str(evidencia_tool.get("content") or "").strip()
        if conteudo:
            evidencias.append(f"[{nome}]\n{conteudo}")

    evidencia = "\n\n".join(evidencias).strip()
    if not evidencia:
        evidencia = "(nenhuma tool devolveu conteúdo neste turno)"
    return pergunta, resposta, evidencia[-6000:]


def _publicar(texto: str, rotulo: str, resposta_original: str, veredito: str = "recusado") -> dict:
    logger.info("[juiz] %s", rotulo)
    texto_final = texto.strip()
    if texto_final == (resposta_original or "").strip():
        return {"agentes_chamados": [f"juiz:{rotulo}"], "veredito_turno": veredito}
    return {
        "messages": [{"role": "assistant", "content": texto_final}],
        "agentes_chamados": [f"juiz:{rotulo}"],
        "veredito_turno": veredito,
    }


def _requisicao_jev(corpo: dict) -> dict:
    pedido = urllib.request.Request(
        JEV_URL,
        data=json.dumps(corpo, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {TYPESAFE_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Assessor.AI/0.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(pedido, timeout=15) as resposta_http:
        dados = json.loads(resposta_http.read().decode("utf-8"))
    if not isinstance(dados, dict):
        raise ValueError("Resposta inválida do serviço de julgamento.")
    return dados


def _julgar_jev(pergunta: str, resposta: str, evidencia: str) -> dict:
    if not TYPESAFE_API_KEY:
        return _publicar(FRASE_SEGURA, "sem_chave", resposta, "falha")

    corpo = {
        "model": JEV_MODELO,
        "state": {
            "pergunta": pergunta,
            "resposta": resposta,
            "evidencia": evidencia,
            "regras": REGRAS,
        },
        "questions": {
            "veredito": {
                "type": "choice",
                "instructions": "Seguindo as regras, a resposta só afirma o que a evidência sustenta?",
                "criteria": {
                    "apoiada": "toda afirmação factual aparece na evidência",
                    "parcial": "parte aparece e parte não",
                    "sem_apoio": "há fato, número, regra ou opinião sem evidência",
                },
            },
            "apoiada": {
                "type": "noul",
                "instructions": (
                    "Seguindo as regras, a resposta é fiel à evidência: todo valor, moeda, "
                    "data e fato dela sai da evidência, da fala do usuário, de contas sobre "
                    "elas ou de uma conversão cambial documentada pela tool?"
                ),
            },
        },
    }

    try:
        dados = _requisicao_jev(corpo)
        respostas = dados["answers"]
        choice = str(respostas["veredito"]["choice"])
        noul = float(respostas["apoiada"]["noul"])
    except urllib.error.HTTPError as exc:
        logger.warning("[juiz] erro HTTP %s", exc.code)
        return _publicar(FRASE_SEGURA, f"erro_http_{exc.code}", resposta, "falha")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("[juiz] erro de conexão: %s", type(exc).__name__)
        return _publicar(FRASE_SEGURA, "erro_conexao", resposta, "falha")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("[juiz] resposta inválida: %s", type(exc).__name__)
        return _publicar(FRASE_SEGURA, "resposta_invalida", resposta, "falha")

    if choice == "apoiada" and noul >= LIMIAR:
        return _publicar(resposta, f"apoiada_noul={noul:.2f}", resposta, "aprovado")
    rotulo = "apoiada_abaixo_do_limiar" if choice == "apoiada" else choice
    return _publicar(FRASE_SEGURA, f"{rotulo}_noul={noul:.2f}", resposta)


def no_juiz(estado: dict, config: RunnableConfig) -> dict:
    pergunta, resposta, evidencia = _coleta(estado)
    tx = transaction_for(config)
    if tx and tx.aborted:
        return _publicar("Não consegui concluir a operação no banco de dados.", "erro_tool", resposta, "falha")
    if not resposta:
        return {"agentes_chamados": ["juiz:sem_resposta"], "veredito_turno": "falha"}
    try:
        return _julgar_jev(pergunta, resposta, evidencia)
    except Exception as exc:  # defesa final: falhar fechado sem expor dados
        logger.exception("[juiz] falha inesperada: %s", type(exc).__name__)
        return _publicar(FRASE_SEGURA, "erro", resposta, "falha")
