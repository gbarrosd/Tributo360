from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from contas.papeis import Acao
from core.permissions import PapelPermite, TemEscritorio

from . import servicos
from .models import AliquotaEfetivaSN, Simulacao
from .serializers import (
    AliquotaEfetivaSNSerializer,
    SimulacaoDetalheSerializer,
    SimulacaoRegularSerializer,
    SimulacaoResumoSerializer,
    SimulacaoSimplesSerializer,
)


class SimulacaoViewSet(viewsets.ReadOnlyModelViewSet):
    """Histórico de simulações e os dois cálculos: regime regular e Simples Nacional.

    Simulação não se edita nem se apaga: é o registro do que o motor respondeu naquela versão.
    """

    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    acoes = {
        "list": Acao.VER,
        "retrieve": Acao.VER,
        "regime_regular": Acao.SIMULAR,
        "simples": Acao.SIMULAR,
    }

    def get_queryset(self):
        qs = Simulacao.tenant(self.request).select_related("versao_motor", "empresa")
        params = self.request.query_params
        if empresa := params.get("empresa"):
            qs = qs.filter(empresa_id=empresa)
        if tipo := params.get("tipo"):
            qs = qs.filter(tipo=tipo)
        if self.action == "retrieve":
            qs = qs.prefetch_related("itens")
        return qs

    def get_serializer_class(self):
        if self.action == "regime_regular":
            return SimulacaoRegularSerializer
        if self.action == "simples":
            return SimulacaoSimplesSerializer
        if self.action == "retrieve":
            return SimulacaoDetalheSerializer
        return SimulacaoResumoSerializer

    def _calcular(self, request, funcao):
        entrada = self.get_serializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        simulacao = funcao(request, entrada.validated_data, request.data)
        simulacao = (
            Simulacao.tenant(request).prefetch_related("itens").select_related("versao_motor").get(pk=simulacao.pk)
        )
        return Response(SimulacaoDetalheSerializer(simulacao).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="regime-regular")
    def regime_regular(self, request):
        return self._calcular(request, servicos.simular_regime_regular)

    @action(detail=False, methods=["post"])
    def simples(self, request):
        return self._calcular(request, servicos.simular_simples)


class AliquotaEfetivaSNViewSet(viewsets.ModelViewSet):
    """Alíquotas efetivas do Simples por empresa e mês (D3), usadas por padrão na simulação do Simples."""

    serializer_class = AliquotaEfetivaSNSerializer
    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    acoes = {
        "list": Acao.VER,
        "retrieve": Acao.VER,
        "create": Acao.INFORMAR_ALIQUOTAS_SN,
        "update": Acao.INFORMAR_ALIQUOTAS_SN,
        "partial_update": Acao.INFORMAR_ALIQUOTAS_SN,
        "destroy": Acao.INFORMAR_ALIQUOTAS_SN,
    }

    def get_queryset(self):
        qs = AliquotaEfetivaSN.tenant(self.request).select_related("informado_por")
        if empresa := self.request.query_params.get("empresa"):
            qs = qs.filter(empresa_id=empresa)
        return qs

    def _salvar(self, serializer):
        dados = {
            "periodo": serializer.instance.periodo if serializer.instance else None,
            "fator_r": getattr(serializer.instance, "fator_r", False),
            "impedimento_sublimite": getattr(serializer.instance, "impedimento_sublimite", False),
            "anexos": servicos.efetivas_do_cadastro(serializer.instance)["anexos"] if serializer.instance else {},
            "observacao": getattr(serializer.instance, "observacao", ""),
            **serializer.validated_data,
        }
        empresa = dados.pop("empresa", None) or serializer.instance.empresa
        serializer.instance = servicos.salvar_aliquotas_sn(self.request, empresa, dados, serializer.instance)

    def perform_create(self, serializer):
        self._salvar(serializer)

    def perform_update(self, serializer):
        self._salvar(serializer)

    def perform_destroy(self, instance):
        servicos.remover_aliquotas_sn(self.request, instance)
