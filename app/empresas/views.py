from django.db.models import Q
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from contas.papeis import Acao
from core.permissions import PapelPermite, TemEscritorio

from . import servicos
from .models import Empresa
from .serializers import EmpresaSerializer


class EmpresaViewSet(viewsets.ModelViewSet):
    """CRUD de empresas. DELETE inativa (não apaga)."""

    serializer_class = EmpresaSerializer
    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    acoes = {
        "list": Acao.VER,
        "retrieve": Acao.VER,
        "create": Acao.EDITAR_EMPRESAS,
        "update": Acao.EDITAR_EMPRESAS,
        "partial_update": Acao.EDITAR_EMPRESAS,
        "destroy": Acao.EDITAR_EMPRESAS,
    }

    def get_queryset(self):
        qs = Empresa.tenant(self.request)
        params = self.request.query_params
        if params.get("ativa") in {"true", "false"}:
            qs = qs.filter(ativa=params["ativa"] == "true")
        if busca := params.get("q", "").strip():
            qs = qs.filter(
                Q(razao_social__icontains=busca) | Q(nome_fantasia__icontains=busca) | Q(cnpj__icontains=busca)
            )
        return qs

    def perform_create(self, serializer):
        servicos.criar_empresa(self.request, serializer)

    def perform_update(self, serializer):
        servicos.atualizar_empresa(self.request, serializer)

    def perform_destroy(self, instance):
        servicos.inativar_empresa(self.request, instance)
