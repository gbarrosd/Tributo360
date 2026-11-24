from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions
from rest_framework.views import exception_handler

from engines.http import MotorIndisponivel, OperacaoRecusada

from .permissions import TemEscritorio
from .tenancy import SemEscopoDeEscritorio


class ServicoIndisponivel(exceptions.APIException):
    status_code = 503
    default_detail = "Motor de cálculo indisponível. Tente de novo em instantes."
    default_code = "motor_indisponivel"


class OperacaoNaoProcessavel(exceptions.APIException):
    status_code = 422
    default_detail = "O motor recusou a operação."
    default_code = "operacao_recusada"


def handler(exc, context):
    """Converte erros de domínio em respostas da API."""
    if isinstance(exc, SemEscopoDeEscritorio):
        exc = exceptions.PermissionDenied(TemEscritorio.message)
    elif isinstance(exc, DjangoValidationError):
        detalhe = exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages}
        exc = exceptions.ValidationError(detalhe)
    elif isinstance(exc, MotorIndisponivel):
        resposta = exception_handler(ServicoIndisponivel(exc.mensagem), context)
        resposta.data["motor"] = str(exc.motor)
        return resposta
    elif isinstance(exc, OperacaoRecusada):
        resposta = exception_handler(OperacaoNaoProcessavel(exc.mensagem), context)
        resposta.data.update({"motor": str(exc.motor), "erros": [e.como_dict() for e in exc.erros]})
        return resposta
    return exception_handler(exc, context)
