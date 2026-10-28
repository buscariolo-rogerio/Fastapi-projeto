import unittest
from unittest.mock import patch

from app.tools import calendario_google


class GoogleCalendarToolTests(unittest.TestCase):
    def test_fim_padrao_soma_uma_hora_preservando_fuso(self):
        self.assertEqual(
            calendario_google._fim_padrao("2026-10-28T14:00:00-03:00"),
            "2026-10-28T15:00:00-03:00",
        )

    @patch("app.tools.calendario_google.chamar_google_calendar")
    def test_add_google_event_monta_payload_e_normaliza_resultado(self, chamar):
        chamar.return_value = {
            "status": "ok",
            "id": "google-123",
            "titulo": "Consulta",
            "inicio": "2026-10-28T14:00:00-03:00",
            "link": "https://calendar.google.com/event?eid=teste",
        }

        resultado = calendario_google.add_google_event.invoke(
            {
                "title": "Consulta",
                "start_time": "2026-10-28T14:00:00-03:00",
                "location": "Clínica",
            }
        )

        self.assertEqual(resultado["status"], "ok")
        self.assertEqual(resultado["id"], "google-123")
        nome, argumentos = chamar.call_args.args
        self.assertEqual(nome, "create_event")
        self.assertEqual(argumentos["endTime"], "2026-10-28T15:00:00-03:00")
        self.assertEqual(argumentos["location"], "Clínica")

    @patch("app.tools.calendario_google._carregar_token", return_value=None)
    def test_sem_token_retorna_none(self, _carregar):
        with patch.object(calendario_google, "GOOGLE_CALENDAR_ACCESS_TOKEN", None):
            self.assertIsNone(calendario_google._access_token())

    def test_tools_google_exporta_add_google_event(self):
        self.assertEqual(
            [tool.name for tool in calendario_google.TOOLS_GOOGLE],
            ["add_google_event"],
        )


if __name__ == "__main__":
    unittest.main()
