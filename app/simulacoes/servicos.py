"""Primeiros cálculos pelo Django: monta a operação, chama o motor, grava tudo com a versão.

As regras de cálculo ficam nos motores. Aqui ficam só as nossas: de onde vem cada alíquota (D2/D3),
o que vai para o banco e o que o contador vê.
"""

import uuid
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from auditoria.servicos import registrar, snapshot
from engines import aliquotas, clientes, registro
from engines.contratos import (
    AliquotaAnexoSN,
    AliquotasEfetivasSN,
    AliquotasNominais,
    ImpostoSeletivo,
    ItemRegular,
    ItemSimples,
    OperacaoRegular,
    OperacaoSimples,
    ParAliquotaSN,
)

from .models import AliquotaEfetivaSN, Simulacao, SimulacaoItem

ORIGEM_USUARIO_SN = "informada_pelo_usuario"
ORIGEM_CADASTRO_SN = "cadastro_do_periodo"
NOMES_CAMPOS = {"cbs": "CBS", "ibs_estadual": "IBS estadual", "ibs_municipal": "IBS municipal"}


def _sem_nulos(dados):
    return {chave: valor for chave, valor in (dados or {}).items() if valor is not None}


# --- Regime regular -----------------------------------------------------------------------------


def _montar_itens_regular(dados, data):
    """Itens do contrato + origem das alíquotas de cada um. Levanta ValidationError se faltar alíquota."""
    municipio = dados.get("municipio") or dados["empresa"].municipio_ibge
    aliq_operacao = _sem_nulos(dados.get("aliquotas"))
    exige = aliquotas.exige_aliquotas(data)
    propostas = aliquotas.propostas_da_tabela(data, municipio) if exige else None

    itens, origens, erros = [], [], {}
    for numero, item in enumerate(dados["itens"], start=1):
        informadas = {**aliq_operacao, **_sem_nulos(item.get("aliquotas"))}
        nominais = None
        if exige:
            valores, origem, faltando = aliquotas.resolver(propostas, informadas)
            if faltando:
                nomes = ", ".join(NOMES_CAMPOS[c] for c in faltando)
                erros[f"itens[{numero - 1}].aliquotas"] = [
                    f"Sem alíquota de {nomes} na tabela do Tributo360 para {data:%d/%m/%Y} neste local. "
                    "Informe o valor na simulação."
                ]
                continue
            nominais = AliquotasNominais(
                cbs=valores["cbs"],
                ibs_estadual=valores["ibs_estadual"],
                ibs_municipal=valores["ibs_municipal"],
                is_ad_valorem=informadas.get("is_ad_valorem"),
                is_ad_rem=informadas.get("is_ad_rem"),
            )
            for campo in ("is_ad_valorem", "is_ad_rem"):
                if informadas.get(campo) is not None:
                    origem[campo] = {"origem": aliquotas.ORIGEM_USUARIO, "valor": str(informadas[campo])}
        else:
            if any(informadas.get(c) is not None for c in aliquotas.CAMPOS):
                erros[f"itens[{numero - 1}].aliquotas"] = [
                    "Antes de 2027 o motor usa as alíquotas de teste de 2026 e não aceita outras. "
                    "Retire as alíquotas de CBS/IBS."
                ]
                continue
            origem = {}  # completada com o que o motor aplicou, depois do cálculo

        seletivo = item.get("imposto_seletivo")
        itens.append(
            ItemRegular(
                numero=numero,
                cst=item["cst"],
                c_class_trib=item["c_class_trib"],
                base_calculo=item.get("base_calculo"),
                ncm=item.get("ncm", ""),
                nbs=item.get("nbs", ""),
                quantidade=item.get("quantidade"),
                unidade=item.get("unidade", ""),
                aliquotas=nominais,
                imposto_seletivo=ImpostoSeletivo(**seletivo) if seletivo else None,
            )
        )
        origens.append(origem)
    if erros:
        raise serializers.ValidationError(erros)
    return itens, origens, int(municipio)


def _origem_motor_2026(resultado_item):
    v = resultado_item.valores
    pares = {"cbs": v.aliquota_cbs, "ibs_estadual": v.aliquota_ibs_uf, "ibs_municipal": v.aliquota_ibs_mun}
    return {
        campo: {"origem": aliquotas.ORIGEM_MOTOR_2026, "valor": str(valor)}
        for campo, valor in pares.items()
        if valor is not None
    }


def simular_regime_regular(request, dados, entrada):
    """Calcula uma operação do regime regular. `dados` vem validado por `SimulacaoRegularSerializer`."""
    empresa = dados["empresa"]
    fato_gerador = dados["data_fato_gerador"]
    data = fato_gerador.date()
    itens, origens, municipio = _montar_itens_regular(dados, data)

    simulacao_id = uuid.uuid4()
    operacao = OperacaoRegular(
        id=simulacao_id.hex,
        fato_gerador=fato_gerador,
        uf=dados.get("uf") or empresa.uf,
        municipio=municipio,
        tp_doc=dados.get("tp_doc"),
        itens=tuple(itens),
    )
    resultado = clientes.regime_regular().calcular(operacao, contexto={"simulacao_id": simulacao_id})

    por_numero = {r.numero: r for r in resultado.itens}
    linhas = []
    for item, origem in zip(itens, origens, strict=True):
        calculado = por_numero.get(item.numero)
        if not aliquotas.exige_aliquotas(data) and calculado is not None:
            origem = _origem_motor_2026(calculado)
        linhas.append(
            {
                "item": calculado,
                "numero": item.numero,
                "ncm": item.ncm,
                "nbs": item.nbs,
                "cst": item.cst,
                "c_class_trib": item.c_class_trib,
                "base_calculo": item.base_calculo,
                "origem_aliquotas": origem,
                "item_catalogo": dados["itens"][item.numero - 1].get("item_catalogo"),
            }
        )
    return _gravar(request, empresa, Simulacao.Tipo.REGULAR, data, dados, entrada, resultado, linhas, simulacao_id)


# --- Simples Nacional ---------------------------------------------------------------------------


def _par(dados):
    return ParAliquotaSN(cbs=dados["cbs"], ibs=dados["ibs"])


class SemAliquotasDoPeriodo(serializers.ValidationError):
    default_code = "sem_aliquotas_do_periodo"


def primeiro_dia(data):
    return data.replace(day=1)


def aliquotas_do_periodo(empresa, data):
    """Cadastro de alíquotas efetivas da empresa para o mês de `data`, ou ``None``."""
    return AliquotaEfetivaSN.objects.filter(empresa=empresa, periodo=primeiro_dia(data)).first()


def _decimais(par):
    return {"cbs": Decimal(str(par["cbs"])), "ibs": Decimal(str(par["ibs"]))}


def efetivas_do_cadastro(cadastro):
    """Converte o cadastro no mesmo formato que `AliquotasEfetivasSNSerializer` entrega."""
    anexos = {}
    for anexo, valor in cadastro.anexos.items():
        anexos[anexo] = _decimais(valor)
        if valor.get("sem_redistribuicao_iss"):
            anexos[anexo]["sem_redistribuicao_iss"] = _decimais(valor["sem_redistribuicao_iss"])
    return {
        "fator_r": cadastro.fator_r,
        "impedimento_sublimite": cadastro.impedimento_sublimite,
        "anexos": anexos,
    }


def simular_simples(request, dados, entrada):
    """Cálculo não assistido do Simples com as alíquotas efetivas do PGDAS-D (D3).

    As alíquotas vêm do cadastro da empresa para o mês da operação (`AliquotaEfetivaSN`) ou, se a
    requisição trouxer `aliquotas_efetivas`, só para esta simulação. Sem nenhum dos dois, a simulação
    é recusada antes de chamar o motor.
    """
    empresa = dados["empresa"]
    efetivas = dados.get("aliquotas_efetivas")
    origem_base = {"origem": ORIGEM_USUARIO_SN}
    if efetivas is None:
        cadastro = aliquotas_do_periodo(empresa, dados["data"])
        if cadastro is None:
            raise SemAliquotasDoPeriodo(
                {
                    "aliquotas_efetivas": [
                        f"Sem alíquotas efetivas do Simples cadastradas para {dados['data']:%m/%Y}. "
                        "Cadastre as alíquotas do PGDAS-D deste período antes de simular."
                    ]
                }
            )
        efetivas = efetivas_do_cadastro(cadastro)
        origem_base = {
            "origem": ORIGEM_CADASTRO_SN,
            "aliquota_efetiva_sn_id": str(cadastro.pk),
            "periodo": f"{cadastro.periodo:%Y-%m}",
            "informado_por": cadastro.informado_por.email if cadastro.informado_por else "",
            "atualizado_em": cadastro.atualizado_em.isoformat(),
        }
    anexos = {
        anexo: AliquotaAnexoSN(
            par=_par(valor),
            sem_redistribuicao_iss=_par(valor["sem_redistribuicao_iss"])
            if valor.get("sem_redistribuicao_iss")
            else None,
        )
        for anexo, valor in efetivas["anexos"].items()
    }
    nfse = dados["tp_doc"] == "91"
    crt = dados.get("crt") or ("" if nfse else str(empresa.crt))
    op_simp_nac = dados.get("op_simp_nac") or ("" if not nfse else ("2" if empresa.regime == "mei" else "3"))

    itens = tuple(
        ItemSimples(
            numero=numero,
            c_ativ_sn=item["c_ativ_sn"],
            c_class_trib=item["c_class_trib"],
            base_calculo=item["base_calculo"],
            ncm=item.get("ncm", ""),
            nbs=item.get("nbs", ""),
            valor_imposto_seletivo=item.get("valor_imposto_seletivo"),
            nfse={
                "tribISSQN": item.get("trib_issqn", ""),
                "tpRetISSQN": item.get("tp_ret_issqn", ""),
                "tpBM": item.get("tp_bm", ""),
            },
        )
        for numero, item in enumerate(dados["itens"], start=1)
    )
    simulacao_id = uuid.uuid4()
    operacao = OperacaoSimples(
        id=simulacao_id.hex,
        data=dados["data"],
        tp_doc=dados["tp_doc"],
        crt=crt,
        op_simp_nac=op_simp_nac,
        reg_ap_ibscbs_sn=dados.get("reg_ap_ibscbs_sn", ""),
        reg_ap_trib_sn=dados.get("reg_ap_trib_sn", ""),
        aliquotas_efetivas=AliquotasEfetivasSN(
            fator_r=efetivas["fator_r"], impedimento_sublimite=efetivas["impedimento_sublimite"], anexos=anexos
        ),
        itens=itens,
    )
    resultado = clientes.simples().calcular(operacao, contexto={"simulacao_id": simulacao_id})

    por_numero = {r.numero: r for r in resultado.itens}
    linhas = []
    for item in itens:
        calculado = por_numero.get(item.numero)
        resolucao = calculado.extras.get("resolucao_aliquota", {}) if calculado else {}
        origem = dict(origem_base)
        if resolucao:
            origem.update(
                {
                    "anexo": resolucao.get("anexoAplicado"),
                    "tipo": resolucao.get("tipoAliquota"),
                    "pCBSSN": resolucao.get("pCBSSN"),
                    "pIBSSN": resolucao.get("pIBSSN"),
                }
            )
        linhas.append(
            {
                "item": calculado,
                "numero": item.numero,
                "ncm": item.ncm,
                "nbs": item.nbs,
                "c_class_trib": item.c_class_trib,
                "c_ativ_sn": item.c_ativ_sn,
                "base_calculo": item.base_calculo,
                "origem_aliquotas": origem,
                "item_catalogo": dados["itens"][item.numero - 1].get("item_catalogo"),
            }
        )
    return _gravar(
        request, empresa, Simulacao.Tipo.SIMPLES, dados["data"], dados, entrada, resultado, linhas, simulacao_id
    )


# --- Gravação -----------------------------------------------------------------------------------


def _situacao(resultado):
    if not resultado.todos_calcularam:
        return Simulacao.Situacao.COM_INCONSISTENCIA
    if resultado.simulado:
        return Simulacao.Situacao.SIMULADO
    return Simulacao.Situacao.CALCULADO


def _gravar(request, empresa, tipo, data, dados, entrada, resultado, linhas, simulacao_id):
    versao = registro.versao_do_calculo(resultado)
    escritorio = request.membership.escritorio
    with transaction.atomic():
        simulacao = Simulacao.objects.create(
            id=simulacao_id,
            escritorio=escritorio,
            empresa=empresa,
            usuario=request.user,
            tipo=tipo,
            rotulo=dados.get("rotulo", ""),
            data_fato_gerador=data,
            versao_motor=versao,
            situacao=_situacao(resultado),
            simulado=resultado.simulado,
            aviso_simulado=resultado.aviso_simulado,
            entrada=entrada,
            payload_enviado=resultado.payload_enviado,
            resposta_bruta=resultado.resposta_bruta,
            total_base_calculo=resultado.totais.base_calculo,
            total_cbs=resultado.totais.cbs,
            total_ibs=resultado.totais.ibs,
            total_imposto_seletivo=resultado.totais.imposto_seletivo,
            duracao_ms=resultado.duracao_ms,
        )
        SimulacaoItem.objects.bulk_create(
            [
                SimulacaoItem(
                    escritorio=escritorio,
                    simulacao=simulacao,
                    item_catalogo=linha.get("item_catalogo"),
                    numero=linha["numero"],
                    estado=str(linha["item"].estado) if linha["item"] else "SEM_RESPOSTA",
                    ncm=linha.get("ncm", ""),
                    nbs=linha.get("nbs", ""),
                    cst=linha.get("cst", ""),
                    c_class_trib=linha.get("c_class_trib", ""),
                    c_ativ_sn=linha.get("c_ativ_sn", ""),
                    base_calculo=linha.get("base_calculo"),
                    valores=linha["item"].valores.como_dict() if linha["item"] else {},
                    erros=[e.como_dict() for e in linha["item"].erros] if linha["item"] else [],
                    memoria=linha["item"].memoria if linha["item"] else {},
                    origem_aliquotas=linha["origem_aliquotas"],
                    extras=linha["item"].extras if linha["item"] else {},
                )
                for linha in linhas
            ]
        )
        registrar(
            request,
            "simulacao.criar",
            simulacao,
            depois={
                "tipo": tipo,
                "empresa": str(empresa.pk),
                "versao_motor": str(versao),
                "situacao": simulacao.situacao,
                "totais": resultado.totais.como_dict(),
            },
        )
    return simulacao


# --- Alíquotas efetivas do Simples por período ------------------------------------------------------


def _anexos_para_json(anexos):
    saida = {}
    for anexo, valor in anexos.items():
        saida[anexo] = {"cbs": str(valor["cbs"]), "ibs": str(valor["ibs"])}
        if valor.get("sem_redistribuicao_iss"):
            par = valor["sem_redistribuicao_iss"]
            saida[anexo]["sem_redistribuicao_iss"] = {"cbs": str(par["cbs"]), "ibs": str(par["ibs"])}
    return saida


def periodo_em_uso(empresa, periodo, ignorar_pk=None):
    qs = AliquotaEfetivaSN.objects.filter(empresa=empresa, periodo=primeiro_dia(periodo))
    if ignorar_pk:
        qs = qs.exclude(pk=ignorar_pk)
    return qs.exists()


@transaction.atomic
def salvar_aliquotas_sn(request, empresa, dados, instancia=None):
    """Cria ou edita o cadastro de um período. Edição fica na auditoria com o antes e o depois."""
    antes = snapshot(instancia) if instancia else None
    if instancia is None:
        instancia = AliquotaEfetivaSN(escritorio=request.membership.escritorio, empresa=empresa)
    instancia.periodo = primeiro_dia(dados["periodo"])
    instancia.fator_r = dados["fator_r"]
    instancia.impedimento_sublimite = dados.get("impedimento_sublimite", False)
    instancia.anexos = _anexos_para_json(dados["anexos"])
    instancia.observacao = dados.get("observacao", "")
    instancia.informado_por = request.user
    instancia.save()
    acao = "aliquota_sn.editar" if antes else "aliquota_sn.criar"
    registrar(request, acao, instancia, antes=antes, depois=snapshot(instancia))
    return instancia


@transaction.atomic
def remover_aliquotas_sn(request, instancia):
    registrar(request, "aliquota_sn.remover", instancia, antes=snapshot(instancia))
    instancia.delete()
