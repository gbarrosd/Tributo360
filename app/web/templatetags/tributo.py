"""Formatação brasileira de valores e a memória de cálculo dos motores."""

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

register = template.Library()


def _decimal(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return None


def _br(numero, casas):
    texto = f"{numero:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


@register.filter
def moeda(valor):
    numero = _decimal(valor)
    return "—" if numero is None else f"R$ {_br(numero, 2)}"


@register.filter
def numero(valor, casas=2):
    numero = _decimal(valor)
    return "—" if numero is None else _br(numero, int(casas))


@register.filter
def pct(valor):
    """Alíquota em %, sem zeros à direita desnecessários (0,9% e não 0,9000%)."""
    numero = _decimal(valor)
    if numero is None:
        return "—"
    casas = max(2, min(4, -numero.normalize().as_tuple().exponent))
    return f"{_br(numero, casas)}%"


@register.filter
def sinal(valor):
    numero = _decimal(valor)
    if numero is None:
        return "—"
    return ("+" if numero > 0 else "") + _br(numero, 2)


ESTADOS = {
    "CALCULADO": ("success", "Calculado"),
    "CALCULO_SIMULADO": ("info", "Calculado (simulado)"),
    "INCONSISTENCIA_ENTRADA": ("danger", "Inconsistência na entrada"),
    "NAO_IMPLEMENTADO": ("warning", "Não implementado no motor"),
    "FALHA_TECNICA": ("danger", "Falha técnica"),
    "SEM_RESPOSTA": ("secondary", "Sem resposta"),
}


@register.simple_tag
def estado_item(estado):
    cor, texto = ESTADOS.get(estado, ("secondary", estado))
    return format_html('<span class="badge badge-{}">{}</span>', cor, texto)


SITUACOES = {
    "calculado": ("success", "Calculado"),
    "simulado": ("info", "Simulado"),
    "com_inconsistencia": ("warning", "Itens não calculados"),
}


@register.simple_tag
def situacao(valor):
    cor, texto = SITUACOES.get(valor, ("secondary", valor))
    return format_html('<span class="badge badge-{}">{}</span>', cor, texto)


STATUS_ITEM = {
    "pendente": ("warning", "Pendente"),
    "revisar": ("danger", "Revisar"),
    "aprovado": ("success", "Aprovado"),
}


@register.simple_tag
def status_item(valor):
    cor, texto = STATUS_ITEM.get(valor, ("secondary", valor))
    return format_html('<span class="badge badge-{}">{}</span>', cor, texto)


ROTULOS_MEMORIA = {"cbs": "CBS", "ibsUF": "IBS estadual", "ibsMun": "IBS municipal", "is": "Imposto Seletivo"}


@register.filter
def memoria(dados):
    """Memória de cálculo do motor (dict de textos, às vezes aninhado) como lista de definições."""
    if not dados:
        return mark_safe('<p class="text-suave text-xs">O motor não devolveu memória de cálculo.</p>')
    return _memoria_html(dados)


def _memoria_html(dados):
    if isinstance(dados, dict):
        linhas = format_html_join(
            "",
            "<dt>{}</dt><dd>{}</dd>",
            ((ROTULOS_MEMORIA.get(chave, chave), _memoria_html(valor)) for chave, valor in dados.items()),
        )
        return format_html('<dl class="definicoes definicoes-memoria">{}</dl>', linhas)
    if isinstance(dados, list):
        itens = format_html_join("", "<li>{}</li>", ((_memoria_html(v),) for v in dados))
        return format_html('<ul class="list-disc pl-5">{}</ul>', itens)
    return format_html("{}", dados)


@register.filter
def json_bonito(dados):
    import json

    from django.core.serializers.json import DjangoJSONEncoder

    return json.dumps(dados, indent=2, ensure_ascii=False, cls=DjangoJSONEncoder)


@register.filter
def item_da_lista(lista, indice):
    try:
        return lista[int(indice)]
    except (IndexError, TypeError, ValueError):
        return None


@register.filter
def controle(campo, extra=""):
    """Renderiza o widget do campo com as classes do tema (e is-invalid quando há erro)."""
    tipo = getattr(campo.field.widget, "input_type", "")
    classe = "caixa" if tipo == "checkbox" else "campo"
    if campo.errors:
        classe += " is-invalid"
    if extra:
        classe += f" {extra}"
    return campo.as_widget(attrs={"class": classe})
