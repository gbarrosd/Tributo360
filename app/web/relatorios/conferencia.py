"""Relatório de conferência das notas: resumo, quadro por empresa, notas e divergências (PDF e planilha).

A view monta os querysets já com escopo (`Model.tenant(request)`) e filtrados por empresa e período;
aqui só se agrupa e se desenha.
"""

from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify

from documentos.models import Divergencia, DocumentoFiscal
from web.templatetags.tributo import STATUS_DIVERGENCIA, STATUS_DOCUMENTO, moeda, sinal

from . import pdf
from . import planilha as xl

LIMITE_PDF = 3000  # notas e divergências por PDF; a planilha leva tudo
LIMITE_PLANILHA = 50000


def nome_arquivo(filtros, extensao):
    alvo = slugify(filtros["empresa"].razao_social)[:40] if filtros.get("empresa") else "todas-as-empresas"
    return f"conferencia-{alvo}-{filtros['de']:%Y-%m-%d}-a-{filtros['ate']:%Y-%m-%d}.{extensao}"


def _contraparte(doc):
    if doc.papel == DocumentoFiscal.Papel.EMITIDA:
        return doc.destinatario_nome or doc.destinatario_cnpj
    return doc.emitente_nome or doc.emitente_cnpj


def montar(documentos, divergencias, filtros, tolerancia):
    """Junta os números do relatório. `documentos` e `divergencias` já vêm com escopo e filtros."""
    documentos = documentos.select_related("empresa").annotate(
        abertas=Count("divergencias", filter=Q(divergencias__status=Divergencia.Status.ABERTA)),
        tratadas=Count("divergencias", filter=~Q(divergencias__status=Divergencia.Status.ABERTA)),
    )
    notas = list(documentos.order_by("empresa__razao_social", "emissao", "numero")[:LIMITE_PLANILHA])
    if filtros.get("divergencias") != "todas":
        divergencias = divergencias.filter(status=Divergencia.Status.ABERTA)
    lista_div = list(
        divergencias.select_related("documento__empresa", "tratado_por").order_by(
            "documento__empresa__razao_social", "documento__emissao", "documento__numero", "numero_item", "campo"
        )[:LIMITE_PLANILHA]
    )

    por_empresa = {}
    for doc in notas:
        linha = por_empresa.setdefault(
            doc.empresa_id,
            {
                "empresa": doc.empresa,
                "notas": 0,
                "abertas": 0,
                "diferenca_aberta": Decimal("0"),
                **{s: 0 for s in DocumentoFiscal.Status.values},
            },
        )
        linha["notas"] += 1
        linha[doc.status] += 1
        linha["abertas"] += doc.abertas
    for d in lista_div:
        if d.status == Divergencia.Status.ABERTA and d.diferenca is not None and d.documento.empresa_id in por_empresa:
            por_empresa[d.documento.empresa_id]["diferenca_aberta"] += abs(d.diferenca)

    contagem = {s: sum(e[s] for e in por_empresa.values()) for s in DocumentoFiscal.Status.values}
    return {
        "filtros": filtros,
        "tolerancia": tolerancia,
        "notas": notas,
        "divergencias": lista_div,
        "por_empresa": sorted(por_empresa.values(), key=lambda e: e["empresa"].razao_social.lower()),
        "contagem": contagem,
        "total_notas": len(notas),
        "conferidas": contagem["conforme"] + contagem["divergente"],
        "abertas": sum(e["abertas"] for e in por_empresa.values()),
        "diferenca_aberta": sum((e["diferenca_aberta"] for e in por_empresa.values()), Decimal("0")),
    }


def _descricao_filtros(dados):
    f = dados["filtros"]
    alvo = f["empresa"].razao_social if f.get("empresa") else "Todas as empresas"
    texto = f"{alvo} · notas emitidas de {f['de']:%d/%m/%Y} a {f['ate']:%d/%m/%Y}"
    texto += " · divergências: " + ("todas" if f.get("divergencias") == "todas" else "só as abertas")
    return texto


def _rodape(request, dados):
    return [
        pdf.linha_gerado(request),
        f"{_descricao_filtros(dados)} · tolerância R$ {dados['tolerancia']:.2f}".replace(".", ","),
    ]


def _numero_nota(doc):
    return f"{doc.numero}/{doc.serie}" if doc.serie else str(doc.numero)


def _situacao_nota(doc):
    return STATUS_DOCUMENTO.get(doc.status, ("secondary", doc.status))


def _situacao_div(d):
    return STATUS_DIVERGENCIA.get(d.status, ("secondary", d.status))


def _tratamento(d):
    partes = []
    if d.comentario:
        partes.append(d.comentario)
    if d.tratado_por_id:
        quando = f" em {timezone.localtime(d.tratado_em):%d/%m/%Y}" if d.tratado_em else ""
        partes.append(f"({pdf.nome_usuario(d.tratado_por)}{quando})")
    return " ".join(partes)


# --- PDF --------------------------------------------------------------------------------------------


def gerar_pdf(request, dados):
    est = pdf.estilos()
    largura = pdf.largura_util(paisagem=True)
    uma_empresa = bool(dados["filtros"].get("empresa"))
    elementos = [
        pdf.p("Conferência de notas fiscais", est["titulo"]),
        pdf.p(_descricao_filtros(dados), est["subtitulo"]),
    ]
    if uma_empresa:
        empresa = dados["filtros"]["empresa"]
        elementos.insert(1, pdf.p(f"CNPJ {pdf.formatar_cnpj(empresa.cnpj)}", est["suave"]))
    if dados["total_notas"] > LIMITE_PDF or len(dados["divergencias"]) > LIMITE_PDF:
        elementos += [
            pdf.aviso(
                f"O período tem mais de {LIMITE_PDF} notas ou divergências: o PDF mostra as primeiras "
                f"{LIMITE_PDF} de cada. A planilha traz todas.",
                "warning",
                largura,
                est,
            ),
            pdf.Spacer(1, 4),
        ]
    c = dados["contagem"]
    elementos += [
        pdf.Spacer(1, 4),
        pdf.kpis(
            [
                ("Notas no período", str(dados["total_notas"]), False),
                ("Conformes", str(c["conforme"]), False),
                ("Com divergência", str(c["divergente"]), False),
                ("Sem conferência ou erro", str(c["nao_conferido"] + c["erro"] + c["recebido"]), False),
                ("Divergências abertas", str(dados["abertas"]), dados["abertas"] > 0),
                ("Diferença em aberto", moeda(dados["diferenca_aberta"]), dados["abertas"] > 0),
            ],
            largura,
            est,
        ),
    ]
    if not dados["notas"]:
        elementos += [pdf.Spacer(1, 10), pdf.p("Nenhuma nota importada no período.", est["suave"])]
        return _pdf(request, dados, elementos)

    if not uma_empresa:
        elementos += [pdf.p("Por empresa", est["secao"]), _tabela_empresas(dados, largura, est)]
    elementos += [pdf.p("Notas", est["secao"]), _tabela_notas(dados, largura, est, uma_empresa)]
    titulo_div = "Divergências" if dados["filtros"].get("divergencias") == "todas" else "Divergências abertas"
    elementos.append(pdf.p(titulo_div, est["secao"]))
    if dados["divergencias"]:
        elementos.append(_tabela_divergencias(dados, largura, est, uma_empresa))
    else:
        elementos.append(pdf.p("Nenhuma divergência no período.", est["suave"]))
    return _pdf(request, dados, elementos)


def _pdf(request, dados, elementos):
    return pdf.gerar(
        elementos,
        titulo="Conferência de notas fiscais",
        cabecalho=f"{request.membership.escritorio} · Relatório de conferência",
        rodape=_rodape(request, dados),
        paisagem=True,
    )


def _tabela_empresas(dados, largura, est):
    cab = [
        "Empresa",
        "CNPJ",
        "Notas",
        "Conformes",
        "Com divergência",
        "Sem conf./erro",
        "Div. abertas",
        "Diferença em aberto",
    ]
    larguras = [70, 32, 14, 18, 22, 20, 18, 28]
    linhas, destaques = [], {}
    for n, e in enumerate(dados["por_empresa"]):
        linhas.append(
            [
                e["empresa"].razao_social,
                pdf.formatar_cnpj(e["empresa"].cnpj),
                e["notas"],
                e["conforme"],
                e["divergente"],
                e["nao_conferido"] + e["erro"] + e["recebido"],
                e["abertas"],
                moeda(e["diferenca_aberta"]),
            ]
        )
        if e["abertas"]:
            destaques[n] = "danger"
    escala = largura / sum(larguras)
    return pdf.tabela(
        cab, linhas, [w * escala for w in larguras], numericas=set(range(2, 8)), est=est, destacar=destaques
    )


def _tabela_notas(dados, largura, est, uma_empresa):
    cab = ["Nota", "Modelo", "Emissão", "Papel", "Contraparte", "Situação", "Div. abertas", "Tratadas"]
    larguras = [22, 14, 22, 18, 70, 30, 18, 16]
    if not uma_empresa:
        cab.insert(0, "Empresa")
        larguras.insert(0, 50)
    linhas = []
    for doc in dados["notas"][:LIMITE_PDF]:
        tom, rotulo = _situacao_nota(doc)
        linha = [
            _numero_nota(doc),
            doc.get_modelo_display(),
            f"{timezone.localtime(doc.emissao):%d/%m/%Y}",
            "Emitida" if doc.papel == "emitida" else "Recebida",
            _contraparte(doc),
            pdf.selo(rotulo, tom, est),
            doc.abertas or "",
            doc.tratadas or "",
        ]
        if not uma_empresa:
            linha.insert(0, doc.empresa.razao_social)
        linhas.append(linha)
    escala = largura / sum(larguras)
    return pdf.tabela(cab, linhas, [w * escala for w in larguras], numericas={len(cab) - 2, len(cab) - 1}, est=est)


def _tabela_divergencias(dados, largura, est, uma_empresa):
    cab = ["Nota", "Item", "Campo", "Na nota", "Recalculado", "Diferença", "Situação", "Mensagem / tratamento"]
    larguras = [22, 10, 26, 22, 22, 20, 20, 90]
    if not uma_empresa:
        cab.insert(0, "Empresa")
        larguras.insert(0, 44)
    linhas = []
    for d in dados["divergencias"][:LIMITE_PDF]:
        tom, rotulo = _situacao_div(d)
        observacao = " ".join(x for x in (d.mensagem, _tratamento(d)) if x)
        linha = [
            _numero_nota(d.documento),
            d.numero_item or "—",
            d.nome_campo,
            moeda(d.valor_nota),
            moeda(d.valor_recalculado),
            sinal(d.diferenca),
            pdf.selo(rotulo, tom, est),
            observacao,
        ]
        if not uma_empresa:
            linha.insert(0, d.documento.empresa.razao_social)
        linhas.append(linha)
    escala = largura / sum(larguras)
    deslocamento = 0 if uma_empresa else 1
    numericas = {1 + deslocamento, 3 + deslocamento, 4 + deslocamento, 5 + deslocamento}
    return pdf.tabela(cab, linhas, [w * escala for w in larguras], numericas=numericas, est=est)


# --- Planilha --------------------------------------------------------------------------------------


def gerar_xlsx(request, dados):
    livro = xl.Planilha()
    c = dados["contagem"]

    resumo = livro.aba("Resumo")
    xl.titulo(resumo, "Conferência de notas fiscais", _descricao_filtros(dados))
    xl.pares(
        resumo,
        [
            ("Empresa", dados["filtros"]["empresa"].razao_social if dados["filtros"].get("empresa") else "Todas", None),
            ("Emitidas de", dados["filtros"]["de"], xl.DATA),
            ("Emitidas até", dados["filtros"]["ate"], xl.DATA),
            ("Tolerância (R$)", xl.numero(dados["tolerancia"]), xl.MOEDA),
            ("Notas no período", dados["total_notas"], None),
            ("Conformes", c["conforme"], None),
            ("Com divergência", c["divergente"], None),
            ("Sem conferência", c["nao_conferido"] + c["recebido"], None),
            ("Erro na conferência", c["erro"], None),
            ("Divergências abertas", dados["abertas"], None),
            ("Diferença em aberto (R$, soma absoluta)", xl.numero(dados["diferenca_aberta"]), xl.MOEDA),
        ],
    )
    xl.rodape(resumo, _rodape(request, dados))

    empresas = livro.aba("Por empresa")
    xl.tabela(
        empresas,
        [
            ("Empresa", 40, None),
            ("CNPJ", 20, None),
            ("Notas", 9, None),
            ("Conformes", 11, None),
            ("Com divergência", 15, None),
            ("Sem conferência", 15, None),
            ("Erro", 8, None),
            ("Divergências abertas", 14, None),
            ("Diferença em aberto (R$)", 18, xl.MOEDA),
        ],
        [
            [
                e["empresa"].razao_social,
                pdf.formatar_cnpj(e["empresa"].cnpj),
                e["notas"],
                e["conforme"],
                e["divergente"],
                e["nao_conferido"] + e["recebido"],
                e["erro"],
                e["abertas"],
                xl.numero(e["diferenca_aberta"]),
            ]
            for e in dados["por_empresa"]
        ],
        {n: "danger" for n, e in enumerate(dados["por_empresa"]) if e["abertas"]},
    )

    notas = livro.aba("Notas")
    linhas, destaques = [], {}
    for n, doc in enumerate(dados["notas"]):
        totais = doc.totais or {}
        linhas.append(
            [
                doc.empresa.razao_social,
                doc.get_modelo_display(),
                doc.numero,
                doc.serie,
                xl.sem_fuso(doc.emissao),
                doc.get_papel_display(),
                doc.emitente_cnpj,
                doc.emitente_nome,
                doc.destinatario_cnpj,
                doc.destinatario_nome,
                doc.get_status_display(),
                doc.abertas,
                doc.tratadas,
                xl.numero(totais.get("valor_nota")),
                xl.numero(totais.get("base_calculo")),
                xl.numero(totais.get("cbs")),
                xl.numero(totais.get("ibs")),
                xl.numero(totais.get("imposto_seletivo")),
                doc.chave,
            ]
        )
        tom, _ = _situacao_nota(doc)
        if tom in ("danger", "warning"):
            destaques[n] = tom
    xl.tabela(
        notas,
        [
            ("Empresa", 30, None),
            ("Modelo", 8, None),
            ("Número", 10, None),
            ("Série", 6, None),
            ("Emissão", 16, xl.DATA_HORA),
            ("Papel", 20, None),
            ("CNPJ emitente", 16, None),
            ("Emitente", 30, None),
            ("CNPJ destinatário", 16, None),
            ("Destinatário", 30, None),
            ("Situação", 18, None),
            ("Divergências abertas", 12, None),
            ("Divergências tratadas", 12, None),
            ("Valor da nota (R$)", 15, xl.MOEDA),
            ("Base na nota (R$)", 14, xl.MOEDA),
            ("CBS na nota (R$)", 13, xl.MOEDA),
            ("IBS na nota (R$)", 13, xl.MOEDA),
            ("IS na nota (R$)", 12, xl.MOEDA),
            ("Chave de acesso", 48, None),
        ],
        linhas,
        destaques,
    )

    divergencias = livro.aba("Divergências")
    xl.tabela(
        divergencias,
        [
            ("Empresa", 30, None),
            ("Nota", 10, None),
            ("Série", 6, None),
            ("Emissão", 12, xl.DATA),
            ("Item", 6, None),
            ("Campo", 16, None),
            ("Valor na nota (R$)", 15, xl.MOEDA),
            ("Recalculado (R$)", 15, xl.MOEDA),
            ("Diferença (R$)", 13, xl.MOEDA),
            ("Situação", 12, None),
            ("Mensagem", 50, None),
            ("Comentário", 40, None),
            ("Tratada por", 22, None),
            ("Tratada em", 16, xl.DATA_HORA),
            ("Chave de acesso", 48, None),
        ],
        [
            [
                d.documento.empresa.razao_social,
                d.documento.numero,
                d.documento.serie,
                timezone.localtime(d.documento.emissao).date(),
                d.numero_item,
                d.nome_campo,
                xl.numero(d.valor_nota),
                xl.numero(d.valor_recalculado),
                xl.numero(d.diferenca),
                d.get_status_display(),
                d.mensagem,
                d.comentario,
                pdf.nome_usuario(d.tratado_por) if d.tratado_por_id else "",
                xl.sem_fuso(d.tratado_em),
                d.documento.chave,
            ]
            for d in dados["divergencias"]
        ],
        {n: "danger" for n, d in enumerate(dados["divergencias"]) if d.status == Divergencia.Status.ABERTA},
    )
    return livro.salvar()
