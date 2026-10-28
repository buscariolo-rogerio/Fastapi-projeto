import json
import operator
import re
import ast
from datetime import datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo
from langgraph.graph import StateGraph, MessagesState, END
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from app.tools.financeiro import TOOLS, add_transaction, saldo_total
from app.tools.faq import faq_retriever
from app.guardrail import (
    anonimizar_entrada,
    guardrail_entrada,
    guardrail_saida,
    limpar_raciocinio,
    normalizar_resposta_usuario,
)
from app.juiz import no_juiz
from langchain_core.messages import RemoveMessage
from langchain_core.runnables import RunnableConfig
from app.llms import llm, llm_rapido
from app.memory import salvar_mensagem
from app.observabilidade import abrir_turno, fechar_turno, status_do_estado
from app.tools.memoria import TOOLS_MEMORIA
from app.tools.perfil import buscar_perfil
from app.tools.agenda import TOOLS_AGENDA
from app.tools.calendario_google import TOOLS_GOOGLE
from app.prompts import (
    ROUTER_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    AGENDA_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    FAQ_PROMPT_COMPLETO,
)

# AGENTES

# ROUTER
router_memory = MemorySaver()

router_app = create_agent(
    model=llm_rapido,
    tools=TOOLS_MEMORIA,
    system_prompt=ROUTER_PROMPT_COMPLETO,
    checkpointer=router_memory,
)

# FINANCEIRO
financeiro_app = create_agent(
    model=llm,
    tools=TOOLS + TOOLS_MEMORIA + [buscar_perfil],
    system_prompt=FINANCEIRO_PROMPT_COMPLETO,
)

# AGENDA
agenda_app = create_agent(
    model=llm,
    tools=TOOLS_AGENDA + TOOLS_GOOGLE + TOOLS_MEMORIA,
    system_prompt=AGENDA_PROMPT_COMPLETO,
)

# ORQUESTRADOR
orquestrador_app = create_agent(
    model=llm_rapido,
    system_prompt=ORQUESTRADOR_PROMPT_COMPLETO,
)

# FAQ
faq_app = create_agent(
    model=llm,
    tools=[faq_retriever],
    system_prompt=FAQ_PROMPT_COMPLETO,
)

# ==============================================================================
# ESTADO
# ==============================================================================
class Estado(MessagesState):
    agentes_chamados:   Annotated[list[str], operator.add]  # acumula entre nós
    rota: str                                  # decisão do roteador
    mapa_pii: dict #tokens -> valores originais gerados na anonimização


# ==============================================================================
# NÓS
# ==============================================================================
def no_roteador(estado: Estado, config: RunnableConfig) -> dict:
    saida = router_app.invoke(
        {"messages": list(estado["messages"])},
        config=config,
    )
    texto = _texto_mensagem(saida["messages"][-1])

    if "ROUTE=" not in texto:
        return {
            "agentes_chamados": ["roteador"],
            "rota":             "fim",
            "messages":         [{"role": "assistant", "content": texto}],
        }

    rota = "fim"
    for linha in texto.splitlines():
        if linha.startswith("ROUTE="):
            rota = linha.split("=", 1)[1].strip()
            break

    return {
        "agentes_chamados": ["roteador"],
        "rota":             rota,
        # Não adiciona nada ao messages — especialista lê histórico limpo
    }


def _texto_mensagem(mensagem) -> str:
    """Extrai texto de mensagens LangChain, inclusive conteúdo multimodal."""
    if isinstance(mensagem, dict):
        conteudo = mensagem.get("content", "")
    else:
        texto = getattr(mensagem, "text", None)
        if isinstance(texto, str) and texto.strip():
            return texto.strip()
        conteudo = getattr(mensagem, "content", "")

    if isinstance(conteudo, str):
        return conteudo.strip()
    if isinstance(conteudo, list):
        partes = []
        for parte in conteudo:
            if isinstance(parte, dict):
                valor = parte.get("text", "")
            else:
                valor = getattr(parte, "text", "")
            if isinstance(valor, str):
                partes.append(valor)
        return "\n".join(partes).strip()
    return ""


def _indice_ultima_mensagem_usuario(mensagens) -> int:
    for indice in range(len(mensagens) - 1, -1, -1):
        mensagem = mensagens[indice]
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("human", "user"):
            return indice
    return -1


def _eh_solicitacao_de_saldo_total(texto: str) -> bool:
    texto = texto.casefold()
    return (
        "saldo" in texto
        and not any(palavra in texto for palavra in ("hoje", "ontem", "dia", "semana", "mês", "mes", "período", "periodo"))
        and any(palavra in texto for palavra in ("total", "momento", "agora", "atual"))
    )


def _parece_operacao_financeira(texto: str) -> bool:
    texto = texto.casefold()
    return any(palavra in texto for palavra in (
        "saldo", "extrato", "gasto", "gastos", "despesa", "receita", "entrada",
        "transação", "transacao", "lançamento", "lancamento", "registrar",
        "adicionar", "atualizar", "corrigir", "excluir", "apagar", "remover",
    ))


def _eh_pedido_de_insercao(texto: str) -> bool:
    texto = texto.casefold()
    if any(palavra in texto for palavra in ("quanto", "qual", "mostre", "listar", "extrato")):
        return False
    return any(palavra in texto for palavra in (
        "gastei", "comprei", "paguei", "ganhei", "recebi", "registrar",
        "registre", "lançar", "lancar", "adicionar", "lance",
    ))


def _valor_insercao(texto: str) -> float | None:
    encontrados = re.findall(
        r"(?:r\$\s*|\b)(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:reais?|rs)\b",
        texto.casefold(),
    )
    if not encontrados:
        encontrados = re.findall(r"(?<![/\d])\d+(?:[.,]\d{1,2})?(?![/\d])", texto)
    for bruto in encontrados:
        numero = bruto.replace(".", "").replace(",", ".") if "," in bruto else bruto
        try:
            valor = float(numero)
        except ValueError:
            continue
        if valor > 0:
            return valor
    return None


def _data_insercao(texto: str) -> str | None:
    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    texto_lower = texto.casefold()
    if "anteontem" in texto_lower:
        dia = agora - timedelta(days=2)
    elif "ontem" in texto_lower:
        dia = agora - timedelta(days=1)
    else:
        encontrados = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", texto)
        if not encontrados:
            return None
        try:
            return datetime.strptime(encontrados.group(0), "%d/%m/%Y").date().isoformat()
        except ValueError:
            return None
    return dia.date().isoformat()


def _numero_pt_br(valor: float, casas: int = 2) -> str:
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _resposta_transacao_registrada(resultado: dict) -> str:
    valor_brl = float(resultado.get("amount_brl", 0))
    moeda = resultado.get("original_currency", "BRL")
    if moeda != "BRL":
        original = float(resultado.get("original_amount", 0))
        taxa = float(resultado.get("exchange_rate", 0))
        data = resultado.get("rate_date", "cotação mais recente")
        return (
            f"Converti {_numero_pt_br(original)} {moeda} pela cotação de {data} "
            f"(1 {moeda} = R$ {_numero_pt_br(taxa, 4)}) e registrei "
            f"R$ {_numero_pt_br(valor_brl)} no banco de dados."
        )
    return f"Registrei a transação de R$ {_numero_pt_br(valor_brl)} no banco de dados."


def _fallback_insercao(texto: str) -> dict | None:
    valor = _valor_insercao(texto)
    if valor is None:
        return None
    texto_lower = texto.casefold()
    tipo = "INCOME" if any(palavra in texto_lower for palavra in ("ganhei", "recebi", "salário", "salario", "receita")) else "EXPENSES"
    data = _data_insercao(texto)
    ocorreu_em = f"{data}T12:00:00-03:00" if data else None
    resultado = add_transaction.invoke({
        "amount": valor,
        "source_text": texto,
        "occurred_at": ocorreu_em,
        "type_name": tipo,
        "description": texto,
    })
    if resultado.get("status") == "ok":
        return {
            "dominio": "financeiro",
            "intencao": "inserir",
            "resposta": _resposta_transacao_registrada(resultado),
            "recomendacao": "",
            "escrita": {"operacao": "adicionar", "id": resultado.get("id")},
        }
    return {
        "dominio": "financeiro",
        "intencao": "inserir",
        "resposta": "Não consegui registrar essa transação no banco de dados.",
        "recomendacao": "Tente novamente em instantes.",
    }


def _json_especialista_valido(texto: str) -> bool:
    texto = limpar_raciocinio(texto).strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        valor = json.loads(texto)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(valor, dict) and isinstance(valor.get("resposta"), str)


def _extrair_json_especialista(texto: str) -> dict | None:
    texto = limpar_raciocinio(texto).strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        valor = json.loads(texto)
    except (TypeError, json.JSONDecodeError):
        inicio = texto.find("{")
        fim = texto.rfind("}")
        if inicio < 0 or fim <= inicio:
            return None
        try:
            valor = json.loads(texto[inicio:fim + 1])
        except json.JSONDecodeError:
            return None
    return valor if isinstance(valor, dict) else None


def _formatar_resposta_especialista(texto: str) -> str | None:
    """Converte o contrato interno JSON para uma resposta natural simples."""
    dados = _extrair_json_especialista(texto)
    if not dados or not isinstance(dados.get("resposta"), str):
        return None

    partes = [dados["resposta"].strip()]
    recomendacao = dados.get("recomendacao")
    acompanhamento = dados.get("esclarecer") or dados.get("acompanhamento")
    if isinstance(recomendacao, str) and recomendacao.strip():
        partes.append(recomendacao.strip())
    if isinstance(acompanhamento, str) and acompanhamento.strip():
        partes.append(acompanhamento.strip())
    return normalizar_resposta_usuario(" ".join(parte for parte in partes if parte))


def _ultimo_resultado_tool(mensagens, nome_tool: str) -> dict | None:
    for mensagem in reversed(mensagens):
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        nome = mensagem.get("name", "") if isinstance(mensagem, dict) else getattr(mensagem, "name", "")
        if tipo != "tool" or (nome and nome != nome_tool):
            continue
        conteudo = mensagem.get("content", "") if isinstance(mensagem, dict) else getattr(mensagem, "content", "")
        if isinstance(conteudo, dict):
            return conteudo
        if isinstance(conteudo, str):
            try:
                valor = json.loads(conteudo)
            except json.JSONDecodeError:
                try:
                    valor = ast.literal_eval(conteudo)
                except (ValueError, SyntaxError):
                    continue
            if isinstance(valor, dict):
                return valor
    return None


def no_financeiro(estado: Estado, config: RunnableConfig) -> dict:
    """Executa o especialista e impede uma resposta sem consulta em saldo total."""
    indice = _indice_ultima_mensagem_usuario(estado["messages"])
    pedido = _texto_mensagem(estado["messages"][indice]).casefold() if indice >= 0 else ""
    if (
        any(campo in pedido for campo in ("perfil", "renda mensal", "tolerância", "tolerancia", "preferências", "preferencias", "meu objetivo"))
        and re.search(
            r"\b(mudar|mude|muda|alterar|altere|altera|atualizar|atualize|atualiza|"
            r"cadastrar|cadastre|cadastra|registrar|registre|registra|corrigir|corrija|"
            r"corrige|trocar|troque|troca)\b", pedido
        )
    ):
        # Não deixar um pedido de cadastro cair no fallback de transações.
        return {
            "agentes_chamados": ["financeiro"],
            "messages": [{"role": "assistant", "content": json.dumps({
                "dominio": "financeiro", "intencao": "consultar",
                "resposta": "Para cadastrar ou alterar seu perfil, use a tela Perfil em /perfil.html. O chat apenas consulta esse cadastro.",
                "recomendacao": "",
            }, ensure_ascii=False)}],
        }
    try:
        saida = financeiro_app.invoke(
            {"messages": list(estado["messages"])},
            config=config,
        )
    except Exception:
        return {
            "agentes_chamados": ["financeiro"],
            "messages": [{
                "role": "assistant",
                "content": json.dumps({
                    "dominio": "financeiro",
                    "intencao": "consultar",
                    "resposta": "Não consegui concluir a operação financeira agora.",
                    "recomendacao": "Tente novamente em instantes.",
                }, ensure_ascii=False),
            }],
        }
    mensagens = saida.get("messages", [])
    indice_usuario = _indice_ultima_mensagem_usuario(mensagens)
    mensagens_do_turno = mensagens[indice_usuario + 1:] if indice_usuario >= 0 else mensagens
    chamou_tool = any(
        (mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")) == "tool"
        for mensagem in mensagens_do_turno
    )
    indice_pergunta = _indice_ultima_mensagem_usuario(estado["messages"])
    pergunta = _texto_mensagem(estado["messages"][indice_pergunta]) if indice_pergunta >= 0 else ""
    texto_financeiro = ""
    for mensagem in reversed(mensagens_do_turno):
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("ai", "assistant"):
            texto_financeiro = _texto_mensagem(mensagem)
            if texto_financeiro:
                break

    # Saldo total é uma operação determinística. Se o LLM falhar em emitir a
    # chamada da tool, fazemos a consulta explicitamente para nunca responder
    # com um texto operacional/hallucinado como se fosse o resultado.
    if _eh_solicitacao_de_saldo_total(pergunta) and not chamou_tool:
        try:
            resultado = saldo_total.invoke({})
        except Exception:
            resultado = {
                "status": "error",
                "message": "Não foi possível consultar o saldo agora.",
            }
        if resultado.get("status") == "ok":
            valor = resultado["saldo"]
            resposta = (
                "Seu saldo total é R$ "
                f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                + "."
            )
            especialista = {
                "dominio": "financeiro",
                "intencao": "consultar",
                "resposta": resposta,
                "recomendacao": "",
                "indicadores": {
                    "saldo": resultado["saldo"],
                    "total_income": resultado["total_income"],
                    "total_expenses": resultado["total_expenses"],
                },
            }
        else:
            especialista = {
                "dominio": "financeiro",
                "intencao": "consultar",
                "resposta": resultado.get("message", "Não foi possível consultar o saldo agora."),
                "recomendacao": "Tente novamente em instantes.",
            }
        return {
            "agentes_chamados": ["financeiro"],
            "messages": [{"role": "assistant", "content": json.dumps(especialista, ensure_ascii=False)}],
        }

    # Para inserções simples, se o modelo não emitiu a chamada da tool,
    # extraímos somente os dados inequívocos e registramos de forma explícita.
    # Se faltar valor ou a intenção não for clara, não fazemos nenhuma escrita.
    if _eh_pedido_de_insercao(pergunta):
        resultado_adicao = _ultimo_resultado_tool(mensagens_do_turno, "add_transaction")
        if resultado_adicao and resultado_adicao.get("status") == "ok":
            especialista = {
                "dominio": "financeiro",
                "intencao": "inserir",
                "resposta": _resposta_transacao_registrada(resultado_adicao),
                "recomendacao": "",
                "escrita": {"operacao": "adicionar", "id": resultado_adicao.get("id")},
            }
        elif not chamou_tool:
            especialista = _fallback_insercao(pergunta)
        else:
            especialista = None
        if especialista is not None:
            return {
                "agentes_chamados": ["financeiro"],
                "messages": [{"role": "assistant", "content": json.dumps(especialista, ensure_ascii=False)}],
            }

    # Uma operação financeira não pode terminar com texto de prontidão,
    # instruções internas ou qualquer saída que não seja o JSON contratado.
    # Nesse caso nenhuma escrita é considerada confirmada.
    if _parece_operacao_financeira(pergunta) and texto_financeiro and not _json_especialista_valido(texto_financeiro):
        return {
            "agentes_chamados": ["financeiro"],
            "messages": [{
                "role": "assistant",
                "content": json.dumps({
                    "dominio": "financeiro",
                    "intencao": "consultar",
                    "resposta": "Não consegui confirmar essa operação no banco de dados.",
                    "recomendacao": "Tente novamente; nenhuma alteração foi confirmada.",
                }, ensure_ascii=False),
            }],
        }

    return saida


def no_orquestrador(estado: Estado, config: RunnableConfig) -> dict:
    # O estado do LangGraph contém o histórico de vários turnos. Portanto,
    # procurar simplesmente o último AIMessage pode reutilizar a resposta de
    # uma pergunta anterior quando o especialista atual falhar. Delimitamos o
    # turno pela última mensagem do usuário e só aceitamos uma resposta gerada
    # depois dela.
    mensagens = estado["messages"]
    indice_usuario = -1
    for indice, mensagem in enumerate(mensagens):
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("human", "user"):
            indice_usuario = indice

    especialista_texto = ""
    for mensagem in reversed(mensagens[indice_usuario + 1:]):
        tipo = mensagem.get("role") if isinstance(mensagem, dict) else getattr(mensagem, "type", "")
        if tipo in ("ai", "assistant"):
            especialista_texto = _texto_mensagem(mensagem)
            if especialista_texto:
                break

    if not especialista_texto:
        especialista_texto = json.dumps({
            "dominio": "geral",
            "intencao": "responder",
            "resposta": "Desculpe, não consegui obter os dados necessários no momento. Como posso te ajudar?",
            "recomendacao": "",
        }, ensure_ascii=False)

    especialista_texto = limpar_raciocinio(especialista_texto)
    # O JSON do especialista é um contrato interno. Quando ele está válido,
    # formatamos localmente para eliminar de forma determinística chaves,
    # valores, rótulos e Markdown. O LLM fica apenas como fallback para
    # respostas antigas que ainda não estejam no contrato.
    resposta_orquestrada = _formatar_resposta_especialista(especialista_texto)
    if resposta_orquestrada is None:
        saida = orquestrador_app.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": f"ESPECIALISTA_JSON:\n{especialista_texto}",
                    }
                ]
            },
            config=config,
        )
        resposta_orquestrada = normalizar_resposta_usuario(_texto_mensagem(saida["messages"][-1]))
    if not resposta_orquestrada:
        raise RuntimeError("O orquestrador não produziu uma resposta textual.")
    return {
        "agentes_chamados": [estado["rota"], "orquestrador"],
        "messages":         [{"role": "assistant", "content": resposta_orquestrada}],
    }


# ==============================================================================
# FUNÇÃO DE DECISÃO
# ==============================================================================
def decidir_especialista(estado: Estado) -> str:
    rota = estado.get("rota", "fim")

    if rota in ("financeiro", "agenda", "faq"):
        return rota

    return "fim"

def decidir_pos_guardrail_entrada(estado: Estado) -> str:
    return "fim" if estado["rota"] == "fim" else "roteador"

# ==============================================================================
# FUNÇÕES DE GUARDRAIL
# ==============================================================================

def no_guardrail_entrada(estado: Estado, config: RunnableConfig) -> dict:
    """Executa o guardrail de entrada e decide se o fluxo continua ou é bloqueado."""

    puser = estado["messages"][-1]

    mensagem_anonimizada, mapa_pii = anonimizar_entrada(_texto_mensagem(puser))
    entrada = guardrail_entrada(mensagem_anonimizada)

    if entrada["bloqueado"]:
        return {
            "agentes_chamados": ["guardrail_entrada"],
            "rota": "fim",
            "messages": [
                {
                    "role": "assistant",
                    "content": entrada["mensagem"],
                }
            ],
        }

    configuravel = (config or {}).get("configurable", {})
    session_id = configuravel.get("thread_id")
    user_id = configuravel.get("user_id") or session_id
    if session_id:
        # A pergunta só é persistida depois da anonimização.
        salvar_mensagem(
            session_id,
            "user",
            mensagem_anonimizada,
            user_id=user_id,
        )

    return {
        "agentes_chamados": ["guardrail_entrada"],
        "rota": "roteador",
        "mapa_pii": mapa_pii,
        "messages": [
            RemoveMessage(id=puser.id),
            {
                "role": "human",
                "content": mensagem_anonimizada,
            },
        ],
    }
    
def no_guardrail_saida(estado: Estado) -> dict:
    """Executa o guardrail de saída e devolve a resposta final (analisada e desanonimizada)."""
    resultado = guardrail_saida(_texto_mensagem(estado["messages"][-1]), estado["mapa_pii"])

    if resultado["bloqueado"]:
        return {
            "messages":         [{"role": "assistant", "content": resultado["mensagem"]}],
        }

    mensagem_atual = estado["messages"][-1]
    return {
        "messages": [
            RemoveMessage(id=mensagem_atual.id),
            {"role": "assistant", "content": resultado["conteudo"]},
        ],
    }


# ==============================================================================
# CONSTRUÇÃO DO GRAFO
# ==============================================================================
grafo = StateGraph(Estado)

grafo.add_node("guardrail_entrada", no_guardrail_entrada)
grafo.add_node("roteador",     no_roteador)
grafo.add_node("financeiro",   no_financeiro)
grafo.add_node("agenda",       agenda_app)
grafo.add_node("faq",          faq_app)
grafo.add_node("orquestrador", no_orquestrador)
grafo.add_node("guardrail_saida", no_guardrail_saida)
grafo.add_node("juiz", no_juiz)

grafo.set_entry_point("guardrail_entrada")

grafo.add_conditional_edges(
    "guardrail_entrada",
    decidir_pos_guardrail_entrada,
    {
        "roteador": "roteador",
        "fim":      END,       # bloqueado pelo guardrail de entrada
    },
)

grafo.add_conditional_edges(
    "roteador",
    decidir_especialista,
    {
        "financeiro": "financeiro",
        "agenda":     "agenda",
        "faq":        "faq",
        "fim":        END,       # resposta direta: sem especialista nem orquestrador
    },
)

grafo.add_edge("financeiro",   "orquestrador")
grafo.add_edge("agenda",       "orquestrador")
grafo.add_edge("orquestrador", "guardrail_saida")
grafo.add_edge("guardrail_saida", "juiz")
grafo.add_edge("faq",          "juiz")   # FAQ bypassa o orquestrador, mas não o juiz
grafo.add_edge("juiz",         END)

# Memória centralizada no grafo — persiste o Estado inteiro entre turns
memory = MemorySaver()
fluxo_agentes = grafo.compile(checkpointer=memory)

# ==============================================================================
# FLUXO PRINCIPAL
# ==============================================================================
def executar_fluxo_assessor_detalhado(
    pergunta_usuario: str,
    session_id: str,
    user_id: str = "usuario_teste",
) -> tuple[str, list[str]]:
    estado_inicial = {
        "messages": [{"role": "human", "content": pergunta_usuario}],
        "agentes_chamados": [],
        "rota": "",
        "mapa_pii": {},
        "session_id": session_id,
    }
    
    turno_id = abrir_turno()
    status = "erro"
    rota = ""
    try:
        estado_final = fluxo_agentes.invoke(
            estado_inicial,
            config={
                "configurable": {
                    "thread_id": session_id,
                    "user_id": user_id,
                }
            },
        )

        resposta_bruta = _texto_mensagem(estado_final["messages"][-1])
        resposta = _formatar_resposta_especialista(resposta_bruta) or normalizar_resposta_usuario(resposta_bruta)
        # A entrada já foi persistida de forma anonimizada no guardrail de entrada.
        salvar_mensagem(
            session_id,
            "assistant",
            resposta,
            user_id=user_id or session_id,
        )
        status = status_do_estado(estado_final)
        rota = estado_final.get("rota") or ""
        return resposta, estado_final.get("agentes_chamados", [])
    finally:
        fechar_turno(turno_id, session_id=session_id, rota=rota, status=status)


def executar_fluxo_assessor(pergunta_usuario: str, session_id: str) -> str:
    """Mantém a API antiga, devolvendo somente o texto da resposta."""
    resposta, _ = executar_fluxo_assessor_detalhado(pergunta_usuario, session_id)
    return resposta
