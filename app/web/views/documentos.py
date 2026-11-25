"""Documentos fiscais: importação de XML (NF-e 55 e NFS-e nacional), conferência pelo motor, divergências e DANFSe."""

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from contas.papeis import Acao
from documentos import servicos
from documentos.models import Divergencia, DocumentoFiscal, LoteImportacao
from empresas.models import Empresa
from engines import clientes
from engines.http import ErroDeMotor

from ..acesso import tela
from ..forms import DanfseAvulsoForm, DanfseForm, DivergenciaForm, ImportarXmlForm, ToleranciaForm
from .empresas import empresa_da_requisicao

POR_PAGINA = 100


def documento_da_requisicao(request, pk):
    return get_object_or_404(DocumentoFiscal.tenant(request).select_related("empresa", "simulacao"), pk=pk)


def _filtrar(request, qs):
    status = request.GET.get("status", "")
    if status in DocumentoFiscal.Status.values:
        qs = qs.filter(status=status)
    busca = request.GET.get("q", "").strip()
    if busca:
        qs = qs.filter(Q(numero=busca) | Q(chave__contains=busca) | Q(emitente_nome__icontains=busca)
                       | Q(destinatario_nome__icontains=busca))  # fmt: skip
    return qs, status, busca


def _contagem(qs):
    return dict(qs.order_by().values("status").annotate(total=Count("pk")).values_list("status", "total"))


@tela()
def todos(request):
    """Todos os documentos do escritório (menu lateral)."""
    base = DocumentoFiscal.tenant(request)
    empresa_id = request.GET.get("empresa", "")
    if empresa_id:
        base = base.filter(empresa_id=empresa_id) if _uuid(empresa_id) else base.none()
    qs, status, busca = _filtrar(request, base)
    contagem = _contagem(base)
    return render(
        request,
        "web/documentos/todos.html",
        {
            "conferidas": contagem.get("conforme", 0) + contagem.get("divergente", 0),
            "documentos": qs.select_related("empresa").annotate(
                abertas=Count("divergencias", filter=Q(divergencias__status=Divergencia.Status.ABERTA))
            )[:POR_PAGINA],
            "contagem": contagem,
            "status": status,
            "busca": busca,
            "empresa_id": empresa_id,
            "empresas": Empresa.tenant(request).filter(ativa=True),
            "status_choices": DocumentoFiscal.Status.choices,
            "divergencias_abertas": Divergencia.tenant(request).filter(status=Divergencia.Status.ABERTA).count(),
            "tolerancia_form": ToleranciaForm(
                initial={"tolerancia": request.membership.escritorio.tolerancia_divergencia}
            ),
        },
    )


def _uuid(valor):
    import uuid

    try:
        uuid.UUID(str(valor))
    except ValueError:
        return False
    return True


@tela()
def da_empresa(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    base = DocumentoFiscal.tenant(request).filter(empresa=empresa)
    qs, status, busca = _filtrar(request, base)
    return render(
        request,
        "web/documentos/empresa.html",
        {
            "empresa": empresa,
            "documentos": qs.annotate(
                abertas=Count("divergencias", filter=Q(divergencias__status=Divergencia.Status.ABERTA))
            )[:POR_PAGINA],
            "contagem": _contagem(base),
            "total": base.count(),
            "status": status,
            "busca": busca,
            "status_choices": DocumentoFiscal.Status.choices,
            "lotes": LoteImportacao.tenant(request).filter(empresa=empresa)[:5],
        },
    )


@tela(Acao.CADASTRAR_ITENS)
def importar(request, pk):
    empresa = empresa_da_requisicao(request, pk)
    form = ImportarXmlForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        enviados = []
        for arquivo in form.cleaned_data["arquivos"]:
            limite = servicos.MAX_BYTES_ZIP if arquivo.name.lower().endswith(".zip") else servicos.MAX_BYTES_XML
            enviados.append((arquivo.name, arquivo.read(limite + 1)))
        try:
            lote = servicos.importar(request, empresa, enviados)
        except servicos.LoteRecusado as erro:
            form.add_error("arquivos", str(erro))
        else:
            return redirect("web:lote", lote.pk)
    return render(request, "web/documentos/importar.html", {"empresa": empresa, "form": form})


@tela()
def lote(request, pk):
    lote = get_object_or_404(LoteImportacao.tenant(request).select_related("empresa", "enviado_por"), pk=pk)
    documentos = {str(d.pk): d for d in DocumentoFiscal.tenant(request).filter(lote=lote)}
    linhas = [{**r, "documento": documentos.get(r.get("documento_id") or "")} for r in lote.resultados]
    return render(request, "web/documentos/lote.html", {"lote": lote, "empresa": lote.empresa, "linhas": linhas})


def _comparacao(documento):
    """Por item: valor da nota, recalculado e se há divergência aberta/tratada em cada campo."""
    calculados = {}
    if documento.simulacao_id:
        calculados = {i.numero: i for i in documento.simulacao.itens.all()}
    por_item = {}
    for d in documento.divergencias.all():
        por_item.setdefault(d.numero_item, {})[d.campo] = d
    linhas = []
    for posicao, item in enumerate(documento.itens, start=1):
        calculado = calculados.get(posicao)
        marcas = por_item.get(item["numero"], {})
        valores = [
            {
                "campo": campo,
                "nome": Divergencia.CAMPOS[campo],
                "nota": item.get(campo),
                "motor": (calculado.valores.get(campo) if calculado else None),
                "divergencia": marcas.get(campo),
            }
            for campo in servicos.CAMPOS_COMPARADOS
            if item.get(campo) is not None or (calculado and calculado.valores.get(campo) not in (None, "0", "0.00"))
        ]
        linhas.append({"item": item, "calculado": calculado, "valores": valores, "calculo": marcas.get("calculo")})
    return linhas


@tela()
def documento(request, pk):
    documento = documento_da_requisicao(request, pk)
    divergencias = list(documento.divergencias.select_related("tratado_por"))
    return render(
        request,
        "web/documentos/detalhe.html",
        {
            "documento": documento,
            "empresa": documento.empresa,
            "linhas": _comparacao(documento),
            "divergencias": divergencias,
            "abertas": sum(1 for d in divergencias if d.status == Divergencia.Status.ABERTA),
            "status_divergencia": Divergencia.Status.choices,
        },
    )


@require_POST
@tela(Acao.CADASTRAR_ITENS)
def reconferir(request, pk):
    documento = documento_da_requisicao(request, pk)
    servicos.conferir(request, documento)
    messages.success(request, f"Nota conferida de novo: {documento.get_status_display().lower()}.")
    return redirect("web:documento", documento.pk)


@tela()
def documento_xml(request, pk):
    documento = documento_da_requisicao(request, pk)
    resposta = HttpResponse(documento.xml, content_type="application/xml; charset=utf-8")
    prefixo = "NFSe" if documento.modelo == DocumentoFiscal.Modelo.NFSE else "NFe"
    resposta["Content-Disposition"] = f'attachment; filename="{prefixo}{documento.chave}.xml"'
    return resposta


def _pdf_do_danfse(request, xml, form, nome):
    """PDF do gerador, ou None com a mensagem do motor em `messages`."""
    try:
        pdf = clientes.danfse().gerar(xml, stc=int(form.cleaned_data["situacao"]), canhoto=form.cleaned_data["canhoto"])
    except ErroDeMotor as erro:
        messages.error(request, str(erro))
        return None
    resposta = HttpResponse(pdf, content_type="application/pdf")
    resposta["Content-Disposition"] = f'inline; filename="{nome}"'
    return resposta


@tela(Acao.SIMULAR)
def documento_danfse(request, pk):
    """DANFSe (PDF) de uma NFS-e importada. Situação e canhoto vêm da query string."""
    documento = documento_da_requisicao(request, pk)
    if documento.modelo != DocumentoFiscal.Modelo.NFSE:
        messages.error(request, "O DANFSe só existe para NFS-e.")
        return redirect("web:documento", pk=documento.pk)
    form = DanfseForm(request.GET or {"situacao": "0"})
    if form.is_valid():
        resposta = _pdf_do_danfse(request, documento.xml.encode(), form, f"DANFSe-{documento.numero}.pdf")
        if resposta is not None:
            return resposta
    else:
        messages.error(request, "Situação do DANFSe inválida.")
    return redirect("web:documento", pk=documento.pk)


@tela(Acao.SIMULAR)
def danfse_avulso(request):
    """Gera o DANFSe de um XML de NFS-e enviado na hora, sem importar a nota."""
    form = DanfseAvulsoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        resposta = _pdf_do_danfse(request, form.cleaned_data["arquivo"], form, "DANFSe.pdf")
        if resposta is not None:
            return resposta
    return render(request, "web/documentos/danfse.html", {"form": form})


@require_POST
@tela(Acao.RESOLVER_DIVERGENCIA)
def divergencia(request, pk):
    divergencia = get_object_or_404(Divergencia.tenant(request).select_related("documento"), pk=pk)
    form = DivergenciaForm(request.POST)
    if form.is_valid():
        servicos.tratar_divergencia(request, divergencia, form.cleaned_data["status"], form.cleaned_data["comentario"])
        messages.success(request, f"Divergência marcada como {divergencia.get_status_display().lower()}.")
    else:
        for erros in form.errors.values():
            for erro in erros:
                messages.error(request, erro)
    return redirect(f"{_url_documento(divergencia.documento_id)}#divergencias")


def _url_documento(pk):
    from django.urls import reverse

    return reverse("web:documento", args=[pk])


@require_POST
@tela(Acao.RESOLVER_DIVERGENCIA)
def tolerancia(request):
    form = ToleranciaForm(request.POST)
    if form.is_valid():
        servicos.salvar_tolerancia(request, form.cleaned_data["tolerancia"])
        messages.success(request, "Tolerância salva. Vale para as próximas conferências.")
    else:
        messages.error(request, "Informe a tolerância em reais, entre 0 e 1.000 (ex.: 0,01).")
    return redirect("web:documentos")
