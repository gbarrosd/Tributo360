from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions
from rest_framework.views import exception_handler

from .tenancy import SemEscopoDeEscritorio


def handler(exc, context):
    """Converte erros de domínio em respostas da API."""
    if isinstance(exc, SemEscopoDeEscritorio):
        exc = exceptions.PermissionDenied("Selecione um escritório (cabeçalho X-Escritorio).")
    elif isinstance(exc, DjangoValidationError):
        detalhe = exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages}
        exc = exceptions.ValidationError(detalhe)
    return exception_handler(exc, context)
