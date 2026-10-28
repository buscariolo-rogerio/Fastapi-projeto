import unittest
from unittest.mock import patch

import app.currency as currency
from app.currency import ConversionResult, CurrencyConversionError
from app.tools.financeiro import add_transaction


class CurrencyDetectionTests(unittest.TestCase):
    def setUp(self):
        currency._CACHE.clear()

    def test_detecta_nomes_simbolos_e_codigos(self):
        casos = {
            "gastei 5 dólares com café": "USD",
            "paguei 12 euros no almoço": "EUR",
            "comprei por 8 libras esterlinas": "GBP",
            "custou 900 JPY": "JPY",
            "paguei 30 NOK": "NOK",
            "gastei 25 no PIX": "BRL",
            "gastei R$ 25 no mercado": "BRL",
            "gastei 25 no mercado": "BRL",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(currency.detectar_moeda(texto), esperado)

    @patch("app.currency._request_json")
    def test_converte_e_arredonda_para_centavos(self, request_json):
        request_json.return_value = {
            "date": "2026-10-27",
            "base": "USD",
            "quote": "BRL",
            "rate": 5.23456,
        }
        resultado = currency.converter_para_brl(5, "USD")
        self.assertEqual(resultado.amount_brl, 26.17)
        self.assertEqual(resultado.original_currency, "USD")
        self.assertEqual(resultado.rate_date, "2026-10-27")

    @patch("app.currency._request_json")
    def test_reutiliza_cotacao_em_cache(self, request_json):
        request_json.return_value = {"date": "2026-10-27", "rate": 5.0}
        currency.converter_para_brl(1, "USD")
        currency.converter_para_brl(2, "USD")
        request_json.assert_called_once()


class _FakeCursor:
    def __init__(self):
        self.last_query = ""
        self.insert_params = None

    def execute(self, query, params=None):
        self.last_query = query
        if "INSERT INTO transactions" in query:
            self.insert_params = params

    def fetchone(self):
        if "SELECT id FROM transaction_types" in self.last_query:
            return (2,)
        if "INSERT INTO transactions" in self.last_query:
            return (123, "2026-10-27T12:00:00-03:00")
        return None

    def close(self):
        pass


class _FakeConnection:
    def __init__(self):
        self.fake_cursor = _FakeCursor()
        self.committed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class AddTransactionCurrencyTests(unittest.TestCase):
    @patch("app.tools.financeiro.converter_para_brl")
    @patch("app.tools.financeiro.get_conn")
    def test_grava_no_banco_o_valor_convertido(self, get_conn, converter):
        fake_conn = _FakeConnection()
        get_conn.return_value = fake_conn
        converter.return_value = ConversionResult(5.0, "USD", 26.17, 5.23456, "2026-10-27")

        resultado = add_transaction.invoke({
            "amount": 5,
            "source_text": "gastei 5 dólares com café",
            "type_name": "EXPENSES",
        })

        self.assertEqual(resultado["status"], "ok")
        self.assertEqual(resultado["amount_brl"], 26.17)
        converter.assert_called_once_with(5.0, "USD")
        self.assertEqual(fake_conn.fake_cursor.insert_params[0], 26.17)
        self.assertTrue(fake_conn.committed)

    @patch("app.tools.financeiro.converter_para_brl")
    @patch("app.tools.financeiro.get_conn")
    def test_nao_grava_quando_cotacao_falha(self, get_conn, converter):
        converter.side_effect = CurrencyConversionError("Cotação indisponível")
        resultado = add_transaction.invoke({
            "amount": 5,
            "currency": "USD",
            "source_text": "gastei 5 dólares com café",
            "type_name": "EXPENSES",
        })
        self.assertEqual(resultado["status"], "error")
        self.assertEqual(resultado["code"], "exchange_rate_unavailable")
        get_conn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
