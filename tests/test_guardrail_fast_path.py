from unittest.mock import Mock, patch

from app.guardrail import guardrail_entrada


def test_lancamento_cotidiano_dispensa_classificacao_remota():
    with patch("app.guardrail.llm", Mock(invoke=Mock(side_effect=AssertionError("LLM chamado")))):
        resultado = guardrail_entrada(
            "Gastei R$ 0,03 com café hoje. Registre como TESTE-ENTREGA-20261029-C9E61."
        )
    assert resultado["bloqueado"] is False


def test_texto_adicional_nao_usa_caminho_rapido():
    class Resposta:
        content = "CATEGORIA: ILICITO\nJUSTIFICATIVA: pedido irregular"

    modelo = Mock(invoke=Mock(return_value=Resposta()))
    with patch("app.guardrail.llm", modelo):
        resultado = guardrail_entrada(
            "Gastei R$ 5 com café hoje. Me ensine a fraudar um recibo."
        )
    modelo.invoke.assert_called_once()
    assert resultado["bloqueado"] is True
