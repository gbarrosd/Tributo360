"""Catálogo de produtos e serviços: lista com fila de pendentes, cadastro, importação e aprovação."""

import uuid

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework.exceptions import ValidationError

from catalogo import servicos
from catalogo.models import ItemCatalogo
from catalogo.serializers import ItemCatalogoSerializer
from contas.papeis import Acao

from ..acesso import destino_seguro, tela
from ..forms import ImportacaoForm, ItemCatalogoForm, aplicar_erros
from .empresas import empresa_da_requisicao


def item_da_requisicao(request, pk):
    return get_object_or_404(ItemCatalogo.tenant(request).select_related("empresa"), pk=pk)


@tela()
def lista(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    todos = ItemCatalogo.tenant(request).filter(empresa=empresa, ativo=True)
    status = request.GET.get("status", "")
    qs = todos.select_related("aprovado_por")
    if status in ItemCatalogo.Status.values:
        qs = qs.filter(status_classificacao=status)
    elif status == "fila":
        qs = qs.exclude(status_classificacao=ItemCatalogo.Status.APROVADO)
    busca = request.GET.get("q", "").strip()
    if busca:
        qs = qs.filter(Q(descricao__icontains=busca) | Q(codigo__icontains=busca))
    contagem = {s: todos.filter(status_classificacao=s).count() for s in ItemCatalogo.Status.values}
    return render(
        request,
        "web/catalogo/lista.html",
        {
            "empresa": empresa,
            "itens": qs[:500],
            "status": status,
            "busca": busca,
            "contagem": contagem,
            "fila": contagem["pendente"] + contagem["revisar"],
        },
    )


def _salvar(request, form, empresa, item=None):
    dados = {**form.cleaned_data, "empresa": str(empresa.pk)}
    serializer = ItemCatalogoSerializer(
        instance=item, data=dados, partial=item is not None, context={"request": request}
    )
    if not serializer.is_valid():
        aplicar_erros(form, serializer.errors)
        return None
    validados = dict(serializer.validated_data)
    validados.pop("empresa", None)
    if item is None:
        return servicos.criar_item(request, empresa, validados)
    return servicos.atualizar_item(request, item, validados)


def _mensagem_validacao(request, item):
    if item.status_classificacao == ItemCatalogo.Status.REVISAR:
        messages.warning(request, f"Item {item.codigo} salvo, mas a classificação precisa de revisão.")
    else:
        messages.success(request, f"Item {item.codigo} salvo.")


@tela(Acao.CADASTRAR_ITENS)
def novo(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    form = ItemCatalogoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = _salvar(request, form, empresa)
        if item:
            _mensagem_validacao(request, item)
            return redirect("web:item-editar", pk=item.pk)
    return render(request, "web/catalogo/form.html", {"form": form, "empresa": empresa, "item": None})


@tela(Acao.CADASTRAR_ITENS)
def editar(request, pk):
    item = item_da_requisicao(request, pk)
    inicial = {campo: getattr(item, campo) for campo in ItemCatalogoForm.base_fields}
    form = ItemCatalogoForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        salvo = _salvar(request, form, item.empresa, item)
        if salvo:
            _mensagem_validacao(request, salvo)
            return redirect("web:item-editar", pk=salvo.pk)
    return render(request, "web/catalogo/form.html", {"form": form, "empresa": item.empresa, "item": item})


@tela(Acao.CADASTRAR_ITENS)
@require_POST
def revalidar(request, pk):
    item = item_da_requisicao(request, pk)
    resumo = servicos.validar([item])
    if resumo["motor_indisponivel"]:
        messages.warning(request, "Motor indisponível: só as validações locais rodaram.")
    _mensagem_validacao(request, item)
    return redirect("web:item-editar", pk=item.pk)


@tela(Acao.CADASTRAR_ITENS)
@require_POST
def inativar(request, pk):
    item = item_da_requisicao(request, pk)
    servicos.atualizar_item(request, item, {"ativo": False})
    messages.success(request, f"Item {item.codigo} retirado do catálogo.")
    return redirect("web:catalogo", pk=item.empresa_id)


@tela(Acao.APROVAR_CLASSIFICACAO)
@require_POST
def aprovar(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    ids = request.POST.getlist("itens")
    itens = list(ItemCatalogo.tenant(request).filter(empresa=empresa, pk__in=_uuids(ids)))
    aprovados, recusados = servicos.aprovar_itens(request, itens)
    if aprovados:
        messages.success(request, f"{len(aprovados)} item(ns) aprovado(s).")
    if recusados:
        messages.warning(
            request,
            f"{len(recusados)} item(ns) com erro na classificação não foram aprovados: corrija antes.",
        )
    if not itens:
        messages.info(request, "Selecione ao menos um item.")
    return redirect(destino_seguro(request, request.POST.get("proximo"), f"/empresas/{empresa.pk}/itens/?status=fila"))


def _uuids(valores):
    validos = []
    for valor in valores:
        try:
            validos.append(uuid.UUID(valor))
        except ValueError:
            continue
    return validos


@tela(Acao.CADASTRAR_ITENS)
def importar(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    form = ImportacaoForm(request.POST or None, request.FILES or None)
    resumo = None
    if request.method == "POST" and form.is_valid():
        arquivo = form.cleaned_data["arquivo"]
        try:
            resumo = servicos.importar_planilha(
                request, empresa, arquivo.name, arquivo.read(servicos.MAX_BYTES_PLANILHA + 1)
            )
        except ValidationError as exc:
            aplicar_erros(form, exc.detail)
    return render(request, "web/catalogo/importar.html", {"form": form, "empresa": empresa, "resumo": resumo})


@tela()
def modelo(request):
    resposta = HttpResponse(servicos.modelo_csv(), content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = 'attachment; filename="modelo-catalogo-tributo360.csv"'
    return resposta
