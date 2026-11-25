"""Base das planilhas (openpyxl): estilos do tema, cabeçalho congelado e números como números.

Valores monetários e alíquotas vão como número (não texto), para o contador somar e filtrar no Excel.
"""

import io
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

MOEDA = "#,##0.00;[Red]-#,##0.00"
PERCENTUAL = "0.00##"
DATA = "dd/mm/yyyy"
DATA_HORA = "dd/mm/yyyy hh:mm"

_CAB_FONTE = Font(bold=True, color="FFFFFF")
_CAB_FUNDO = PatternFill("solid", fgColor="0F6B7D")
_TITULO = Font(bold=True, size=14, color="1B2430")
_SUAVE = Font(color="637082", size=9)
_ROTULO = Font(color="637082")
_NEGRITO = Font(bold=True)
_LINHA = Border(bottom=Side(style="thin", color="E1E6EB"))
TONS = {
    "danger": PatternFill("solid", fgColor="FDECEA"),
    "warning": PatternFill("solid", fgColor="FFF1D9"),
    "success": PatternFill("solid", fgColor="E3F4EA"),
    "info": PatternFill("solid", fgColor="E5EFFB"),
}


def numero(valor):
    """Decimal → float para a célula (None/'' ficam vazios)."""
    if valor in (None, ""):
        return None
    try:
        return float(Decimal(str(valor)))
    except (InvalidOperation, ValueError):
        return None


def sem_fuso(valor):
    """openpyxl não grava datetime com fuso: converte para o horário local e tira o tzinfo."""
    from django.utils import timezone

    if valor is not None and getattr(valor, "tzinfo", None) is not None:
        return timezone.localtime(valor).replace(tzinfo=None)
    return valor


class Planilha:
    def __init__(self):
        self.livro = Workbook()
        self._primeira = True

    def aba(self, nome):
        if self._primeira:
            folha = self.livro.active
            folha.title = nome
            self._primeira = False
        else:
            folha = self.livro.create_sheet(nome)
        return folha

    def salvar(self):
        buffer = io.BytesIO()
        self.livro.save(buffer)
        return buffer.getvalue()


def titulo(folha, texto, subtitulo=""):
    folha.append([texto])
    folha.cell(row=folha.max_row, column=1).font = _TITULO
    if subtitulo:
        folha.append([subtitulo])
        folha.cell(row=folha.max_row, column=1).font = _SUAVE
    folha.append([])


def pares(folha, linhas):
    """Bloco rótulo → valor (aba de resumo). `linhas`: [(rótulo, valor, formato|None)]."""
    for rotulo, valor, formato in linhas:
        folha.append([rotulo, valor])
        linha = folha.max_row
        folha.cell(row=linha, column=1).font = _ROTULO
        celula = folha.cell(row=linha, column=2)
        celula.alignment = Alignment(wrap_text=True, vertical="top")
        if formato:
            celula.number_format = formato
    folha.column_dimensions["A"].width = 28
    folha.column_dimensions["B"].width = 60


def tabela(folha, colunas, linhas, destaques=None):
    """Tabela com cabeçalho na cor da marca, filtro e painel congelado.

    `colunas`: [(título, largura, formato|None)]. `destaques`: {índice da linha: tom} pinta a linha.
    """
    inicio = folha.max_row + 1 if folha._cells else 1  # aba vazia: o append vai para a linha 1
    folha.append([c[0] for c in colunas])
    for i, (_, largura, _) in enumerate(colunas, start=1):
        celula = folha.cell(row=inicio, column=i)
        celula.font = _CAB_FONTE
        celula.fill = _CAB_FUNDO
        celula.alignment = Alignment(vertical="center", wrap_text=True)
        letra = get_column_letter(i)
        atual = folha.column_dimensions[letra].width or 0
        folha.column_dimensions[letra].width = max(atual, largura)
    for n, linha in enumerate(linhas):
        folha.append(list(linha))
        r = folha.max_row
        for i, (_, _, formato) in enumerate(colunas, start=1):
            celula = folha.cell(row=r, column=i)
            celula.border = _LINHA
            celula.alignment = Alignment(vertical="top", wrap_text=isinstance(celula.value, str))
            if formato:
                celula.number_format = formato
            if destaques and n in destaques:
                celula.fill = TONS[destaques[n]]
    fim = folha.max_row
    folha.auto_filter.ref = f"A{inicio}:{get_column_letter(len(colunas))}{max(fim, inicio)}"
    folha.freeze_panes = folha.cell(row=inicio + 1, column=1)
    return inicio


def rodape(folha, linhas):
    folha.append([])
    for texto in linhas:
        folha.append([texto])
        folha.cell(row=folha.max_row, column=1).font = _SUAVE


def total(folha, rotulo, valores_por_coluna, formato=MOEDA):
    """Linha de total em negrito: `valores_por_coluna` = {índice da coluna (1-based): valor}."""
    folha.append([rotulo])
    r = folha.max_row
    folha.cell(row=r, column=1).font = _NEGRITO
    for coluna, valor in valores_por_coluna.items():
        celula = folha.cell(row=r, column=coluna, value=valor)
        celula.font = _NEGRITO
        celula.number_format = formato
