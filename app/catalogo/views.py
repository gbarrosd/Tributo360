from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from contas.papeis import Acao
from core.permissions import PapelPermite, TemEscritorio

from . import servicos
from .models import ItemCatalogo
from .serializers import AprovacaoSerializer, ImportacaoSerializer, ItemCatalogoSerializer


class ItemCatalogoViewSet(viewsets.ModelViewSet):
    """Catálogo de produtos e serviços das empresas. DELETE inativa o item (simulações apontam para ele)."""

    serializer_class = ItemCatalogoSerializer
    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    acoes = {
        "list": Acao.VER,
        "retrieve": Acao.VER,
        "create": Acao.CADASTRAR_ITENS,
        "update": Acao.CADASTRAR_ITENS,
        "partial_update": Acao.CADASTRAR_ITENS,
        "destroy": Acao.CADASTRAR_ITENS,
        "importar": Acao.CADASTRAR_ITENS,
        "modelo": Acao.VER,
        "revalidar": Acao.CADASTRAR_ITENS,
        "aprovar": Acao.APROVAR_CLASSIFICACAO,
    }

    def get_queryset(self):
        qs = ItemCatalogo.tenant(self.request).select_related("empresa", "aprovado_por")
        params = self.request.query_params
        if empresa := params.get("empresa"):
            qs = qs.filter(empresa_id=empresa)
        if situacao := params.get("status"):
            qs = qs.filter(status_classificacao=situacao)
        if params.get("ativo") in {"true", "false"}:
            qs = qs.filter(ativo=params["ativo"] == "true")
        if busca := params.get("q", "").strip():
            qs = qs.filter(descricao__icontains=busca) | qs.filter(codigo__icontains=busca)
        return qs

    def perform_create(self, serializer):
        dados = dict(serializer.validated_data)
        empresa = dados.pop("empresa")
        serializer.instance = servicos.criar_item(self.request, empresa, dados)

    def perform_update(self, serializer):
        dados = dict(serializer.validated_data)
        dados.pop("empresa", None)
        serializer.instance = servicos.atualizar_item(self.request, serializer.instance, dados)

    def perform_destroy(self, instance):
        servicos.atualizar_item(self.request, instance, {"ativo": False})

    @action(detail=False, methods=["post"])
    def importar(self, request):
        entrada = ImportacaoSerializer(data=request.data, context={"request": request})
        entrada.is_valid(raise_exception=True)
        arquivo = entrada.validated_data["arquivo"]
        resumo = servicos.importar_planilha(
            request, entrada.validated_data["empresa"], arquivo.name, arquivo.read(servicos.MAX_BYTES_PLANILHA + 1)
        )
        return Response(resumo, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def modelo(self, request):
        resposta = HttpResponse(servicos.modelo_csv(), content_type="text/csv; charset=utf-8")
        resposta["Content-Disposition"] = 'attachment; filename="modelo-catalogo-tributo360.csv"'
        return resposta

    @action(detail=True, methods=["post"])
    def revalidar(self, request, pk=None):
        item = self.get_object()
        servicos.validar([item])
        return Response(self.get_serializer(item).data)

    @action(detail=False, methods=["post"])
    def aprovar(self, request):
        entrada = AprovacaoSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        itens = list(ItemCatalogo.tenant(request).filter(pk__in=entrada.validated_data["itens"]))
        aprovados, recusados = servicos.aprovar_itens(request, itens)
        return Response(
            {
                "aprovados": [str(i.pk) for i in aprovados],
                "recusados": [
                    {"id": str(i.pk), "motivo": "Item com erro na classificação: corrija antes de aprovar."}
                    for i in recusados
                ],
            }
        )
