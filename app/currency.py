"""Detecção de moeda e conversão de valores para reais (BRL)."""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FX_API_BASE_URL = "https://api.frankfurter.dev/v2"
FX_TIMEOUT_SECONDS = 8
FX_CACHE_SECONDS = 6 * 60 * 60


class CurrencyConversionError(RuntimeError):
    """Falha segura: nenhuma transação deve ser gravada sem uma cotação válida."""


@dataclass(frozen=True)
class ConversionResult:
    original_amount: float
    original_currency: str
    amount_brl: float
    exchange_rate: float
    rate_date: str


_ALIASES = {
    "BRL": ("real", "reais", "brl", "r$"),
    "USD": ("dolar", "dolares", "dollar", "dollars", "usd", "us$"),
    "EUR": ("euro", "euros", "eur", "€"),
    "GBP": ("libra", "libras", "libra esterlina", "libras esterlinas", "pound", "pounds", "gbp", "£"),
    "JPY": ("iene", "ienes", "yen", "jpy", "¥"),
    "CAD": ("dolar canadense", "dolares canadenses", "cad"),
    "AUD": ("dolar australiano", "dolares australianos", "aud"),
    "CHF": ("franco suico", "francos suicos", "chf"),
    "CNY": ("yuan", "renminbi", "cny"),
    "ARS": ("peso argentino", "pesos argentinos", "ars"),
    "CLP": ("peso chileno", "pesos chilenos", "clp"),
    "COP": ("peso colombiano", "pesos colombianos", "cop"),
    "MXN": ("peso mexicano", "pesos mexicanos", "mxn"),
    "UYU": ("peso uruguaio", "pesos uruguaios", "uyu"),
    "PYG": ("guarani", "guaranis", "pyg"),
    "PEN": ("sol peruano", "soles peruanos", "pen"),
    "NZD": ("dolar neozelandes", "dolares neozelandeses", "nzd"),
    "KRW": ("won", "won sul-coreano", "krw"),
    "INR": ("rupia indiana", "rupias indianas", "inr"),
    "RUB": ("rublo", "rublos", "rub"),
    "ZAR": ("rand", "rands", "zar"),
}

# Lista ISO 4217 usada somente para reconhecer códigos escritos em maiúsculas.
# A whitelist evita interpretar meios de pagamento brasileiros, como PIX, como
# se fossem moedas. A disponibilidade final ainda é validada pela API de câmbio.
_ISO_CODES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB
BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC
CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF
GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF
KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU
MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR
PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP
STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU
UYW UZS VED VES VND VUV WST XAF XAG XAU XBA XBB XBC XBD XCD XDR XOF XPD
XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWL
""".split())

_CACHE: dict[str, tuple[float, float, str]] = {}
_CACHE_LOCK = threading.Lock()


def _sem_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(caractere for caractere in normalizado if not unicodedata.combining(caractere)).casefold()


def normalizar_codigo_moeda(moeda: str) -> str | None:
    """Converte código ou nome comum de moeda em um código ISO 4217."""
    if not isinstance(moeda, str) or not moeda.strip():
        return None
    original = moeda.strip()
    if re.fullmatch(r"[A-Za-z]{3}", original):
        return original.upper()
    procurada = _sem_acentos(original)
    for codigo, aliases in _ALIASES.items():
        if procurada in {_sem_acentos(alias) for alias in aliases}:
            return codigo
    return None


def detectar_moeda(texto: str) -> str:
    """Infere a moeda do texto; sem indicação explícita, assume BRL."""
    if not isinstance(texto, str):
        return "BRL"

    # Códigos ISO em maiúsculas são inequívocos e permitem moedas além da
    # lista de nomes traduzidos, por exemplo NOK, SEK ou AED.
    for codigo in re.findall(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])", texto):
        if codigo in _ISO_CODES and codigo != "BRL":
            return codigo

    normalizado = _sem_acentos(texto)
    # Nomes compostos vêm primeiro para evitar que "dólar canadense" seja USD.
    candidatos = sorted(
        (
            (_sem_acentos(alias), codigo)
            for codigo, aliases in _ALIASES.items()
            for alias in aliases
            if codigo != "BRL"
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for alias, codigo in candidatos:
        if alias in {"€", "£", "¥", "us$"}:
            if alias in normalizado:
                return codigo
        elif re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalizado):
            return codigo

    # Em um sistema brasileiro, "$" sem R ou US é interpretado como dólar.
    if re.search(r"(?<![RrSs])\$", texto):
        return "USD"
    return "BRL"


def _request_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Assessor.AI/0.1"})
    try:
        with urlopen(request, timeout=FX_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise CurrencyConversionError("Não foi possível consultar a cotação da moeda agora.") from exc
    if not isinstance(payload, dict):
        raise CurrencyConversionError("A fonte de câmbio devolveu uma resposta inválida.")
    return payload


def obter_cotacao_brl(codigo_moeda: str) -> tuple[float, str]:
    """Retorna quantos reais valem uma unidade da moeda e a data da cotação."""
    codigo = normalizar_codigo_moeda(codigo_moeda)
    if codigo is None:
        raise CurrencyConversionError("Moeda não reconhecida; informe o código ISO, como USD, EUR ou GBP.")
    if codigo == "BRL":
        return 1.0, "local"

    agora = time.monotonic()
    with _CACHE_LOCK:
        cache = _CACHE.get(codigo)
        if cache and agora - cache[0] < FX_CACHE_SECONDS:
            return cache[1], cache[2]

    payload = _request_json(f"{FX_API_BASE_URL}/rate/{codigo}/BRL")
    try:
        taxa = float(payload["rate"])
        data_cotacao = str(payload["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CurrencyConversionError("A fonte de câmbio não devolveu uma cotação válida para essa moeda.") from exc
    if taxa <= 0:
        raise CurrencyConversionError("A fonte de câmbio devolveu uma cotação inválida para essa moeda.")

    with _CACHE_LOCK:
        _CACHE[codigo] = (agora, taxa, data_cotacao)
    return taxa, data_cotacao


def converter_para_brl(valor: float, moeda: str) -> ConversionResult:
    """Converte um valor positivo para BRL, arredondando em centavos."""
    codigo = normalizar_codigo_moeda(moeda)
    if codigo is None:
        raise CurrencyConversionError("Moeda não reconhecida; informe o código ISO, como USD, EUR ou GBP.")
    try:
        original = Decimal(str(valor))
    except (InvalidOperation, ValueError) as exc:
        raise CurrencyConversionError("O valor informado não é válido para conversão.") from exc
    if not original.is_finite() or original <= 0:
        raise CurrencyConversionError("O valor informado deve ser maior que zero.")

    taxa, data_cotacao = obter_cotacao_brl(codigo)
    convertido = (original * Decimal(str(taxa))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ConversionResult(
        original_amount=float(original),
        original_currency=codigo,
        amount_brl=float(convertido),
        exchange_rate=taxa,
        rate_date=data_cotacao,
    )
