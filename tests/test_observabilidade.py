import unittest
from unittest.mock import patch

from app.observabilidade import custo_usd, status_do_estado
from app.routes.monitor import _percentil_95, montar_painel, painel


class ObservabilidadeTests(unittest.TestCase):
    def test_custo_gemini(self):
        self.assertAlmostEqual(custo_usd("gemini-2.5-flash", 1_000_000, 1_000_000), 2.80)

    def test_custo_modelo_groq_atual(self):
        self.assertAlmostEqual(custo_usd("qwen/qwen3.8-27b", 1_000_000, 1_000_000), 4.80)

    def test_percentil_nearest_rank(self):
        self.assertEqual(_percentil_95(list(range(1, 101))), 95)

    def test_guardrail_bloqueado_e_identificado(self):
        estado = {
            "rota": "fim",
            "agentes_chamados": ["guardrail_entrada"],
        }
        self.assertEqual(status_do_estado(estado), "bloqueado")

    @patch("app.routes.monitor.situacao", return_value={"em_andamento": 0, "no_ar_segundos": 12})
    @patch("app.routes.monitor.listar_chamadas")
    @patch("app.routes.monitor.listar_turnos")
    def test_painel_agrega_slo_tokens_custo_e_fallback(self, turnos, chamadas, _situacao):
        turnos.return_value = [
            {"hora": "10:00:00", "session_id": "s1", "latencia_ms": 9000, "rota": "financeiro", "status": "erro", "tokens_total": 30, "custo_usd": 0.01},
            {"hora": "10:01:00", "session_id": "s2", "latencia_ms": 1000, "rota": "agenda", "status": "ok", "tokens_total": 20, "custo_usd": 0.02},
        ]
        chamadas.return_value = [
            {"modelo": "gemini-2.5-flash", "no": "financeiro", "tokens_entrada": 10, "tokens_saida": 20, "custo_usd": 0.01, "latencia_ms": 100, "fallback": False},
            {"modelo": "qwen/qwen3.8-27b", "no": "agenda", "tokens_entrada": 5, "tokens_saida": 15, "custo_usd": 0.02, "latencia_ms": 200, "fallback": True},
        ]

        painel = montar_painel()

        self.assertEqual(painel["resumo"]["turnos"], 2)
        self.assertEqual(painel["resumo"]["tokens_total"], 50)
        self.assertEqual(painel["resumo"]["fallbacks"], 1)
        self.assertFalse(painel["resumo"]["slo_ok"])

    def test_endpoint_monitor_responde(self):
        self.assertIn("resumo", painel())


if __name__ == "__main__":
    unittest.main()
