import unittest
from unittest.mock import patch

from app.juiz import FRASE_SEGURA, _coleta, _julgar_jev


class JudgeEvidenceTests(unittest.TestCase):
    def test_coleta_somente_o_turno_mais_recente_e_preserva_conversao(self):
        estado = {
            "messages": [
                {"role": "user", "content": "pergunta antiga"},
                {"role": "tool", "name": "search_transactions", "content": "dado antigo"},
                {"role": "assistant", "content": "resposta antiga"},
                {"role": "user", "content": "gastei 5 dólares com café"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "name": "add_transaction",
                        "args": {"amount": 5, "currency": "USD"},
                    }],
                },
                {
                    "role": "tool",
                    "name": "add_transaction",
                    "content": (
                        '{"status":"ok","original_amount":5,"original_currency":"USD",'
                        '"exchange_rate":5.2,"rate_date":"2026-10-27","amount_brl":26}'
                    ),
                },
                {"role": "assistant", "content": "Registrei R$ 26,00."},
            ]
        }
        pergunta, resposta, evidencia = _coleta(estado)
        self.assertEqual(pergunta, "gastei 5 dólares com café")
        self.assertEqual(resposta, "Registrei R$ 26,00.")
        self.assertIn("original_currency", evidencia)
        self.assertIn("amount_brl", evidencia)
        self.assertNotIn("dado antigo", evidencia)

    @patch("app.juiz.TYPESAFE_API_KEY", None)
    def test_sem_chave_falha_fechado(self):
        resultado = _julgar_jev("oi", "Olá!", "(nenhuma tool)")
        self.assertEqual(resultado["messages"][0]["content"], FRASE_SEGURA)
        self.assertIn("juiz:sem_chave", resultado["agentes_chamados"])

    @patch("app.juiz.TYPESAFE_API_KEY", "chave-de-teste")
    @patch("app.juiz._requisicao_jev")
    def test_resposta_apoiada_e_preservada_sem_reescrita(self, requisicao):
        requisicao.return_value = {
            "answers": {
                "veredito": {"choice": "apoiada"},
                "apoiada": {"noul": 0.92},
            }
        }
        resultado = _julgar_jev("oi", "Olá!", "(nenhuma tool)")
        self.assertNotIn("messages", resultado)
        self.assertIn("juiz:apoiada_noul=0.92", resultado["agentes_chamados"])

    @patch("app.juiz.TYPESAFE_API_KEY", "chave-de-teste")
    @patch("app.juiz._requisicao_jev")
    def test_resposta_sem_apoio_e_substituida(self, requisicao):
        requisicao.return_value = {
            "answers": {
                "veredito": {"choice": "sem_apoio"},
                "apoiada": {"noul": 0.12},
            }
        }
        resultado = _julgar_jev("saldo?", "Seu saldo é R$ 999.", "[]")
        self.assertEqual(resultado["messages"][0]["content"], FRASE_SEGURA)
        self.assertIn("juiz:sem_apoio_noul=0.12", resultado["agentes_chamados"])

    @patch("app.juiz.TYPESAFE_API_KEY", "chave-de-teste")
    @patch("app.juiz._requisicao_jev", side_effect=TimeoutError("timeout"))
    def test_timeout_falha_fechado_e_exige_rollback(self, _requisicao):
        resultado = _julgar_jev("registre", "Registrei.", "tool status ok")
        self.assertEqual(resultado["messages"][0]["content"], FRASE_SEGURA)
        self.assertEqual(resultado["veredito_turno"], "falha")
        self.assertIn("juiz:erro_conexao", resultado["agentes_chamados"])


if __name__ == "__main__":
    unittest.main()
