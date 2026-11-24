"""Carteira de empresas: lista, cadastro, edição e página da empresa."""

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from catalogo.models import ItemCatalogo
from contas.papeis import Acao
from empresas import servicos
from empresas.models import Empresa
from empresas.serializers import EmpresaSerializer
from simulacoes.models import AliquotaEfetivaSN, Simulacao

from ..acesso import tela
from ..forms import EmpresaForm, aplicar_erros


def empresa_da_requisicao(request, pk):
    """Empresa que a membership enxerga; de outro escritório (ou fora do acesso) responde 404."""
    return get_object_or_404(Empresa.tenant(request), pk=pk)


@tela()
def lista(request):
    qs = Empresa.tenant(request)
    busca = request.GET.get("q", "").strip()
    if busca:
        qs = qs.filter(Q(razao_social__icontains=busca) | Q(nome_fantasia__icontains=busca) | Q(cnpj__icontains=busca))
    situacao = request.GET.get("ativa", "true")
    if situacao in {"true", "false"}:
        qs = qs.filter(ativa=situacao == "true")
    return render(request, "web/empresas/lista.html", {"empresas": qs, "busca": busca, "situacao": situacao})


def _salvar(request, form, instancia=None):
    serializer = EmpresaSerializer(
        instance=instancia, data=form.cleaned_data, partial=instancia is not None, context={"request": request}
    )
    if not serializer.is_valid():
        aplicar_erros(form, serializer.errors)
        return None
    if instancia is None:
        return servicos.criar_empresa(request, serializer)
    return servicos.atualizar_empresa(request, serializer)


@tela(Acao.EDITAR_EMPRESAS)
def nova(request):
    form = EmpresaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        empresa = _salvar(request, form)
        if empresa:
            messages.success(request, f"Empresa {empresa.razao_social} cadastrada.")
            return redirect("web:empresa", pk=empresa.pk)
    return render(request, "web/empresas/form.html", {"form": form, "empresa": None})


@tela(Acao.EDITAR_EMPRESAS)
def editar(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    inicial = {campo: getattr(empresa, campo) for campo in EmpresaForm.base_fields}
    form = EmpresaForm(request.POST or None, initial=inicial)
    form.fields["cnpj"].disabled = True
    if request.method == "POST" and form.is_valid():
        if _salvar(request, form, empresa):
            messages.success(request, "Empresa atualizada.")
            return redirect("web:empresa", pk=empresa.pk)
    return render(request, "web/empresas/form.html", {"form": form, "empresa": empresa})


@tela()
def detalhe(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    itens = ItemCatalogo.tenant(request).filter(empresa=empresa, ativo=True)
    contagem = {s: itens.filter(status_classificacao=s).count() for s in ItemCatalogo.Status.values}
    contexto = {
        "empresa": empresa,
        "contagem_itens": contagem,
        "total_itens": sum(contagem.values()),
        "simulacoes": Simulacao.tenant(request).filter(empresa=empresa).select_related("versao_motor")[:10],
        "eh_simples": empresa.regime in {Empresa.Regime.SIMPLES, Empresa.Regime.MEI},
    }
    if contexto["eh_simples"]:
        contexto["aliquotas_simples"] = AliquotaEfetivaSN.tenant(request).filter(empresa=empresa)[:6]
    return render(request, "web/empresas/detalhe.html", contexto)
