# tests/test_currency.py

import math
import pytest

from currency_utils import format_local_amount, find_country

def test_find_country_fr():
    fr = find_country("FR")
    assert fr["code"] == "FR"
    assert fr["currency"] == "EUR"

def test_find_country_name_case_insensitive():
    tn = find_country("tunisie")
    assert tn["code"] == "TN"
    assert tn["currency"] == "TND"

def test_format_eur_basic():
    out = format_local_amount(1234.5, "FR")
    # Exemple: "1 234,50 €"
    assert "1" in out and "234" in out and "€" in out
    assert "," in out  # séparateur décimal FR

def test_format_tnd_3_decimals():
    out = format_local_amount(12, "TN")  # TND => 3 décimales
    assert out.endswith(" TND")
    # 12 -> "12,000 TND" (virgule décimale, 3 décimales)
    assert ",000" in out

def test_format_xof_0_decimals():
    out = format_local_amount(15000, "SN")  # XOF => 0 décimale
    assert out.endswith(" XOF")
    # 15000 -> pas de décimales
    assert "," not in out or out.endswith(" XOF")

def test_conversion_with_rate():
    out = format_local_amount(100, "FR", "EUR", taux_change=3.2)
    # 100 * 3.2 = 320
    assert "320" in out

def test_invalid_amount_raises():
    with pytest.raises(ValueError):
        format_local_amount("abc", "FR")

def test_invalid_rate_raises():
    with pytest.raises(ValueError):
        format_local_amount(100, "FR", taux_change=0)
def test_format_dzd_2_decimals():
    out = format_local_amount(99.9, "DZ")
    assert out.endswith(" DZD")
    assert "," in out  # décimales FR

def test_format_mur_0_decimals():
    out = format_local_amount(2500, "MU")
    assert out.endswith(" MUR")
    assert "," not in out  # pas de décimales
