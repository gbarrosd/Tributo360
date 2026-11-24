"""Isolamento multi-tenant.

Banco compartilhado, isolamento lógico por escritório. Regras (planejamento, seção 2.2):

1. Toda tabela de negócio herda de `TenantModel`.
2. Views e serializers nunca usam `Model.objects` de um `TenantModel`: só `Model.tenant(request)`, que
   filtra pelo escritório da membership ativa **e** pelas empresas que ela pode ver.
   `core/tests/test_arquitetura.py` falha se encontrar acesso sem escopo.
3. Objeto de outro escritório responde 404 (nunca 403), para não revelar que existe.
"""

import uuid

from django.db import models


class SemEscopoDeEscritorio(Exception):
    """Consulta a dados de escritório sem membership resolvida na requisição."""


class TenantQuerySet(models.QuerySet):
    def do_escritorio(self, escritorio):
        return self.filter(escritorio=escritorio)

    def para_membership(self, membership):
        if membership is None:
            raise SemEscopoDeEscritorio("Requisição sem escritório selecionado.")
        qs = self.filter(escritorio_id=membership.escritorio_id)
        campo = self.model.campo_empresa
        if campo and not membership.acesso_todas_empresas:
            qs = qs.filter(**{f"{campo}__in": membership.empresas_permitidas.values("pk")})
        return qs


class TenantModel(models.Model):
    """Base de toda tabela de negócio.

    `campo_empresa` diz qual caminho leva à `Empresa` do registro (ex.: ``"empresa"``), para aplicar a
    restrição de empresas de uma membership com acesso parcial. ``None`` = registro do escritório, não
    de uma empresa específica.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    escritorio = models.ForeignKey("contas.Escritorio", on_delete=models.PROTECT, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    campo_empresa = None

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True

    @classmethod
    def tenant(cls, request):
        """Queryset já filtrado para a membership ativa da requisição."""
        return cls.objects.para_membership(getattr(request, "membership", None))


CABECALHO_ESCRITORIO = "HTTP_X_ESCRITORIO"
SESSAO_ESCRITORIO = "escritorio_id"


def resolver_membership(request):
    """Escolhe a membership ativa: cabeçalho X-Escritorio, depois sessão, depois a única existente.

    Devolve ``None`` quando o usuário não tem acesso ao escritório pedido ou precisa escolher um.
    """
    from contas.models import Escritorio, Membership

    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return None

    ativas = (
        Membership.objects.filter(usuario=usuario, ativo=True)
        .exclude(escritorio__status=Escritorio.Status.CANCELADO)
        .select_related("escritorio")
    )
    escolhido = request.META.get(CABECALHO_ESCRITORIO) or request.session.get(SESSAO_ESCRITORIO)
    if escolhido:
        try:
            uuid.UUID(str(escolhido))
        except ValueError:
            return None
        return ativas.filter(escritorio_id=escolhido).first()

    candidatas = list(ativas[:2])
    return candidatas[0] if len(candidatas) == 1 else None


class TenantMiddleware:
    """Coloca `request.membership` (ou ``None``) em toda requisição autenticada."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.membership = resolver_membership(request)
        return self.get_response(request)
