import pytest
from django.core.exceptions import ValidationError

from core.validators import cnpj_valido, cpf_valido, validar_cpf_ou_cnpj, validar_municipio_da_uf


@pytest.mark.parametrize(
    "cnpj",
    [
        "11.222.333/0001-81",
        "11222333000181",
        "12.ABC.345/01DE-35",  # exemplo oficial de CNPJ alfanumérico da RFB
        "12abc34501de35",  # minúsculas são normalizadas
    ],
)
def test_cnpj_valido(cnpj):
    assert cnpj_valido(cnpj)


@pytest.mark.parametrize(
    "cnpj",
    ["11222333000182", "00000000000000", "AAAAAAAAAAAAAA", "12ABC34501DE3A", "1122233300018", "", None],
)
def test_cnpj_invalido(cnpj):
    assert not cnpj_valido(cnpj)


def test_cpf():
    assert cpf_valido("529.982.247-25")
    assert not cpf_valido("529.982.247-26")
    assert not cpf_valido("111.111.111-11")


def test_documento_do_titular_aceita_cpf_ou_cnpj():
    validar_cpf_ou_cnpj("52998224725")
    validar_cpf_ou_cnpj("12ABC34501DE35")
    with pytest.raises(ValidationError):
        validar_cpf_ou_cnpj("123")


def test_municipio_precisa_ser_da_uf():
    validar_municipio_da_uf("3550308", "SP")
    with pytest.raises(ValidationError):
        validar_municipio_da_uf("3304557", "SP")
    with pytest.raises(ValidationError):
        validar_municipio_da_uf("355030", "SP")
