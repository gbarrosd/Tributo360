"""Cliente do motor do regime regular (`api-regime-geral`, calculadora-rtc-regime-regular)."""

import re
import uuid
from datetime import date
from decimal import Decimal

from ..contratos import (
    EstadoItem,
    Motor,
    OperacaoRegular,
    ResultadoCalculo,
    ResultadoItem,
    ResultadoValidacaoXml,
    Totais,
    ValoresItem,
    VersaoInfo,
)
from ..http import ClienteMotor, erro_de_problem
from .comum import cabecalho_simulado, decimal_ou_none, texto, versao_dos_cabecalhos

PREFIXO = "/api/calculadora"
TIPOS_XML = {"nfe", "nfce", "cte", "cte-simplificado", "bpe", "bpe-tm", "nf3e", "nfse"}


def _dec(valor):
    return None if valor is None else str(valor)


def _sem_vazios(dados):
    return {chave: valor for chave, valor in dados.items() if valor not in (None, "")}


def payload_operacao(op: OperacaoRegular):
    """Traduz o contrato interno para o JSON do `observabilidade/regime-geral`."""
    itens = []
    for item in op.itens:
        dados = {
            "numero": item.numero,
            "ncm": item.ncm,
            "nbs": item.nbs,
            "cst": item.cst,
            "cClassTrib": item.c_class_trib,
            "baseCalculo": _dec(item.base_calculo),
            "quantidade": _dec(item.quantidade),
            "unidade": item.unidade,
        }
        if item.imposto_seletivo:
            seletivo = item.imposto_seletivo
            dados["impostoSeletivo"] = _sem_vazios(
                {
                    "cst": seletivo.cst,
                    "cClassTrib": seletivo.c_class_trib,
                    "baseCalculo": _dec(seletivo.base_calculo),
                    "quantidade": _dec(seletivo.quantidade),
                    "unidade": seletivo.unidade,
                    "impostoInformado": _dec(seletivo.imposto_informado),
                }
            )
        if item.aliquotas:
            aliq = item.aliquotas
            nominais = {
                "cbs": _dec(aliq.cbs),
                "ibsEstadual": _dec(aliq.ibs_estadual),
                "ibsMunicipal": _dec(aliq.ibs_municipal),
            }
            seletivo = _sem_vazios({"adValorem": _dec(aliq.is_ad_valorem), "adRem": _dec(aliq.is_ad_rem)})
            if seletivo:
                nominais["impostoSeletivo"] = seletivo
            dados["aliquotasNominais"] = nominais
        itens.append(_sem_vazios(dados))
    return _sem_vazios(
        {
            "id": op.id or uuid.uuid4().hex,  # obrigatório no motor (VAL-001)
            "versao": "1.0.0",  # versão do leiaute de entrada exigida pelo motor (VAL-001 sem ela)
            "dhFatoGerador": op.fato_gerador.isoformat(timespec="seconds"),
            "uf": op.uf,
            "municipio": op.municipio,
            "tpDoc": op.tp_doc,
            "itens": itens,
        }
    )


def _valores(trib):
    ibscbs = (trib or {}).get("IBSCBS") or {}
    grupo = ibscbs.get("gIBSCBS") or {}
    uf = grupo.get("gIBSUF") or {}
    mun = grupo.get("gIBSMun") or {}
    cbs = grupo.get("gCBS") or {}
    seletivo = (trib or {}).get("IS") or {}
    return ValoresItem(
        base_calculo=decimal_ou_none(grupo.get("vBC")),
        cbs=decimal_ou_none(cbs.get("vCBS")),
        ibs=decimal_ou_none(grupo.get("vIBS")),
        ibs_uf=decimal_ou_none(uf.get("vIBSUF")),
        ibs_mun=decimal_ou_none(mun.get("vIBSMun")),
        imposto_seletivo=decimal_ou_none(seletivo.get("vIS")),
        aliquota_cbs=decimal_ou_none(cbs.get("pCBS")),
        aliquota_ibs_uf=decimal_ou_none(uf.get("pIBSUF")),
        aliquota_ibs_mun=decimal_ou_none(mun.get("pIBSMun")),
    )


def _memoria(trib):
    memoria = {}
    ibscbs = (trib or {}).get("IBSCBS") or {}
    if isinstance(ibscbs.get("memoriaCalculo"), dict):
        memoria.update(ibscbs["memoriaCalculo"])
    seletivo = (trib or {}).get("IS") or {}
    if seletivo.get("memoriaCalculo"):
        memoria["is"] = seletivo["memoriaCalculo"]
    return memoria


def _totais(total):
    trib = (total or {}).get("tribCalc") or {}
    ibscbs = trib.get("IBSCBSTot") or {}
    zero = Decimal("0")
    return Totais(
        base_calculo=decimal_ou_none(ibscbs.get("vBCIBSCBS")) or zero,
        cbs=decimal_ou_none((ibscbs.get("gCBS") or {}).get("vCBS")) or zero,
        ibs=decimal_ou_none((ibscbs.get("gIBS") or {}).get("vIBS")) or zero,
        imposto_seletivo=decimal_ou_none((trib.get("ISTot") or {}).get("vIS")) or zero,
    )


def interpretar_resposta(corpo, cabecalhos, payload, versao, duracao_ms=0):
    itens = []
    for objeto in corpo.get("objetos") or []:
        estado = EstadoItem.de(objeto.get("estadoItem"))
        trib = objeto.get("tribCalc") or {}
        itens.append(
            ResultadoItem(
                numero=int(objeto.get("nObj") or 0),
                estado=estado,
                valores=_valores(trib),
                erros=tuple(erro_de_problem(e) for e in objeto.get("erros") or []),
                memoria=_memoria(trib),
            )
        )
    aviso = cabecalho_simulado(cabecalhos)
    return ResultadoCalculo(
        motor=Motor.REGIME_REGULAR,
        versao=versao,
        simulado=bool(aviso) or any(i.estado == EstadoItem.CALCULO_SIMULADO for i in itens),
        aviso_simulado=aviso,
        itens=tuple(itens),
        totais=_totais(corpo.get("total")),
        payload_enviado=payload,
        resposta_bruta=corpo,
        duracao_ms=duracao_ms,
    )


class RegimeRegularClient(ClienteMotor):
    motor = Motor.REGIME_REGULAR

    def calcular(self, op: OperacaoRegular, contexto=None) -> ResultadoCalculo:
        """Calcula pelo endpoint de observabilidade (erro por item, HTTP 200).

        Nunca usa `/regime-geral`, que derruba a operação inteira no primeiro erro.
        """
        payload = payload_operacao(op)
        resposta, duracao = self.requisitar(
            "POST", f"{PREFIXO}/observabilidade/regime-geral", contexto=contexto, json=payload
        )
        if resposta.status_code != 200:
            raise self.recusa(resposta)
        versao = versao_dos_cabecalhos(self.motor, resposta.headers)
        return interpretar_resposta(resposta.json(), resposta.headers, payload, versao, duracao)

    def validar_xml(self, xml: bytes, tipo="nfe", subtipo="nota", contexto=None) -> ResultadoValidacaoXml:
        """Valida um XML contra o XSD embarcado no motor. `subtipo`: "nota" ou "grupo"."""
        if tipo not in TIPOS_XML:
            raise ValueError(f"Tipo de XML não suportado: {tipo}")
        resposta, _ = self.requisitar(
            "POST",
            f"{PREFIXO}/xml/validate",
            contexto=contexto,
            params={"tipo": tipo, "subtipo": subtipo},
            content=xml,
            headers={"Content-Type": "application/xml"},
        )
        if resposta.status_code == 200:
            return ResultadoValidacaoXml(valido=resposta.text.strip() == "true", mensagem=resposta.text.strip())
        if resposta.status_code == 422:
            detalhe = str(self.json_ou_vazio(resposta).get("detail") or resposta.text)
            linha, coluna = _linha_coluna(detalhe)
            return ResultadoValidacaoXml(valido=False, mensagem=detalhe, linha=linha, coluna=coluna)
        raise self.recusa(resposta)

    def versao(self) -> VersaoInfo:
        resposta, _ = self.requisitar("GET", f"{PREFIXO}/dados-abertos/versao")
        if resposta.status_code != 200:
            raise self.recusa(resposta)
        corpo = resposta.json()
        return VersaoInfo(
            motor=self.motor,
            versao_app=texto(corpo.get("versaoApp")),
            versao_db=texto(corpo.get("versaoDb")),
            data_db=_data(corpo.get("dataVersaoDb")),
            descricao_db=texto(corpo.get("descricaoVersaoDb")),
            ambiente=texto(corpo.get("ambiente")),
        )

    # --- Dados abertos (sincronização de tabelas e combos) ---------------------------------------

    def _dados_abertos(self, caminho, **params):
        resposta, _ = self.requisitar("GET", f"{PREFIXO}/dados-abertos/{caminho}", params=params)
        if resposta.status_code == 404:
            return None
        if resposta.status_code != 200:
            raise self.recusa(resposta)
        return resposta.json()

    def situacoes_tributarias(self, data: date):
        return self._dados_abertos("situacoes-tributarias/cbs-ibs", data=data.isoformat()) or []

    def classificacoes_tributarias(self, data: date):
        return self._dados_abertos("classificacoes-tributarias/cbs-ibs", data=data.isoformat()) or []

    def ncm_vigentes(self, data: date):
        return self._dados_abertos("ncm/vigentes", data=data.isoformat()) or []

    def nbs(self, data: date):
        return self._dados_abertos("nbs/lista", data=data.isoformat()) or []

    def aliquota_referencia(self, esfera, data: date, codigo=None):
        """Alíquota de referência cadastrada no motor (só existe para 2026). `None` quando não há."""
        caminhos = {"uniao": ("aliquota-uniao", {}), "uf": ("aliquota-uf", {"codigoUf": codigo})}
        caminhos["municipio"] = ("aliquota-municipio", {"codigoMunicipio": codigo})
        caminho, params = caminhos[esfera]
        corpo = self._dados_abertos(caminho, data=data.isoformat(), **params)
        return None if corpo is None else decimal_ou_none(corpo.get("aliquotaReferencia"))


def _data(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _linha_coluna(mensagem):
    """Extrai "linha N, coluna M" da mensagem de erro do validador."""
    achado = re.search(r"linha (\d+), coluna (\d+)", mensagem)
    if not achado:
        return None, None
    return int(achado.group(1)), int(achado.group(2))
