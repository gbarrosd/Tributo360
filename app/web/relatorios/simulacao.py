"""Relatório de uma simulação: PDF e planilha com totais, premissas, itens e memória de cálculo.

Mostra o mesmo que a tela do resultado (`web/simulacoes/detalhe.html`), com as mesmas funções de
apresentação, para o relatório e a tela nunca discordarem.
"""

from django.utils import timezone
from django.utils.text import slugify

from simulacoes.models import Simulacao
from web.apresentacao import aliquotas_do_item, premissas, total_tributos
from web.templatetags.tributo import ESTADOS, ROTULOS_MEMORIA, moeda, pct

from . import pdf
from . import planilha as xl

AVISO_SIMULADO = (
    "Resultado <b>simulado</b>: o motor calculou com alíquotas informadas (tabela do Tributo360, edição do "
    "contador ou PGDAS-D), não com alíquotas oficiais publicadas no motor. Serve para planejamento, não "
    "substitui a apuração."
)


def nome_arquivo(simulacao, extensao):
    partes = ["simulacao", slugify(simulacao.empresa.razao_social)[:40], f"{simulacao.data_fato_gerador:%Y-%m-%d}"]
    if simulacao.rotulo:
        partes.append(slugify(simulacao.rotulo)[:40])
    return "-".join(p for p in partes if p) + f".{extensao}"


def linhas_memoria(dados, prefixo=""):
    """Memória do motor (dict de textos, às vezes aninhado) → [(tributo, texto)]."""
    if not dados:
        return []
    if isinstance(dados, dict):
        linhas = []
        for chave, valor in dados.items():
            rotulo = ROTULOS_MEMORIA.get(chave, chave)
            linhas += linhas_memoria(valor, f"{prefixo} › {rotulo}" if prefixo else rotulo)
        return linhas
    if isinstance(dados, list):
        return [linha for valor in dados for linha in linhas_memoria(valor, prefixo)]
    return [(prefixo, str(dados))]


def _codigo(item):
    partes = []
    if item.ncm:
        partes.append(f"NCM {item.ncm}")
    elif item.nbs:
        partes.append(f"NBS {item.nbs}")
    if item.cst:
        partes.append(f"CST {item.cst}")
    if item.c_class_trib:
        partes.append(f"cClassTrib {item.c_class_trib}")
    if item.c_ativ_sn:
        partes.append(f"cAtivSN {item.c_ativ_sn}")
    return " · ".join(partes)


def _versao(simulacao):
    v = simulacao.versao_motor
    texto = f"Motor {v.motor} {v.versao_app}"
    if v.versao_db:
        texto += f" · base {v.versao_db}"
    if v.data_db:
        texto += f" de {v.data_db:%d/%m/%Y}"
    return texto


def _premissas_pares(simulacao, dados):
    data = (
        timezone.localtime(dados["fato_gerador"]).strftime("%d/%m/%Y %H:%M")
        if dados.get("fato_gerador")
        else f"{simulacao.data_fato_gerador:%d/%m/%Y}"
    )
    pares = [("Data do fato gerador", data)]
    if dados.get("local"):
        pares.append(("Local", dados["local"]))
    pares.append(("Documento", dados["documento"]))
    if dados.get("aliquotas_sn"):
        pares.append(("Alíquotas do Simples", dados["aliquotas_sn"]))
    pares.append(("Motor", f"{_versao(simulacao)} · {simulacao.duracao_ms} ms"))
    pares.append(("Situação", simulacao.get_situacao_display()))
    return pares


def _rodape(request, simulacao):
    return [pdf.linha_gerado(request), f"{_versao(simulacao)} · simulação {simulacao.pk}"]


def _avisos_motor(simulacao):
    return (simulacao.resposta_bruta or {}).get("resolucaoAliquotas", {}).get("avisos") or []


# --- PDF --------------------------------------------------------------------------------------------


def gerar_pdf(request, simulacao):
    est = pdf.estilos()
    largura = pdf.largura_util()
    dados = premissas(simulacao)
    regular = simulacao.tipo == Simulacao.Tipo.REGULAR
    elementos = [
        pdf.p(simulacao.rotulo or "Simulação", est["titulo"]),
        pdf.p(
            f"{simulacao.get_tipo_display()} · {simulacao.empresa.razao_social} · CNPJ "
            f"{pdf.formatar_cnpj(simulacao.empresa.cnpj)} · feita por {pdf.nome_usuario(simulacao.usuario)} em "
            f"{timezone.localtime(simulacao.criado_em):%d/%m/%Y %H:%M}",
            est["subtitulo"],
        ),
    ]
    if simulacao.simulado:
        elementos += [pdf.aviso(AVISO_SIMULADO, "secondary", largura, est), pdf.Spacer(1, 4)]
    for aviso in _avisos_motor(simulacao):
        elementos += [pdf.aviso(pdf.texto(aviso), "warning", largura, est), pdf.Spacer(1, 4)]
    elementos += [
        pdf.Spacer(1, 4),
        pdf.kpis(
            [
                ("Base de cálculo", moeda(simulacao.total_base_calculo), False),
                ("CBS", moeda(simulacao.total_cbs), False),
                ("IBS", moeda(simulacao.total_ibs), False),
                ("Imposto Seletivo", moeda(simulacao.total_imposto_seletivo), False),
                ("Total de tributos", moeda(total_tributos(simulacao)), True),
            ],
            largura,
            est,
        ),
        pdf.p("Premissas", est["secao"]),
        pdf.definicoes(_premissas_pares(simulacao, dados), largura, est),
        pdf.p("Resumo por item", est["secao"]),
        _tabela_itens(simulacao, regular, largura, est),
        pdf.p("Itens e memória de cálculo", est["secao"]),
    ]
    for item in simulacao.itens.all():
        elementos += _bloco_item(item, regular, largura, est)
    return pdf.gerar(
        elementos,
        titulo=f"Simulação · {simulacao.empresa.razao_social}",
        cabecalho=f"{request.membership.escritorio} · Relatório de simulação",
        rodape=_rodape(request, simulacao),
    )


def _tabela_itens(simulacao, regular, largura, est):
    if regular:
        cab = ["Item", "Código", "Base", "CBS", "IBS UF", "IBS mun.", "IS", "Situação"]
        larguras = [10, 52, 22, 20, 20, 20, 18, 18]
    else:
        cab = ["Item", "Código", "Base", "CBS", "IBS", "Situação"]
        larguras = [10, 70, 26, 26, 26, 22]
    escala = largura / sum(larguras)
    linhas, destaques = [], {}
    for n, item in enumerate(simulacao.itens.all()):
        v = item.valores or {}
        tom, rotulo = ESTADOS.get(item.estado, ("secondary", item.estado))
        meio = (
            [moeda(v.get("cbs")), moeda(v.get("ibs_uf")), moeda(v.get("ibs_mun")), moeda(v.get("imposto_seletivo"))]
            if regular
            else [moeda(v.get("cbs")), moeda(v.get("ibs"))]
        )
        linhas.append(
            [
                str(item.numero),
                _codigo(item),
                moeda(v.get("base_calculo") or item.base_calculo),
                *meio,
                pdf.selo(rotulo, tom, est),
            ]
        )
        if tom == "danger":
            destaques[n] = "danger"
    return pdf.tabela(
        cab, linhas, [w * escala for w in larguras], numericas=set(range(2, len(cab) - 1)), est=est, destacar=destaques
    )


def _bloco_item(item, regular, largura, est):
    tom, rotulo = ESTADOS.get(item.estado, ("secondary", item.estado))
    cabeca = pdf.Table(
        [[pdf.p(f"Item {item.numero}", est["item"]), pdf.p(_codigo(item), est["mono"]), pdf.selo(rotulo, tom, est)]],
        colWidths=[22 * pdf.mm, largura - 22 * pdf.mm - 40 * pdf.mm, 40 * pdf.mm],
    )
    cabeca.setStyle(
        pdf.TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), pdf.FUNDO_2),
                ("BOX", (0, 0), (-1, -1), 0.6, pdf.BORDA),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (2, 0), (2, 0), "RIGHT"),
            ]
        )
    )
    blocos = [cabeca, pdf.Spacer(1, 4)]
    for erro in item.erros or []:
        mensagem = f"{erro.get('codigo', '')} {erro.get('titulo', '')}"
        if erro.get("detalhe"):
            mensagem += f": {erro['detalhe']}"
        if erro.get("campo"):
            mensagem += f" ({erro['campo']})"
        blocos += [pdf.aviso(pdf.texto(mensagem.strip()), "danger", largura, est), pdf.Spacer(1, 3)]

    v = item.valores or {}
    valores = [("Base de cálculo", v.get("base_calculo") or item.base_calculo), ("CBS", v.get("cbs"))]
    if regular:
        valores += [("IBS estadual", v.get("ibs_uf")), ("IBS municipal", v.get("ibs_mun"))]
    valores.append(("IBS", v.get("ibs")))
    if v.get("imposto_seletivo"):
        valores.append(("Imposto Seletivo", v.get("imposto_seletivo")))
    aliquotas = [
        [a["nome"], pct(a["valor"]), a["origem"] + (f" ({a['detalhe']})" if a.get("detalhe") else "")]
        for a in aliquotas_do_item(item)
    ]
    esquerda = largura * 0.36
    direita = largura - esquerda - 8
    tabela_valores = pdf.tabela(
        ["Valor", "R$"],
        [[n, moeda(x)] for n, x in valores],
        [esquerda * 0.55, esquerda * 0.45],
        numericas={1},
        est=est,
    )
    tabela_aliquotas = pdf.tabela(
        ["Alíquota", "%", "Origem"],
        aliquotas or [["—", "", ""]],
        [direita * 0.2, direita * 0.14, direita * 0.66],
        numericas={1},
        est=est,
    )
    lado_a_lado = pdf.Table([[tabela_valores, tabela_aliquotas]], colWidths=[esquerda + 8, direita])
    lado_a_lado.setStyle(
        pdf.TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    blocos += [lado_a_lado, pdf.Spacer(1, 5)]
    memoria = linhas_memoria(item.memoria)
    blocos.append(pdf.p("MEMÓRIA DE CÁLCULO DO MOTOR", est["rotulo"]))
    if memoria:
        blocos.append(pdf.tabela(["Tributo", "Texto do motor"], memoria, [32 * pdf.mm, largura - 32 * pdf.mm], est=est))
    else:
        blocos.append(pdf.p("O motor não devolveu memória de cálculo.", est["suave"]))
    blocos.append(pdf.Spacer(1, 10))
    # Cabeçalho, erros e valores juntos; a memória pode quebrar de página.
    return [pdf.KeepTogether(blocos[:-3]), *blocos[-3:]]


# --- Planilha --------------------------------------------------------------------------------------


def gerar_xlsx(request, simulacao):
    dados = premissas(simulacao)
    regular = simulacao.tipo == Simulacao.Tipo.REGULAR
    livro = xl.Planilha()

    resumo = livro.aba("Resumo")
    xl.titulo(
        resumo,
        simulacao.rotulo or "Simulação",
        f"{simulacao.get_tipo_display()} · {simulacao.empresa.razao_social} · CNPJ "
        f"{pdf.formatar_cnpj(simulacao.empresa.cnpj)}",
    )
    xl.pares(
        resumo,
        [
            ("Feita por", pdf.nome_usuario(simulacao.usuario), None),
            ("Feita em", xl.sem_fuso(simulacao.criado_em), xl.DATA_HORA),
            *[(r, v, None) for r, v in _premissas_pares(simulacao, dados)],
            ("Resultado simulado", "Sim" if simulacao.simulado else "Não", None),
            ("Base de cálculo (R$)", xl.numero(simulacao.total_base_calculo), xl.MOEDA),
            ("CBS (R$)", xl.numero(simulacao.total_cbs), xl.MOEDA),
            ("IBS (R$)", xl.numero(simulacao.total_ibs), xl.MOEDA),
            ("Imposto Seletivo (R$)", xl.numero(simulacao.total_imposto_seletivo), xl.MOEDA),
            ("Total de tributos (R$)", xl.numero(total_tributos(simulacao)), xl.MOEDA),
        ],
    )
    for aviso in ([AVISO_SIMULADO.replace("<b>", "").replace("</b>", "")] if simulacao.simulado else []) + list(
        _avisos_motor(simulacao)
    ):
        resumo.append(["Aviso", aviso])
    xl.rodape(resumo, _rodape(request, simulacao))

    itens = livro.aba("Itens")
    colunas = [
        ("Item", 6, None),
        ("NCM", 11, None),
        ("NBS", 11, None),
        ("CST", 6, None),
        ("cClassTrib", 11, None),
    ]
    if not regular:
        colunas.append(("cAtivSN", 9, None))
    colunas += [("Situação", 22, None), ("Base de cálculo", 15, xl.MOEDA), ("CBS", 13, xl.MOEDA)]
    if regular:
        colunas += [("IBS estadual", 13, xl.MOEDA), ("IBS municipal", 13, xl.MOEDA)]
    colunas += [("IBS", 13, xl.MOEDA)]
    if regular:
        colunas.append(("Imposto Seletivo", 14, xl.MOEDA))
    colunas += [("Alíquotas aplicadas (%) e origem", 60, None), ("Erros do motor", 40, None)]
    linhas, destaques = [], {}
    for n, item in enumerate(simulacao.itens.all()):
        v = item.valores or {}
        tom, rotulo = ESTADOS.get(item.estado, ("secondary", item.estado))
        linha = [item.numero, item.ncm, item.nbs, item.cst, item.c_class_trib]
        if not regular:
            linha.append(item.c_ativ_sn)
        linha += [rotulo, xl.numero(v.get("base_calculo") or item.base_calculo), xl.numero(v.get("cbs"))]
        if regular:
            linha += [xl.numero(v.get("ibs_uf")), xl.numero(v.get("ibs_mun"))]
        linha.append(xl.numero(v.get("ibs")))
        if regular:
            linha.append(xl.numero(v.get("imposto_seletivo")))
        linha.append("; ".join(f"{a['nome']} {pct(a['valor'])} ({a['origem']})" for a in aliquotas_do_item(item)))
        linha.append("; ".join(f"{e.get('codigo', '')} {e.get('titulo', '')}".strip() for e in item.erros or []))
        linhas.append(linha)
        if tom == "danger":
            destaques[n] = "danger"
    xl.tabela(itens, colunas, linhas, destaques)

    aliquotas = livro.aba("Alíquotas")
    linhas = [
        [item.numero, a["nome"], xl.numero(a["valor"]), a["origem"], a.get("detalhe", "")]
        for item in simulacao.itens.all()
        for a in aliquotas_do_item(item)
    ]
    xl.tabela(
        aliquotas,
        [
            ("Item", 6, None),
            ("Tributo", 16, None),
            ("Alíquota (%)", 13, xl.PERCENTUAL),
            ("Origem", 34, None),
            ("Detalhe", 60, None),
        ],
        linhas,
    )

    memoria = livro.aba("Memória de cálculo")
    linhas = [
        [item.numero, tributo, texto]
        for item in simulacao.itens.all()
        for tributo, texto in linhas_memoria(item.memoria)
    ]
    xl.tabela(memoria, [("Item", 6, None), ("Tributo", 18, None), ("Texto do motor", 110, None)], linhas)
    return livro.salvar()
