"""Funções compartilhadas pelos clientes."""

from decimal import Decimal, InvalidOperation

from ..contratos import VersaoInfo

# Headers que os dois motores Java põem em toda resposta de cálculo.
CABECALHO_VERSAO_APP = "X-CALC-APP-VERSION"
CABECALHO_VERSAO_DB = "X-CALC-DB-VERSION"
CABECALHO_SIMULADO = "x-warning-dados-simulados"


def texto(valor):
    return "" if valor is None else str(valor)


def decimal_ou_none(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return None


def versao_dos_cabecalhos(motor, cabecalhos):
    return VersaoInfo(
        motor=motor,
        versao_app=texto(cabecalhos.get(CABECALHO_VERSAO_APP)),
        versao_db=texto(cabecalhos.get(CABECALHO_VERSAO_DB)),
    )


def cabecalho_simulado(cabecalhos):
    return texto(cabecalhos.get(CABECALHO_SIMULADO))
