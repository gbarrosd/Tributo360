"""Base dos relatórios em PDF (ReportLab): fontes, cores do tema, cabeçalho e rodapé com versões.

As cores e as fontes (IBM Plex) são as do tema claro das telas (`frontend/tailwind.css`), para o PDF
parecer a mesma ferramenta. O rodapé de toda página traz quem gerou, quando, e as versões dos motores
usadas, como pede o planejamento (Fase 5: "premissas e versões no rodapé").
"""

import io
from pathlib import Path

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import KeepTogether

from web.templatetags.tributo import moeda

FONTES = Path(__file__).resolve().parent / "fontes"

# Tema claro (frontend/tailwind.css)
FUNDO_2 = colors.HexColor("#f7f9fa")
BORDA = colors.HexColor("#e1e6eb")
TEXTO = colors.HexColor("#1b2430")
SUAVE = colors.HexColor("#637082")
MARCA = colors.HexColor("#0f6b7d")
MARCA_SUAVE = colors.HexColor("#e2f0f3")
LATERAL = colors.HexColor("#0e3b47")
TONS = {
    "success": (colors.HexColor("#1d7a4a"), colors.HexColor("#e3f4ea")),
    "warning": (colors.HexColor("#8f5200"), colors.HexColor("#fff1d9")),
    "danger": (colors.HexColor("#b42318"), colors.HexColor("#fdecea")),
    "info": (colors.HexColor("#1f5fa8"), colors.HexColor("#e5effb")),
    "secondary": (colors.HexColor("#4b5664"), colors.HexColor("#eceff3")),
}

_registradas = False


def _registrar_fontes():
    global _registradas
    if _registradas:
        return
    pdfmetrics.registerFont(TTFont("Plex", FONTES / "IBMPlexSans-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("Plex-SemiBold", FONTES / "IBMPlexSans-SemiBold.ttf"))
    pdfmetrics.registerFont(TTFont("PlexMono", FONTES / "IBMPlexMono-Regular.ttf"))
    pdfmetrics.registerFontFamily(
        "Plex", normal="Plex", bold="Plex-SemiBold", italic="Plex", boldItalic="Plex-SemiBold"
    )
    _registradas = True


def estilos():
    _registrar_fontes()
    base = ParagraphStyle("base", fontName="Plex", fontSize=9, leading=12.5, textColor=TEXTO)
    return {
        "base": base,
        "titulo": ParagraphStyle("titulo", base, fontName="Plex-SemiBold", fontSize=16, leading=20, spaceAfter=2),
        "subtitulo": ParagraphStyle("subtitulo", base, textColor=SUAVE, spaceAfter=8),
        "secao": ParagraphStyle(
            "secao", base, fontName="Plex-SemiBold", fontSize=11, leading=14, spaceBefore=10, spaceAfter=5
        ),
        "rotulo": ParagraphStyle("rotulo", base, fontSize=7, leading=9, textColor=SUAVE),
        "kpi": ParagraphStyle("kpi", base, fontName="Plex-SemiBold", fontSize=11, leading=14),
        "suave": ParagraphStyle("suave", base, fontSize=8, leading=11, textColor=SUAVE),
        "celula": ParagraphStyle("celula", base, fontSize=8, leading=10.5),
        "celula_num": ParagraphStyle("celula_num", base, fontSize=8, leading=10.5, alignment=TA_RIGHT),
        "cab": ParagraphStyle("cab", base, fontName="Plex-SemiBold", fontSize=7.5, leading=10, textColor=SUAVE),
        "cab_num": ParagraphStyle(
            "cab_num", base, fontName="Plex-SemiBold", fontSize=7.5, leading=10, textColor=SUAVE, alignment=TA_RIGHT
        ),
        "mono": ParagraphStyle("mono", base, fontName="PlexMono", fontSize=7.5, leading=10.5),
        "item": ParagraphStyle("item", base, fontName="Plex-SemiBold", fontSize=9.5, leading=13),
    }


def texto(valor):
    """Escapa para o mini-HTML do Paragraph."""
    return (
        str("" if valor is None else valor).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ).replace("\n", "<br/>")


def p(valor, estilo):
    return Paragraph(texto(valor), estilo)


def formatar_cnpj(cnpj):
    c = str(cnpj or "")
    if len(c) != 14:
        return c
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def nome_usuario(usuario):
    if usuario is None:
        return "—"
    return getattr(usuario, "nome", "") or getattr(usuario, "email", "") or str(usuario)


def aviso(mensagem, tom="warning", largura=None, est=None):
    """Caixa de alerta como `.alert-*` das telas."""
    est = est or estilos()
    cor, fundo = TONS[tom]
    estilo = ParagraphStyle("aviso", est["base"], textColor=cor, fontSize=8.5, leading=12)
    tabela = Table([[Paragraph(mensagem, estilo)]], colWidths=[largura] if largura else None)
    tabela.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), fundo),
                ("BOX", (0, 0), (-1, -1), 0.5, cor),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return tabela


def selo(texto_selo, tom, est=None):
    """Badge (`.badge-*`) para usar dentro de tabelas."""
    est = est or estilos()
    cor, _ = TONS.get(tom, TONS["secondary"])
    estilo = ParagraphStyle("selo", est["celula"], textColor=cor, fontName="Plex-SemiBold")
    return Paragraph(texto(texto_selo), estilo)


def kpis(pares, largura, est=None):
    """Linha de cartões de totais como `.kpi`: [(rótulo, valor, destaque?)]."""
    est = est or estilos()
    coluna = largura / len(pares)
    cartoes = []
    for rotulo, valor, destaque in pares:
        cartao = Table([[p(rotulo.upper(), est["rotulo"])], [p(valor, est["kpi"])]], colWidths=[coluna - 4])
        cartao.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, MARCA if destaque else BORDA),
                    ("BACKGROUND", (0, 0), (-1, -1), MARCA_SUAVE if destaque else colors.white),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (0, 0), 6),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
                ]
            )
        )
        cartoes.append(cartao)
    linha = Table([cartoes], colWidths=[coluna] * len(pares))
    linha.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    return linha


def definicoes(pares, largura, est=None):
    """Lista rótulo → valor (como `.definicoes`)."""
    est = est or estilos()
    linhas = [[p(rotulo, est["suave"]), p(valor, est["base"])] for rotulo, valor in pares]
    tabela = Table(linhas, colWidths=[38 * mm, largura - 38 * mm])
    tabela.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDA),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return tabela


def tabela(cabecalho, linhas, larguras, numericas=(), est=None, destacar=None, repetir=1):
    """Tabela de dados como `.tabela` das telas: cabeçalho cinza, linhas com borda fina.

    `numericas`: índices das colunas alinhadas à direita. `destacar`: {indice_linha: tom} pinta o fundo.
    """
    est = est or estilos()

    def celula(valor, i, estilo, estilo_num):
        if hasattr(valor, "wrap"):
            return valor
        return p(valor, estilo_num if i in numericas else estilo)

    dados = [[celula(v, i, est["cab"], est["cab_num"]) for i, v in enumerate(cabecalho)]]
    dados += [[celula(v, i, est["celula"], est["celula_num"]) for i, v in enumerate(linha)] for linha in linhas]
    t = Table(dados, colWidths=larguras, repeatRows=repetir)
    comandos = [
        ("BACKGROUND", (0, 0), (-1, 0), FUNDO_2),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, BORDA),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, BORDA),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for indice, tom in (destacar or {}).items():
        comandos.append(("BACKGROUND", (0, indice + 1), (-1, indice + 1), TONS[tom][1]))
    t.setStyle(TableStyle(comandos))
    return t


class _CanvasNumerado(rl_canvas.Canvas):
    """Canvas que guarda as páginas para escrever "página N de M" no fim."""

    def __init__(self, *args, rodape=None, cabecalho=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._paginas = []
        self._rodape = rodape
        self._cabecalho = cabecalho

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self._desenhar(total)
            super().showPage()
        super().save()

    def _desenhar(self, total):
        largura, altura = self._pagesize
        margem = 15 * mm
        # Cabeçalho: marca à esquerda, escritório e título à direita.
        self.setFillColor(LATERAL)
        self.setFont("Plex-SemiBold", 10)
        self.drawString(margem, altura - 11 * mm, "Tributo360")
        self.setFillColor(SUAVE)
        self.setFont("Plex", 7.5)
        self.drawRightString(largura - margem, altura - 11 * mm, self._cabecalho or "")
        self.setStrokeColor(BORDA)
        self.setLineWidth(0.6)
        self.line(margem, altura - 13 * mm, largura - margem, altura - 13 * mm)
        # Rodapé: gerado por/em e versões; página N de M.
        self.line(margem, 13 * mm, largura - margem, 13 * mm)
        self.setFont("Plex", 6.8)
        y = 9.5 * mm
        for linha in (self._rodape or [])[:2]:
            self.drawString(margem, y, linha)
            y -= 3.2 * mm
        self.drawRightString(largura - margem, 9.5 * mm, f"Página {self._pageNumber} de {total}")


def gerar(elementos, *, titulo, cabecalho, rodape, paisagem=False):
    """Monta o PDF e devolve os bytes.

    `cabecalho`: texto à direita do topo (escritório). `rodape`: até 2 linhas (gerado por/em, versões).
    """
    _registrar_fontes()
    buffer = io.BytesIO()
    tamanho = landscape(A4) if paisagem else A4
    doc = SimpleDocTemplate(
        buffer,
        pagesize=tamanho,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=19 * mm,
        bottomMargin=19 * mm,
        title=titulo,
        author="Tributo360",
        creator="Tributo360",
    )
    doc.build(
        elementos,
        canvasmaker=lambda *a, **k: _CanvasNumerado(*a, rodape=rodape, cabecalho=cabecalho, **k),
    )
    return buffer.getvalue()


def largura_util(paisagem=False):
    tamanho = landscape(A4) if paisagem else A4
    return tamanho[0] - 30 * mm


def linha_gerado(request):
    agora = timezone.localtime()
    return f"Gerado em {agora:%d/%m/%Y %H:%M} por {nome_usuario(request.user)} · {request.membership.escritorio}"


__all__ = [
    "KeepTogether",
    "Table",
    "TableStyle",
    "mm",
    "Spacer",
    "aviso",
    "definicoes",
    "estilos",
    "formatar_cnpj",
    "gerar",
    "kpis",
    "largura_util",
    "linha_gerado",
    "moeda",
    "nome_usuario",
    "p",
    "selo",
    "tabela",
    "texto",
]
