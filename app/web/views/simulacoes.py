"""Formulário de simulação, resultado com memória de cálculo, histórico e comparação de cenários.

O formulário só monta a mesma entrada que a API recebe (`POST /api/simulacoes/...`) e chama os mesmos
serializers e serviços. A entrada fica gravada na simulação, o que permite "duplicar como novo cenário".
"""

import re
import uuid
from datetime import datetime, time

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from catalogo.models import ItemCatalogo
from contas.papeis import Acao
from empresas.models import Empresa
from engines import aliquotas
from engines.http import MotorIndisponivel, OperacaoRecusada
from simulacoes import servicos
from simulacoes.models import AliquotaEfetivaSN, Simulacao
from simulacoes.serializers import SimulacaoRegularSerializer, SimulacaoSimplesSerializer

from ..acesso import tela
from ..apresentacao import aliquotas_do_item, comparacao, premissas, total_tributos
from ..forms import (
    ItensRegularFormSet,
    ItensSimplesFormSet,
    SimulacaoRegularForm,
    SimulacaoSimplesForm,
    _achatar,
)
from .empresas import empresa_da_requisicao

MAX_COMPARACAO = 4
CAMPOS_ALIQUOTA = ("cbs", "ibs_estadual", "ibs_municipal")


def _eh_simples(empresa):
    return empresa.regime in {Empresa.Regime.SIMPLES, Empresa.Regime.MEI}


def _catalogo_json(catalogo):
    return {
        str(item.pk): {
            "ncm": item.ncm,
            "nbs": item.nbs,
            "cst": item.cst,
            "c_class_trib": item.c_class_trib,
            "c_ativ_sn": item.c_ativ_sn,
            "status": item.status_classificacao,
        }
        for item in catalogo
    }


# --- Entrada da API ↔ formulário ------------------------------------------------------------------


def _entrada_regular(form, itens, empresa):
    dados = form.cleaned_data
    hora = dados.get("hora") or time(12, 0)
    fato = timezone.make_aware(datetime.combine(dados["data"], hora))
    entrada = {
        "empresa": str(empresa.pk),
        "data_fato_gerador": fato.isoformat(),
        "rotulo": dados.get("rotulo", ""),
        "itens": [item for _, item in itens],
    }
    for campo in ("uf", "municipio"):
        if dados.get(campo):
            entrada[campo] = dados[campo]
    if dados.get("tp_doc"):
        entrada["tp_doc"] = dados["tp_doc"]
    informadas = {c: str(dados[c]) for c in CAMPOS_ALIQUOTA if dados.get(c) is not None}
    if informadas:
        entrada["aliquotas"] = informadas
    return entrada


def _entrada_simples(form, itens, empresa):
    dados = form.cleaned_data
    return {
        "empresa": str(empresa.pk),
        "data": dados["data"].isoformat(),
        "tp_doc": dados["tp_doc"],
        "rotulo": dados.get("rotulo", ""),
        "itens": [item for _, item in itens],
    }


def _inicial_de_entrada(entrada, simples):
    """Valores iniciais do formulário a partir da entrada gravada de outra simulação."""
    inicial = {"rotulo": ""}
    if simples:
        inicial.update({"data": entrada.get("data"), "tp_doc": entrada.get("tp_doc")})
    else:
        fato = entrada.get("data_fato_gerador", "")
        if m := re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})", fato):
            inicial.update({"data": m[1], "hora": m[2]})
        inicial.update({c: entrada.get(c) for c in ("uf", "municipio", "tp_doc") if entrada.get(c)})
        inicial.update({c: v for c, v in (entrada.get("aliquotas") or {}).items() if c in CAMPOS_ALIQUOTA})
    itens = [{c: v for c, v in item.items() if not isinstance(v, dict)} for item in entrada.get("itens") or []]
    return inicial, itens


def _distribuir_erros(erros, form, itens):
    """Erros do serializer/serviço → campos do formulário e das linhas de item."""
    mapa_topo = {
        "data_fato_gerador": "data",
        "aliquotas.cbs": "cbs",
        "aliquotas.ibs_estadual": "ibs_estadual",
        "aliquotas.ibs_municipal": "ibs_municipal",
    }
    for campo, mensagens in _achatar(erros):
        if m := re.match(r"itens(?:\.|\[)(\d+)\]?\.?(.*)", campo):
            posicao, resto = int(m[1]), m[2]
            if posicao < len(itens):
                linha = itens[posicao][0]
                alvo = resto.split(".")[0] if resto else None
                if alvo in linha.fields:
                    linha.add_error(alvo, mensagens)
                else:
                    linha.add_error(None, mensagens)
                continue
            form.add_error(None, [f"Item {posicao + 1}: {msg}" for msg in mensagens])
            continue
        alvo = mapa_topo.get(campo, campo)
        if alvo in form.fields:
            form.add_error(alvo, mensagens)
        else:
            form.add_error(None, mensagens)


# --- Formulário de simulação --------------------------------------------------------------------


@tela(Acao.SIMULAR)
def simular(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    if not empresa.ativa:
        messages.warning(request, "Empresa inativa: reative para simular.")
        return redirect("web:empresa", pk=empresa.pk)
    simples = _eh_simples(empresa)
    catalogo = ItemCatalogo.tenant(request).filter(empresa=empresa, ativo=True).order_by("codigo")
    Form, FormSet = (
        (SimulacaoSimplesForm, ItensSimplesFormSet) if simples else (SimulacaoRegularForm, ItensRegularFormSet)
    )

    inicial, itens_iniciais = {"data": timezone.localdate()}, []
    origem = None
    if base_id := request.GET.get("a_partir_de"):
        origem = get_object_or_404(Simulacao.tenant(request).filter(empresa=empresa), pk=base_id)
        inicial, itens_iniciais = _inicial_de_entrada(origem.entrada or {}, simples)
        inicial["rotulo"] = f"{origem.rotulo or 'Cenário'} (variação)"

    form = Form(request.POST or None, initial=inicial)
    formset = FormSet(request.POST or None, initial=itens_iniciais or None, catalogo=catalogo, prefix="itens")
    falta_periodo = None

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        itens = formset.preenchidos()
        if not itens:
            form.add_error(None, "Inclua ao menos um item.")
        else:
            entrada = (_entrada_simples if simples else _entrada_regular)(form, itens, empresa)
            Serializer = SimulacaoSimplesSerializer if simples else SimulacaoRegularSerializer
            funcao = servicos.simular_simples if simples else servicos.simular_regime_regular
            serializer = Serializer(data=entrada, context={"request": request})
            try:
                if not serializer.is_valid():
                    raise ValidationError(serializer.errors)
                simulacao = funcao(request, serializer.validated_data, entrada)
            except servicos.SemAliquotasDoPeriodo:
                falta_periodo = form.cleaned_data["data"]
            except ValidationError as exc:
                _distribuir_erros(exc.detail, form, itens)
            except MotorIndisponivel as exc:
                form.add_error(None, f"Motor de cálculo indisponível ({exc.motor}). Tente de novo em instantes.")
            except OperacaoRecusada as exc:
                form.add_error(None, f"O motor recusou a operação: {exc.mensagem}")
                for erro in exc.erros:
                    form.add_error(None, f"{erro.codigo} {erro.campo}: {erro.detalhe or erro.titulo}")
            else:
                messages.success(request, "Simulação calculada e gravada no histórico.")
                return redirect("web:simulacao", pk=simulacao.pk)

    contexto = {
        "empresa": empresa,
        "form": form,
        "formset": formset,
        "simples": simples,
        "catalogo": catalogo,
        "catalogo_json": _catalogo_json(catalogo),
        "origem": origem,
        "falta_periodo": falta_periodo,
        "ano_aliquotas": aliquotas.ANO_ALIQUOTAS_INFORMADAS,
    }
    if simples:
        contexto["periodos_cadastrados"] = list(
            AliquotaEfetivaSN.tenant(request).filter(empresa=empresa).values_list("periodo", flat=True)[:24]
        )
    return render(request, "web/simulacoes/form.html", contexto)


# --- Resultado, histórico e comparação ------------------------------------------------------------


def _simulacao_da_requisicao(request, pk):
    qs = Simulacao.tenant(request).select_related("empresa", "versao_motor", "usuario").prefetch_related("itens")
    return get_object_or_404(qs, pk=pk)


@tela()
def detalhe(request, pk):
    simulacao = _simulacao_da_requisicao(request, pk)
    itens = [{"item": item, "aliquotas": aliquotas_do_item(item)} for item in simulacao.itens.all()]
    return render(
        request,
        "web/simulacoes/detalhe.html",
        {
            "simulacao": simulacao,
            "itens": itens,
            "premissas": premissas(simulacao),
            "total_tributos": total_tributos(simulacao),
            "avisos_motor": (simulacao.resposta_bruta or {}).get("resolucaoAliquotas", {}).get("avisos") or [],
        },
    )


@tela()
def historico(request):
    qs = Simulacao.tenant(request).select_related("empresa", "versao_motor")
    empresa_id = request.GET.get("empresa", "")
    tipo = request.GET.get("tipo", "")
    if empresa_id:
        qs = qs.filter(empresa_id=empresa_id) if _uuid_valido(empresa_id) else qs.none()
    if tipo in Simulacao.Tipo.values:
        qs = qs.filter(tipo=tipo)
    if busca := request.GET.get("q", "").strip():
        qs = qs.filter(rotulo__icontains=busca)
    return render(
        request,
        "web/simulacoes/historico.html",
        {
            "simulacoes": qs[:200],
            "empresas": Empresa.tenant(request).order_by("razao_social"),
            "empresa_id": empresa_id,
            "tipo": tipo,
            "busca": busca,
            "max_comparacao": MAX_COMPARACAO,
        },
    )


def _uuid_valido(valor):
    try:
        uuid.UUID(str(valor))
    except ValueError:
        return False
    return True


@tela()
def comparar(request):
    ids = [i for i in request.GET.getlist("ids") if _uuid_valido(i)]
    if not 2 <= len(ids) <= MAX_COMPARACAO:
        messages.warning(request, f"Escolha de 2 a {MAX_COMPARACAO} simulações para comparar.")
        return redirect("web:historico")
    encontradas = {
        str(s.pk): s
        for s in Simulacao.tenant(request)
        .filter(pk__in=ids)
        .select_related("empresa", "versao_motor")
        .prefetch_related("itens__simulacao")
    }
    if len(encontradas) != len(set(ids)):
        # Alguma é de outro escritório ou não existe: não revela qual.
        raise Http404("Simulação não encontrada.")
    simulacoes = [encontradas[i] for i in dict.fromkeys(ids)]
    colunas = comparacao(simulacoes)
    versoes = {str(s.versao_motor) for s in simulacoes}
    return render(
        request,
        "web/simulacoes/comparar.html",
        {
            "colunas": colunas,
            "versoes_diferentes": len(versoes) > 1,
            "indices_itens": range(max(len(c["premissas"]["itens"]) for c in colunas)),
        },
    )
