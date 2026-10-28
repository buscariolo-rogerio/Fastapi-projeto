"""Endpoint de observabilidade do processo atual."""

import math

from fastapi import APIRouter

from app.observabilidade import (
    LIMITE_P95_MS,
    LIMITE_TAXA_ERRO,
    listar_chamadas,
    listar_turnos,
    situacao,
)


router = APIRouter(tags=["monitor"])


def _percentil_95(valores: list[int]) -> int:
    if not valores:
        return 0
    ordenados = sorted(valores)
    indice = math.ceil(0.95 * len(ordenados)) - 1
    return ordenados[min(max(indice, 0), len(ordenados) - 1)]


def _media(valores: list[float]) -> float:
    return sum(valores) / len(valores) if valores else 0.0


def _agrupar(chamadas: list[dict], chave: str) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for item in chamadas:
        grupos.setdefault(item[chave], []).append(item)
    return grupos


def montar_painel() -> dict:
    turnos = listar_turnos()
    chamadas = listar_chamadas()
    estado = situacao()

    latencias = [turno["latencia_ms"] for turno in turnos]
    erros = sum(1 for turno in turnos if turno["status"] == "erro")
    bloqueios = sum(1 for turno in turnos if turno["status"] == "bloqueado")
    taxa_erro = erros / len(turnos) if turnos else 0.0
    p95 = _percentil_95(latencias)

    tokens_entrada = sum(item["tokens_entrada"] for item in chamadas)
    tokens_saida = sum(item["tokens_saida"] for item in chamadas)
    custo = sum(item["custo_usd"] for item in chamadas)

    por_modelo_bruto = _agrupar(chamadas, "modelo")
    mais_usado = "—"
    if por_modelo_bruto:
        mais_usado = max(
            por_modelo_bruto,
            key=lambda nome: (
                len(por_modelo_bruto[nome]),
                sum(i["tokens_entrada"] + i["tokens_saida"] for i in por_modelo_bruto[nome]),
            ),
        )

    por_modelo = [{
        "modelo": modelo,
        "chamadas": len(itens),
        "tokens_entrada": sum(i["tokens_entrada"] for i in itens),
        "tokens_saida": sum(i["tokens_saida"] for i in itens),
        "custo_usd": round(sum(i["custo_usd"] for i in itens), 6),
        "latencia_media_ms": round(_media([i["latencia_ms"] for i in itens])),
    } for modelo, itens in por_modelo_bruto.items()]
    por_modelo.sort(key=lambda linha: linha["custo_usd"], reverse=True)

    por_rota = [{
        "rota": rota,
        "turnos": len(itens),
        "erros": sum(1 for i in itens if i["status"] == "erro"),
        "latencia_media_ms": round(_media([i["latencia_ms"] for i in itens])),
        "custo_usd": round(sum(i["custo_usd"] for i in itens), 6),
    } for rota, itens in _agrupar(turnos, "rota").items()]
    por_rota.sort(key=lambda linha: linha["turnos"], reverse=True)

    custo_acumulado = 0.0
    serie_custo = []
    for turno in turnos:
        custo_acumulado += turno["custo_usd"]
        serie_custo.append({
            "hora": turno["hora"],
            "custo_acumulado_usd": round(custo_acumulado, 6),
        })

    return {
        "slo": {
            "latencia_p95_limite_ms": LIMITE_P95_MS,
            "taxa_erro_limite": LIMITE_TAXA_ERRO,
        },
        "resumo": {
            "turnos": len(turnos),
            "em_andamento": estado["em_andamento"],
            "no_ar_segundos": estado["no_ar_segundos"],
            "taxa_erro": round(taxa_erro, 4),
            "bloqueios": bloqueios,
            "latencia_media_ms": round(_media(latencias)),
            "latencia_p95_ms": p95,
            "slo_ok": p95 <= LIMITE_P95_MS and taxa_erro <= LIMITE_TAXA_ERRO,
            "tokens_entrada": tokens_entrada,
            "tokens_saida": tokens_saida,
            "tokens_total": tokens_entrada + tokens_saida,
            "custo_usd": round(custo, 6),
            "modelo_mais_usado": mais_usado,
            "fallbacks": sum(1 for item in chamadas if item["fallback"]),
        },
        "serie_latencia": [
            {"hora": turno["hora"], "latencia_ms": turno["latencia_ms"]}
            for turno in turnos
        ],
        "serie_custo": serie_custo,
        "por_modelo": por_modelo,
        "por_rota": por_rota,
        "turnos": [
            {**turno, "custo_usd": round(turno["custo_usd"], 6)}
            for turno in turnos
        ],
    }


@router.get("/monitor")
def painel() -> dict:
    """Métricas do processo; reiniciar o servidor zera o histórico."""
    return montar_painel()
