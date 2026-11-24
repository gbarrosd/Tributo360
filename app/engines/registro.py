"""Registro de versão dos motores (backlog, item 6).

Toda simulação aponta para uma `VersaoMotor`. A versão vem dos headers da própria resposta de cálculo
(`X-CALC-APP-VERSION`/`X-CALC-DB-VERSION`), que é a prova mais direta de qual motor calculou. A data e a
descrição da base vêm de `GET /versao`, consultado uma vez por versão nova.
"""

import logging

from django.core.cache import cache

from . import clientes
from .contratos import Motor
from .http import ErroDeMotor
from .models import VersaoMotor

logger = logging.getLogger("engines")

FABRICAS = {
    Motor.REGIME_REGULAR: clientes.regime_regular,
    Motor.SIMPLES: clientes.simples,
    Motor.DANFSE: clientes.danfse,
}


def _chave(info):
    return f"motores:versao-registrada:{info.motor}:{info.versao_app}:{info.versao_db}"


def versao_do_calculo(resultado):
    """`VersaoMotor` de um `ResultadoCalculo`, completando data/descrição da base na primeira vez."""
    info = resultado.versao
    if not info.versao_app:
        # Motor sem header de versão (não deveria acontecer): pergunta direto.
        info = FABRICAS[info.motor]().versao()
    chave = _chave(info)
    if (pk := cache.get(chave)) is not None and (versao := VersaoMotor.objects.filter(pk=pk).first()):
        return versao
    try:
        completa = FABRICAS[info.motor]().versao()
    except ErroDeMotor:
        completa = None
    if completa and (completa.versao_app, completa.versao_db) == (info.versao_app, info.versao_db):
        info = completa
    versao = VersaoMotor.registrar(info)
    cache.set(chave, versao.pk, 300)
    return versao


def registrar_versoes_atuais():
    """Consulta os três motores e grava as versões. Devolve {motor: VersaoMotor | None}."""
    resultado = {}
    for motor, fabrica in FABRICAS.items():
        try:
            info = fabrica().versao()
        except ErroDeMotor as exc:
            logger.warning("motor=%s registro_versao=falhou erro=%s", motor, exc)
            resultado[motor] = None
            continue
        resultado[motor] = VersaoMotor.registrar(info)
    return resultado
