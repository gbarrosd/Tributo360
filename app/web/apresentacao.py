"""Como uma simulação aparece na tela: origem de cada alíquota, premissas e comparação de cenários."""

from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_datetime

from engines import aliquotas
from simulacoes.models import Simulacao
from simulacoes.servicos import ORIGEM_CADASTRO_SN, ORIGEM_USUARIO_SN

NOMES_ALIQUOTAS = {
    "cbs": "CBS",
    "ibs_estadual": "IBS estadual",
    "ibs_municipal": "IBS municipal",
    "is_ad_valorem": "IS ad valorem",
    "is_ad_rem": "IS ad rem",
}
ROTULOS_ORIGEM = {
    aliquotas.ORIGEM_MOTOR_2026: "alíquota de teste do motor (2026)",
    aliquotas.ORIGEM_TABELA: "tabela do Tributo360",
    aliquotas.ORIGEM_USUARIO: "editada pelo usuário",
    ORIGEM_CADASTRO_SN: "cadastro do período",
    ORIGEM_USUARIO_SN: "informada só nesta simulação",
}


def decimal(valor):
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return None


def aliquotas_do_item(item):
    """Lista [{nome, valor, origem, detalhe}] a partir de `SimulacaoItem.origem_aliquotas`."""
    origem = item.origem_aliquotas or {}
    if item.simulacao.tipo == Simulacao.Tipo.SIMPLES:
        detalhe = []
        if origem.get("origem") == ORIGEM_CADASTRO_SN and origem.get("periodo"):
            ano, mes = origem["periodo"].split("-")
            detalhe.append(f"PGDAS-D {mes}/{ano}")
        if origem.get("anexo"):
            detalhe.append(f"anexo {origem['anexo']}")
        if origem.get("tipo"):
            detalhe.append(str(origem["tipo"]).lower().replace("_", " "))
        rotulo = ROTULOS_ORIGEM.get(origem.get("origem"), origem.get("origem", ""))
        return [
            {"nome": "CBS", "valor": decimal(origem.get("pCBSSN")), "origem": rotulo, "detalhe": ", ".join(detalhe)},
            {"nome": "IBS", "valor": decimal(origem.get("pIBSSN")), "origem": rotulo, "detalhe": ", ".join(detalhe)},
        ]
    linhas = []
    for campo, nome in NOMES_ALIQUOTAS.items():
        dado = origem.get(campo)
        if not dado:
            continue
        detalhe = ""
        if dado.get("origem") == aliquotas.ORIGEM_TABELA:
            detalhe = f"fonte: {dado.get('fonte', '')}"
            if dado.get("data_publicacao"):
                detalhe += f" (publicada em {_data_br(dado['data_publicacao'])})"
        elif dado.get("origem") == aliquotas.ORIGEM_USUARIO:
            anterior = dado.get("valor_anterior")
            detalhe = f"tabela sugeria {_pct(anterior)}" if anterior is not None else "sem valor na tabela"
        linhas.append(
            {
                "nome": nome,
                "valor": decimal(dado.get("valor")),
                "origem": ROTULOS_ORIGEM.get(dado.get("origem"), dado.get("origem", "")),
                "codigo_origem": dado.get("origem"),
                "detalhe": detalhe,
            }
        )
    return linhas


def _pct(valor):
    numero = decimal(valor)
    return "—" if numero is None else f"{numero.normalize():f}".replace(".", ",") + "%"


def _data_br(texto):
    partes = str(texto)[:10].split("-")
    return "/".join(reversed(partes)) if len(partes) == 3 else texto


def codigo_do_item(item):
    if item.ncm:
        return f"NCM {item.ncm}"
    if item.nbs:
        return f"NBS {item.nbs}"
    return ""


def premissas(simulacao):
    """O que foi assumido no cálculo, para mostrar e comparar cenários."""
    entrada = simulacao.entrada or {}
    payload = simulacao.payload_enviado or {}
    dados = {
        "data": simulacao.data_fato_gerador,
        "versao": simulacao.versao_motor,
    }
    if simulacao.tipo == Simulacao.Tipo.REGULAR:
        dados["local"] = f"{payload.get('uf', '')} · município {payload.get('municipio', '')}"
        dados["fato_gerador"] = parse_datetime(payload.get("dhFatoGerador") or "")
        dados["documento"] = f"tpDoc {payload['tpDoc']}" if payload.get("tpDoc") else "não informado"
    else:
        dados["local"] = ""
        dados["documento"] = f"tpDoc {entrada.get('tp_doc') or payload.get('tpDoc', '')}"
        primeira = next(iter(simulacao.itens.all()), None)
        origem = (primeira.origem_aliquotas or {}) if primeira else {}
        if origem.get("origem") == ORIGEM_CADASTRO_SN:
            ano, mes = origem.get("periodo", "-").split("-")
            dados["aliquotas_sn"] = f"Cadastro do período {mes}/{ano}"
        else:
            dados["aliquotas_sn"] = "Informadas só nesta simulação"
    dados["itens"] = [
        {
            "numero": item.numero,
            "codigo": codigo_do_item(item),
            "cst": item.cst,
            "c_class_trib": item.c_class_trib,
            "c_ativ_sn": item.c_ativ_sn,
            "base": item.base_calculo,
            "estado": item.estado,
            "aliquotas": aliquotas_do_item(item),
        }
        for item in simulacao.itens.all()
    ]
    return dados


def total_tributos(simulacao):
    return simulacao.total_cbs + simulacao.total_ibs + simulacao.total_imposto_seletivo


def comparacao(simulacoes):
    """Colunas para a tela de comparação, com a diferença de cada cenário para o primeiro."""
    colunas = []
    base = None
    for simulacao in simulacoes:
        total = total_tributos(simulacao)
        if base is None:
            base = simulacao
        diferenca = total - total_tributos(base)
        colunas.append(
            {
                "simulacao": simulacao,
                "premissas": premissas(simulacao),
                "total_tributos": total,
                "diferenca": diferenca,
                "diferenca_pct": (diferenca / total_tributos(base) * 100) if total_tributos(base) else None,
                "carga": (total / simulacao.total_base_calculo * 100) if simulacao.total_base_calculo else None,
            }
        )
    return colunas
