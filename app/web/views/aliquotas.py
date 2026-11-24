"""Alíquotas efetivas do Simples por período (D3): cadastradas uma vez por mês, usadas em toda simulação."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from contas.papeis import Acao
from empresas.models import Empresa
from simulacoes import servicos
from simulacoes.models import AliquotaEfetivaSN
from simulacoes.serializers import AliquotaEfetivaSNSerializer

from ..acesso import tela
from ..forms import AliquotasSimplesForm, aplicar_erros
from .empresas import empresa_da_requisicao


def _empresa_simples(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    if empresa.regime not in {Empresa.Regime.SIMPLES, Empresa.Regime.MEI}:
        raise Http404("Alíquotas efetivas só existem para empresas do Simples.")
    return empresa


def cadastro_da_requisicao(request, pk):
    return get_object_or_404(AliquotaEfetivaSN.tenant(request).select_related("empresa"), pk=pk)


@tela()
def lista(request, pk):
    empresa = _empresa_simples(request, pk)
    cadastros = AliquotaEfetivaSN.tenant(request).filter(empresa=empresa).select_related("informado_por")
    return render(request, "web/aliquotas/lista.html", {"empresa": empresa, "cadastros": cadastros})


def _salvar(request, form, empresa, instancia=None):
    dados = {**form.para_api(), "empresa": str(empresa.pk)}
    serializer = AliquotaEfetivaSNSerializer(instance=instancia, data=dados, context={"request": request})
    if not serializer.is_valid():
        aplicar_erros(form, serializer.errors, AliquotasSimplesForm.MAPA_ERROS)
        return None
    validados = dict(serializer.validated_data)
    validados.pop("empresa")
    return servicos.salvar_aliquotas_sn(request, empresa, validados, instancia)


@tela(Acao.INFORMAR_ALIQUOTAS_SN)
def nova(request, pk):
    empresa = _empresa_simples(request, pk)
    inicial = {"periodo": request.GET.get("periodo", "")}
    # Copiar o mês anterior economiza digitação: em geral só alguns anexos mudam.
    if anterior := AliquotaEfetivaSN.tenant(request).filter(empresa=empresa).first():
        inicial = {**AliquotasSimplesForm.inicial_de(anterior), **inicial}
    form = AliquotasSimplesForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        cadastro = _salvar(request, form, empresa)
        if cadastro:
            messages.success(request, f"Alíquotas de {cadastro.periodo_texto} cadastradas.")
            destino = "web:simular" if request.GET.get("voltar") == "simulacao" else "web:aliquotas"
            return redirect(destino, pk=empresa.pk)
    return render(
        request,
        "web/aliquotas/form.html",
        {"form": form, "empresa": empresa, "cadastro": None, "copiado_de": anterior},
    )


@tela(Acao.INFORMAR_ALIQUOTAS_SN)
def editar(request, pk):
    cadastro = cadastro_da_requisicao(request, pk)
    form = AliquotasSimplesForm(request.POST or None, initial=AliquotasSimplesForm.inicial_de(cadastro))
    if request.method == "POST" and form.is_valid():
        if _salvar(request, form, cadastro.empresa, cadastro):
            messages.success(request, f"Alíquotas de {cadastro.periodo_texto} atualizadas.")
            return redirect("web:aliquotas", pk=cadastro.empresa_id)
    return render(request, "web/aliquotas/form.html", {"form": form, "empresa": cadastro.empresa, "cadastro": cadastro})


@tela(Acao.INFORMAR_ALIQUOTAS_SN)
@require_POST
def remover(request, pk):
    cadastro = cadastro_da_requisicao(request, pk)
    empresa_id = cadastro.empresa_id
    servicos.remover_aliquotas_sn(request, cadastro)
    messages.success(request, "Cadastro removido. Simulações já feitas guardam as alíquotas que usaram.")
    return redirect("web:aliquotas", pk=empresa_id)
