"""Regras de negócio de empresas que precisam olhar o escritório inteiro (não só o que a membership vê)."""

from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from auditoria.servicos import registrar, snapshot
from contas.models import Assinatura

from .models import Empresa


class LimiteDoPlano(PermissionDenied):
    default_code = "limite_do_plano"


def cnpj_em_uso(escritorio, cnpj, ignorar_pk=None):
    qs = Empresa.objects.do_escritorio(escritorio).filter(cnpj=cnpj)
    if ignorar_pk:
        qs = qs.exclude(pk=ignorar_pk)
    return qs.exists()


def _checar_limite(escritorio):
    """Trava a assinatura (evita corrida entre duas criações) e confere o limite de empresas ativas."""
    assinatura = Assinatura.objects.select_for_update().get(escritorio=escritorio)
    ativas = Empresa.objects.do_escritorio(escritorio).filter(ativa=True).count()
    if ativas >= assinatura.limite_empresas:
        raise LimiteDoPlano(
            f"O plano permite {assinatura.limite_empresas} empresas ativas. Inative uma empresa ou mude de plano."
        )


@transaction.atomic
def criar_empresa(request, serializer):
    membership = request.membership
    if serializer.validated_data.get("ativa", True):
        _checar_limite(membership.escritorio)
    empresa = serializer.save(escritorio=membership.escritorio)
    if not membership.acesso_todas_empresas:
        # Quem tem acesso restrito precisa continuar vendo a empresa que acabou de cadastrar.
        empresa.membros_restritos.add(membership)
    registrar(request, "empresa.criar", empresa, depois=snapshot(empresa))
    return empresa


@transaction.atomic
def atualizar_empresa(request, serializer):
    empresa = serializer.instance
    antes = snapshot(empresa)
    if not empresa.ativa and serializer.validated_data.get("ativa") is True:
        _checar_limite(request.membership.escritorio)
    empresa = serializer.save()
    registrar(request, "empresa.editar", empresa, antes=antes, depois=snapshot(empresa))
    return empresa


@transaction.atomic
def inativar_empresa(request, empresa):
    """Empresa não é apagada: histórico de simulações e documentos depende dela."""
    antes = snapshot(empresa)
    empresa.ativa = False
    empresa.save(update_fields=["ativa", "atualizado_em"])
    registrar(request, "empresa.inativar", empresa, antes=antes, depois=snapshot(empresa))
