import concurrent.futures
import unittest
from unittest.mock import patch

from app.currency import ConversionResult
from app.tools.agenda import add_event
from app.tools.financeiro import add_transaction, update_transaction
from app.turn_transaction import register_turn, unregister_turn


class _Cursor:
    def __init__(self, marker: str, fail_sql: bool = False, fail_after_sql: bool = False):
        self.marker = marker
        self.fail_sql = fail_sql
        self.fail_after_sql = fail_after_sql
        self.last_query = ""
        self.params = None
        self.rowcount = 0
        self.closed = False

    def execute(self, query, params=None):
        self.last_query = " ".join(query.split())
        self.params = params
        if self.fail_sql and any(token in self.last_query for token in ("INSERT INTO", "UPDATE transactions")):
            raise RuntimeError(f"sql-error:{self.marker}")
        if "UPDATE transactions" in self.last_query:
            self.rowcount = 1

    def fetchone(self):
        if self.fail_after_sql and any(token in self.last_query for token in ("INSERT INTO", "SELECT t.id, t.occurred_at")):
            raise RuntimeError(f"after-sql:{self.marker}")
        if "SELECT id FROM transaction_types" in self.last_query:
            return (2,)
        if "INSERT INTO transactions" in self.last_query:
            return (101, "2026-10-29T12:00:00-03:00")
        if "INSERT INTO events" in self.last_query:
            return (201, "2026-10-29T14:00:00-03:00", "2026-10-29T15:00:00-03:00")
        if "SELECT t.id, t.occurred_at" in self.last_query:
            return (
                301,
                "2026-10-29T12:00:00-03:00",
                42.0,
                "EXPENSES",
                None,
                self.marker,
                None,
                self.marker,
            )
        return None

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, marker: str, fail_sql: bool = False, fail_after_sql: bool = False):
        self.cursor_instance = _Cursor(marker, fail_sql, fail_after_sql)
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


def _config(turn):
    return {"configurable": {"chat_transaction_id": turn.id}}


def _invoke_write(name, marker, config):
    if name == "add_transaction":
        with patch(
            "app.tools.financeiro.converter_para_brl",
            return_value=ConversionResult(5.0, "BRL", 5.0, 1.0, "2026-10-29"),
        ):
            return add_transaction.invoke(
                {
                    "amount": 5,
                    "currency": "BRL",
                    "source_text": marker,
                    "type_name": "EXPENSES",
                    "description": marker,
                },
                config=config,
            )
    if name == "update_transaction":
        return update_transaction.invoke(
            {"id": 301, "description": marker},
            config=config,
        )
    if name == "add_event":
        return add_event.invoke(
            {
                "title": marker,
                "source_text": marker,
                "start_time": "2026-10-29T14:00:00-03:00",
                "end_time": "2026-10-29T15:00:00-03:00",
            },
            config=config,
        )
    raise AssertionError(name)


class TurnWriteInvariantTests(unittest.TestCase):
    WRITES = ("add_transaction", "update_transaction", "add_event")

    def _turn(self, marker, **connection_options):
        turn = register_turn()
        connection = _Connection(marker, **connection_options)
        turn.connection = connection
        self.addCleanup(unregister_turn, turn)
        return turn, connection

    def test_aprovacao_confirma_uma_vez_nas_tres_tools(self):
        for name in self.WRITES:
            with self.subTest(tool=name):
                marker = f"APROVA-{name}"
                turn, connection = self._turn(marker)

                result = _invoke_write(name, marker, _config(turn))

                self.assertEqual(result["status"], "ok")
                self.assertEqual(connection.commits, 0, "a tool não pode confirmar antes do juiz")
                self.assertEqual([item["name"] for item in turn.writes], [name])
                self.assertEqual(turn.finish(True), "committed")
                self.assertEqual(connection.commits, 1)
                self.assertEqual(connection.rollbacks, 0)
                self.assertEqual(connection.closes, 1)

    def test_recusa_desfaz_as_tres_tools(self):
        for name in self.WRITES:
            with self.subTest(tool=name):
                marker = f"RECUSA-{name}"
                turn, connection = self._turn(marker)

                result = _invoke_write(name, marker, _config(turn))

                self.assertEqual(result["status"], "ok")
                self.assertEqual(connection.commits, 0)
                self.assertEqual(turn.finish(False), "rolled_back")
                self.assertEqual(connection.commits, 0)
                self.assertEqual(connection.rollbacks, 1)
                self.assertEqual(connection.closes, 1)

    def test_erro_sql_aborta_e_desfaz_as_tres_tools(self):
        for name in self.WRITES:
            with self.subTest(tool=name):
                marker = f"ERRO-SQL-{name}"
                turn, connection = self._turn(marker, fail_sql=True)

                result = _invoke_write(name, marker, _config(turn))

                self.assertEqual(result["status"], "error")
                self.assertTrue(turn.aborted)
                self.assertEqual(turn.finish(True), "rolled_back")
                self.assertEqual(connection.commits, 0)
                self.assertEqual(connection.rollbacks, 1)

    def test_excecao_depois_do_sql_aborta_e_desfaz_as_tres_tools(self):
        for name in self.WRITES:
            with self.subTest(tool=name):
                marker = f"POS-SQL-{name}"
                turn, connection = self._turn(marker, fail_after_sql=True)

                result = _invoke_write(name, marker, _config(turn))

                self.assertEqual(result["status"], "error")
                self.assertTrue(turn.aborted)
                self.assertEqual(turn.finish(False), "rolled_back")
                self.assertEqual(connection.commits, 0)
                self.assertEqual(connection.rollbacks, 1)

    def test_finalizacao_nao_pode_confirmar_duas_vezes(self):
        turn, connection = self._turn("REPETICAO-MESMO-TURNO")
        _invoke_write("add_event", "REPETICAO-MESMO-TURNO", _config(turn))

        self.assertEqual(turn.finish(True), "committed")
        with self.assertRaises(RuntimeError):
            turn.finish(True)
        self.assertEqual(connection.commits, 1)

    def test_turnos_concorrentes_nao_compartilham_conexao(self):
        first, first_connection = self._turn("CONCORRENTE-A")
        second, second_connection = self._turn("CONCORRENTE-B")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda item: _invoke_write("add_event", item[0], _config(item[1])),
                (("CONCORRENTE-A", first), ("CONCORRENTE-B", second)),
            ))

        self.assertTrue(all(item["status"] == "ok" for item in results))
        self.assertEqual(first.finish(True), "committed")
        self.assertEqual(second.finish(False), "rolled_back")
        self.assertEqual((first_connection.commits, first_connection.rollbacks), (1, 0))
        self.assertEqual((second_connection.commits, second_connection.rollbacks), (0, 1))


if __name__ == "__main__":
    unittest.main()
