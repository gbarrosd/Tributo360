"""Gravação de auditoria. Use `registrar` em toda criação/edição/remoção feita pelo usuário."""

from django.forms.models import model_to_dict

from .models import Auditoria


def snapshot(obj, campos=None):
    """Dicionário serializável com os campos simples do objeto (M2M fica de fora)."""
    if obj is None:
        return None
    dados = model_to_dict(obj, fields=campos)
    return {
        chave: (str(valor) if hasattr(valor, "hex") else valor)
        for chave, valor in dados.items()
        if not isinstance(valor, list)
    }


def ip_da_requisicao(request):
    from django.conf import settings

    if getattr(settings, "CONFIAR_X_REAL_IP", False) and request.META.get("HTTP_X_REAL_IP"):
        return request.META["HTTP_X_REAL_IP"]
    return request.META.get("REMOTE_ADDR")


def registrar(request, acao, obj, antes=None, depois=None, escritorio=None):
    usuario = getattr(request, "user", None)
    if escritorio is None:
        escritorio = request.membership.escritorio
    return Auditoria.objects.create(
        escritorio=escritorio,
        usuario=usuario if usuario and usuario.is_authenticated else None,
        acao=acao,
        objeto_tipo=obj._meta.label_lower,
        objeto_id=str(obj.pk),
        antes=antes,
        depois=depois,
        ip=ip_da_requisicao(request),
    )
