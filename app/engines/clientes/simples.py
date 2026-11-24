"""Cliente do motor do Simples Nacional (`api-simples-nacional`)."""

import uuid
from datetime import date
from decimal import Decimal

from ..contratos import (
    EstadoItem,
    Motor,
    OperacaoSimples,
    ResultadoCalculo,
    ResultadoItem,
    Totais,
    ValoresItem,
    VersaoInfo,
)
from ..http import ClienteMotor, erro_de_problem
from .comum import cabecalho_simulado, decimal_ou_none, texto, versao_dos_cabecalhos

PREFIXO = "/api/calculadora"

# Campos da NFS-e que o motor aceita por item e que mudam o par de alíquotas aplicado.
CAMPOS_NFSE_ITEM = ("tribISSQN", "tpRetISSQN", "tpBM")


def _dec(valor):
    return None if valor is None else str(valor)


def _sem_vazios(dados):
    return {chave: valor for chave, valor in dados.items() if valor not in (None, "")}


def _par(par):
    return {"pCBSSN": _dec(par.cbs), "pIBSSN": _dec(par.ibs)}


def payload_operacao(op: OperacaoSimples):
    """Traduz o contrato interno para o JSON do `observabilidade/simples-nacional/nao-assistido`."""
    aliquotas = {
        "fatorR": op.aliquotas_efetivas.fator_r,
        "impedimentoSublimite": op.aliquotas_efetivas.impedimento_sublimite,
    }
    for anexo, valor in op.aliquotas_efetivas.anexos.items():
        dados = _par(valor.par)
        if valor.sem_redistribuicao_iss:
            dados["semRedistribuicaoISS"] = _par(valor.sem_redistribuicao_iss)
        aliquotas[f"anexo{anexo}"] = dados

    itens = []
    for item in op.itens:
        dados = {
            "numero": item.numero,
            "cAtivSN": item.c_ativ_sn,
            "cClassTrib": item.c_class_trib,
            "ncm": item.ncm,
            "nbs": item.nbs,
            "baseCalculo": _dec(item.base_calculo),
            "valorImpostoSeletivo": _dec(item.valor_imposto_seletivo),
        }
        dados.update({campo: item.nfse[campo] for campo in CAMPOS_NFSE_ITEM if item.nfse.get(campo)})
        itens.append(_sem_vazios(dados))

    return _sem_vazios(
        {
            "id": op.id or uuid.uuid4().hex,  # obrigatório no motor (VAL-001)
            "versao": "3.0.0",  # versão do leiaute de entrada usada nos exemplos oficiais
            "data": op.data.isoformat(),
            "tpDoc": op.tp_doc,
            "CRT": op.crt,
            "opSimpNac": op.op_simp_nac,
            "regApIBSCBSSN": op.reg_ap_ibscbs_sn,
            "regApTribSN": op.reg_ap_trib_sn,
            "aliquotasEfetivas": aliquotas,
            "itens": itens,
        }
    )


def interpretar_resposta(corpo, cabecalhos, payload, versao, duracao_ms=0):
    resolucao = corpo.get("resolucaoAliquotas") or {}
    resolucao_por_item = {r.get("numero"): r for r in resolucao.get("itens") or []}
    numeros = [item.get("numero") for item in payload.get("itens", [])]

    itens = []
    for posicao, objeto in enumerate(corpo.get("objetos") or []):
        trib = objeto.get("tribCalc") or {}
        numero = int(objeto.get("nObj") or 0)
        seletivo = (trib.get("IS") or {}).get("valor")
        valores = ValoresItem(
            base_calculo=decimal_ou_none(trib.get("vBC")),
            cbs=decimal_ou_none(trib.get("vCBSSN")),
            ibs=decimal_ou_none(trib.get("vIBSSN")),
            imposto_seletivo=decimal_ou_none(seletivo),
            aliquota_cbs=decimal_ou_none(trib.get("pCBSSN")),
            aliquota_ibs=decimal_ou_none(trib.get("pIBSSN")),
        )
        numero_entrada = numeros[posicao] if posicao < len(numeros) else numero
        itens.append(
            ResultadoItem(
                numero=numero,
                estado=EstadoItem.de(objeto.get("estadoItem")),
                valores=valores,
                erros=tuple(erro_de_problem(e) for e in objeto.get("erros") or []),
                memoria=trib.get("memoriaCalculo") or {},
                extras={"resolucao_aliquota": resolucao_por_item.get(numero_entrada) or {}},
            )
        )

    total = corpo.get("total") or {}
    zero = Decimal("0")
    totais = Totais(
        base_calculo=decimal_ou_none(total.get("vBC")) or zero,
        cbs=decimal_ou_none(total.get("vCBSSNtot")) or zero,
        ibs=decimal_ou_none(total.get("vIBSSNtot")) or zero,
        imposto_seletivo=decimal_ou_none(total.get("totalIs")) or zero,
    )
    aviso = cabecalho_simulado(cabecalhos)
    return ResultadoCalculo(
        motor=Motor.SIMPLES,
        versao=versao,
        simulado=bool(aviso) or any(i.estado == EstadoItem.CALCULO_SIMULADO for i in itens),
        aviso_simulado=aviso,
        itens=tuple(itens),
        totais=totais,
        payload_enviado=payload,
        resposta_bruta=corpo,
        extras={
            "regime_apuracao": (corpo.get("oper") or {}).get("regimeApuracao", ""),
            "avisos_aliquotas": resolucao.get("avisos") or [],
        },
        duracao_ms=duracao_ms,
    )


class SimplesClient(ClienteMotor):
    motor = Motor.SIMPLES

    def calcular(self, op: OperacaoSimples, contexto=None) -> ResultadoCalculo:
        """Cálculo não assistido: as alíquotas efetivas vêm do contador (decisão D3).

        Nunca usa `/observabilidade/simples-nacional`, marcado como obsoleto no próprio OpenAPI.
        """
        payload = payload_operacao(op)
        resposta, duracao = self.requisitar(
            "POST", f"{PREFIXO}/observabilidade/simples-nacional/nao-assistido", contexto=contexto, json=payload
        )
        if resposta.status_code != 200:
            raise self.recusa(resposta)
        versao = versao_dos_cabecalhos(self.motor, resposta.headers)
        return interpretar_resposta(resposta.json(), resposta.headers, payload, versao, duracao)

    def atividades(self, data: date):
        """Atividades (`cAtivSN`) com anexo, fator r e tipos de documento admitidos."""
        resposta, _ = self.requisitar(
            "GET", f"{PREFIXO}/simples-nacional/atividades", params={"data": data.isoformat()}
        )
        if resposta.status_code != 200:
            raise self.recusa(resposta)
        return resposta.json()

    def versao(self) -> VersaoInfo:
        resposta, _ = self.requisitar("GET", "/api/versao")
        if resposta.status_code != 200:
            raise self.recusa(resposta)
        corpo = resposta.json()
        data_db = corpo.get("dataVersaoDb")
        return VersaoInfo(
            motor=self.motor,
            versao_app=texto(corpo.get("versaoApp")),
            versao_db=texto(corpo.get("versaoDb")),
            data_db=date.fromisoformat(data_db) if data_db else None,
            descricao_db=texto(corpo.get("descricaoVersaoDb")),
            ambiente=texto(corpo.get("ambiente")),
        )
